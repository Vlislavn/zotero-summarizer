"""Compose the ``/api/golden/review-detail`` payload uniformly across
feed, note, and library rows.

The golden CSV mixes three kinds of ``item_key``:

* ``feed:<feed_item_id>``  — produced by ``review.append_to_golden`` when
  the user approves a triaged feed item. Lookup goes through
  ``processed_feed_items`` + Zotero ``feedItems``.
* ``note:<parent_zotero_key>:<note_id>`` — produced by
  ``note_analyzer.classify_notes`` for user-written Zotero notes. Lookup
  goes through the parent Zotero library item + ``itemNotes``.
* 8-char alphanumeric — a Zotero library key. Lookup goes through
  ``ZoteroReader.get_item_detail``.

The legacy implementation in ``api/routes/golden.py`` always took the
library path, which 404'd on 37% of rows (every feed/note row). This
module dispatches per prefix and returns a *uniform shape* so the React
UI branches on data (``source == "feed" | "note" | "library"``), not on
key syntax.

Single responsibility: payload assembly only. SQL helpers live in
``storage/feeds.py`` and ``integrations/zotero_read.py``; scoring lives in
``services/daily_select/_candidate.py`` (we reuse ``parse_payload`` /
``shap_top3``). Author h-index lookup uses the existing OpenAlex cache.
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Any

from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.services.triage.daily_select import _candidate
from zotero_summarizer.services.library.review import _fetch_feed_metadata
from zotero_summarizer.storage import feeds as feeds_storage


SOURCE_FEED = "feed"
SOURCE_NOTE = "note"
SOURCE_LIBRARY = "library"
# Phase 1.18 Step 3: when the live source store no longer contains the
# row (e.g., user deleted the Zotero item, processed_feed_items rotated
# the feed:* row), we fall back to a stub built from the golden CSV
# columns. The user can still see + label the paper.
SOURCE_CSV_STUB = "csv_stub"

_SHAP_WATERFALL_LIMIT = 6  # the waterfall chart caps at 6 bars for readability


# ---------------------------------------------------------------------------
# Key parsing
# ---------------------------------------------------------------------------


class InvalidItemKey(ValueError):
    """Raised when a golden CSV item_key doesn't match any known prefix."""


def classify_item_key(item_key: str) -> str:
    """Return the source category for an item_key. Raises on empty input.

    Pure function — no I/O. The 8-char-alphanumeric heuristic for library
    keys mirrors Zotero's own convention (uppercase letters + digits).
    Anything that isn't ``feed:<int>`` or ``note:<key>:<int>`` is treated
    as a library key; bad-shape keys 404 downstream when the library
    reader returns ``None``.
    """
    if not item_key:
        raise InvalidItemKey("item_key must not be empty")
    if item_key.startswith("feed:"):
        return SOURCE_FEED
    if item_key.startswith("note:"):
        return SOURCE_NOTE
    return SOURCE_LIBRARY


def parse_feed_key(item_key: str) -> int:
    """``feed:<id>`` -> int. Raises ``InvalidItemKey`` on malformed input.

    Non-numeric ids (e.g. ``feed:abc``) raise ``InvalidItemKey`` — not a
    bare ``ValueError`` — so the route layer can map every malformed-key
    case to a single 422 with one ``except InvalidItemKey``.
    """
    suffix = item_key[len("feed:"):]
    if not suffix:
        raise InvalidItemKey(f"feed key has no id: {item_key!r}")
    try:
        return int(suffix)
    except ValueError as exc:
        raise InvalidItemKey(f"feed id must be an integer: {item_key!r}") from exc


def parse_note_key(item_key: str) -> tuple[str, int]:
    """``note:<parent>:<note_id>`` -> (parent_key, note_id). Raises
    ``InvalidItemKey`` on bad shape or a non-numeric note id."""
    parts = item_key.split(":")
    if len(parts) != 3 or parts[0] != "note":
        raise InvalidItemKey(
            f"note key must be 'note:<parent>:<note_id>'; got {item_key!r}"
        )
    parent = parts[1].strip()
    if not parent:
        raise InvalidItemKey(f"note key has empty parent: {item_key!r}")
    try:
        note_id = int(parts[2])
    except ValueError as exc:
        raise InvalidItemKey(f"note id must be an integer: {item_key!r}") from exc
    return parent, note_id


# ---------------------------------------------------------------------------
# Scoring extraction (feed branch)
# ---------------------------------------------------------------------------


def build_scoring(row: dict[str, Any]) -> dict[str, Any] | None:
    """Project the SHAP / prestige / composite signals out of one
    ``processed_feed_items`` row into the shape the React UI consumes.

    Returns ``None`` only when the row has no ``shap_contribs_json``
    payload at all — older Phase-1 rows pre-date the SHAP capture and
    can't show a waterfall. That isn't error masking; the column is
    optional by design.
    """
    payload = _candidate.parse_payload(row)
    shap_list = payload.get("shap")
    summary = payload.get("summary") or {}
    aux = payload.get("aux_context") or {}

    if not shap_list and not summary and not aux:
        return None

    waterfall: list[dict[str, Any]] = []
    if isinstance(shap_list, list):
        ranked = sorted(
            shap_list,
            key=lambda c: abs(float(c.get("contribution", 0.0) or 0.0)),
            reverse=True,
        )
        for item in ranked[:_SHAP_WATERFALL_LIMIT]:
            waterfall.append({
                "feature": str(item.get("feature") or ""),
                "value": float(item.get("contribution") or 0.0),
            })

    composite = row.get("composite_score")
    prestige_inputs: dict[str, Any] = {}
    if isinstance(aux, dict):
        # citation_percentile is THE prestige signal (field+year-normalized);
        # h-index/venue/cites are kept for context only.
        for key in ("citation_percentile", "max_author_h_index", "venue_works_count", "cited_by_count"):
            if key in aux and aux[key] is not None:
                prestige_inputs[key] = aux[key]

    return {
        "composite_score": float(composite) if composite is not None else None,
        "prestige_score": float(summary.get("prestige_score")) if summary.get("prestige_score") is not None else None,
        "shap_top": waterfall,
        "prestige_inputs": prestige_inputs,
    }


# ---------------------------------------------------------------------------
# Author shape normalization
# ---------------------------------------------------------------------------


def _split_author_string(text: str) -> list[str]:
    """Split a multi-author string on ``;`` or ``,``.

    Zotero feed metadata emits authors as a single string with mixed
    delimiters across publishers — some use ``"A; B; C"``, others
    ``"A, B, C"``. We prefer ``;`` when present (less ambiguous given
    that "Last, First" notation uses commas inside one name).
    """
    if ";" in text:
        return [part.strip() for part in text.split(";") if part.strip()]
    return [part.strip() for part in text.split(",") if part.strip()]


def _author_name_from_entry(entry: Any) -> str:
    """Pull a display name out of a single author entry. Returns ``""``
    when the entry is unusable (caller filters)."""
    if isinstance(entry, str):
        return entry.strip()
    if not isinstance(entry, dict):
        return ""
    name = entry.get("name")
    if name:
        return str(name).strip()
    first = entry.get("first_name") or entry.get("firstName") or ""
    last = entry.get("last_name") or entry.get("lastName") or ""
    return f"{first} {last}".strip()


def normalize_authors(raw: Any, *, top_author_h: int | None = None) -> list[dict[str, Any]]:
    """Coerce the various author shapes into a uniform list shape.

    Output: ``[{"name": str, "h_index": int|None}, ...]``.

    Input forms handled:
      * list of strings or dicts (mixed allowed)
      * single comma-separated string (from ``_fetch_feed_metadata``)
      * empty / falsy -> ``[]``

    Only the FIRST author carries ``h_index`` (set to ``top_author_h`` if
    provided). The OpenAlex cache only stores ``max_author_h_index``
    across the top-3 authors, not per-author — per-author h-indices
    would need new OpenAlex calls on the request path, explicitly
    excluded by the plan.
    """
    if not raw:
        return []

    if isinstance(raw, str):
        names = _split_author_string(raw)
    elif isinstance(raw, list):
        names = []
        for entry in raw:
            name = _author_name_from_entry(entry)
            if not name:
                continue
            # A list element may itself be a multi-author string (feed
            # metadata emits ``"Smith J; Lee P"`` as a single element).
            names.extend(_split_author_string(name))
    else:
        return []

    parsed = [{"name": name, "h_index": None} for name in names]
    if parsed and top_author_h is not None:
        parsed[0]["h_index"] = int(top_author_h)
    return parsed


# ---------------------------------------------------------------------------
# Per-source builders
# ---------------------------------------------------------------------------


def build_feed_detail(
    triage_db_path: Path,
    zotero_data_dir: Path,
    feed_item_id: int,
) -> dict[str, Any] | None:
    """Assemble the feed-source review-detail payload.

    Returns ``None`` when the feed row has been hard-deleted from the
    triage DB (rare; user may have ``rm``'d ``triage_history.db``). The
    route layer translates ``None`` into a 404 — distinguishing "row gone"
    from a hard error matches the route's existing contract for missing
    library items.
    """
    conn = sqlite3.connect(str(triage_db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = feeds_storage.get_processed_feed_item_by_id(conn, feed_item_id)
    finally:
        conn.close()
    if row is None:
        return None

    feed_lib_id = int(row.get("feed_library_id") or 0)
    feed_meta = _fetch_feed_metadata(
        feed_library_id=feed_lib_id,
        feed_item_id=feed_item_id,
    )
    scoring = build_scoring(row)

    aux = (_candidate.parse_payload(row).get("aux_context") or {})
    top_author_h = aux.get("max_author_h_index")
    top_author_h_int = int(top_author_h) if top_author_h is not None else None
    summary = _candidate.parse_payload(row).get("summary") or {}
    summary_authors = summary.get("authors") if isinstance(summary, dict) else None

    authors_raw = feed_meta.get("authors") or summary_authors or ""
    authors = normalize_authors(authors_raw, top_author_h=top_author_h_int)

    return {
        "source": SOURCE_FEED,
        "title": str(row.get("title") or ""),
        "authors": authors,
        "venue": feed_meta.get("publication_title", "") or feed_meta.get("venue", ""),
        "year": feed_meta.get("year", ""),
        "doi": str(row.get("doi") or ""),
        "url": "",
        "abstract": feed_meta.get("abstract", "") or "",
        "has_pdf": False,
        "pdf_path": None,
        "tags": [],
        "collections": [],
        "annotations": [],
        "notes": [],
        "date_added": "",
        "scoring": scoring,
        "deep_review": None,
    }


def _pick_note_by_id(notes: list[dict[str, Any]], note_id: int) -> dict[str, Any] | None:
    """Return the note from ``notes`` whose ``note_key`` mentions
    ``note_id`` (the legacy golden CSV encoding), else the newest note.

    Empty input -> ``None``. ``notes`` is already ordered newest-first
    by ``get_item_notes`` (date_modified DESC).
    """
    if not notes:
        return None
    target = str(note_id)
    for entry in notes:
        key_text = str(entry.get("note_key", ""))
        if target in key_text:
            return entry
    return notes[0]


def build_note_detail(
    reader: ZoteroReader,
    parent_key: str,
    note_id: int,
) -> dict[str, Any] | None:
    """Assemble the note-source review-detail payload.

    The parent Zotero item carries the bibliographic metadata; we pick
    the specific note matching ``note_id`` out of ``itemNotes`` and stash
    it in ``notes`` so the React UI can show the user's own writing
    inline. ``note_id`` is the Zotero integer note key, which may not
    match Zotero's note ``key`` column directly — older note rows in the
    golden CSV used a numeric form; we resolve by date-modified ordering
    so the freshest note for the parent wins when the id doesn't line up.
    """
    parent_detail = reader.get_item_detail(parent_key)
    if parent_detail is None:
        return None

    all_notes = reader.get_item_notes(parent_key)
    selected_note = _pick_note_by_id(all_notes, note_id)
    notes_field = [selected_note] if selected_note else []

    return {
        "source": SOURCE_NOTE,
        "title": str(parent_detail.get("title", "")),
        "authors": normalize_authors(parent_detail.get("authors")),
        "venue": str(parent_detail.get("publication_title", "")),
        "year": str(parent_detail.get("publication_date", ""))[:4],
        "doi": str(parent_detail.get("doi", "")),
        "url": str(parent_detail.get("url", "")),
        "abstract": str(parent_detail.get("abstract", "")),
        "has_pdf": bool(parent_detail.get("has_pdf", False)),
        "pdf_path": parent_detail.get("pdf_path"),
        "tags": list(parent_detail.get("tags") or []),
        "collections": list(parent_detail.get("collections") or []),
        "annotations": list(parent_detail.get("annotations") or []),
        "notes": notes_field,
        "date_added": str(parent_detail.get("date_added", "")),
        "scoring": None,
        "deep_review": None,
    }


def build_library_detail(
    reader: ZoteroReader,
    item_key: str,
) -> dict[str, Any] | None:
    """Assemble the library-source review-detail payload.

    ``scoring`` is filled by the gate so the "Why this score?" waterfall shows
    for library items too: reuse the exact score the Read-next queue cached
    (so they agree), else score the item live on open. Gate off / no abstract
    → ``None`` and the UI shows the "no reasoning" placeholder.
    """
    detail = reader.get_item_detail(item_key)
    if detail is None:
        return None
    from zotero_summarizer.services.library import deep_review, reading_queue
    scoring = reading_queue.get_cached_scoring(item_key)
    if scoring is None:
        scoring = reading_queue.live_scoring({
            "item_key": item_key,
            "title": str(detail.get("title", "")),
            "abstract": str(detail.get("abstract", "")),
            "authors": str(detail.get("authors") or ""),
            "doi": str(detail.get("doi", "")),
            "publication_date": str(detail.get("publication_date", "")),
            "venue": str(detail.get("publication_title", "")),
        })
    return {
        "source": SOURCE_LIBRARY,
        "title": str(detail.get("title", "")),
        "authors": normalize_authors(detail.get("authors")),
        "venue": str(detail.get("publication_title", "")),
        "year": str(detail.get("publication_date", ""))[:4],
        "doi": str(detail.get("doi", "")),
        "url": str(detail.get("url", "")),
        "abstract": str(detail.get("abstract", "")),
        "has_pdf": bool(detail.get("has_pdf", False)),
        "pdf_path": detail.get("pdf_path"),
        "tags": list(detail.get("tags") or []),
        "collections": list(detail.get("collections") or []),
        "annotations": list(detail.get("annotations") or []),
        "notes": list(detail.get("notes") or []),
        "date_added": str(detail.get("date_added", "")),
        "scoring": scoring,
        "deep_review": deep_review.get_cached_review(item_key),
    }


def build_csv_stub_detail(csv_row: dict[str, Any]) -> dict[str, Any]:
    """Fallback payload built from the golden CSV row alone.

    Phase 1.18 Step 3: the live source store (Zotero library or
    ``processed_feed_items``) may not have the row anymore — the user
    deleted the Zotero item, or the feeds DB was rotated. Rather than
    returning a 404 (which makes the paper unlabellable), we surface
    whatever the CSV remembers so the user can still cast a verdict.

    All bibliographic columns in the CSV are honored. Annotations,
    notes, tags, and the SHAP waterfall stay empty/null — they do not
    exist in CSV form. ``source == "csv_stub"`` tells the React UI to
    render a small "live source missing" badge so the user knows the
    detail is partial.
    """
    return {
        "source": SOURCE_CSV_STUB,
        "title": str(csv_row.get("title") or ""),
        "authors": normalize_authors(csv_row.get("authors") or ""),
        "venue": str(csv_row.get("venue") or ""),
        "year": str(csv_row.get("year") or ""),
        "doi": str(csv_row.get("doi") or ""),
        "url": str(csv_row.get("url") or ""),
        "abstract": str(csv_row.get("abstract") or ""),
        "has_pdf": False,
        "pdf_path": None,
        "tags": [],
        "collections": [],
        "annotations": [],
        "notes": [],
        "date_added": "",
        "scoring": None,
        "deep_review": None,
    }


def load_csv_row(csv_path: Path, item_key: str) -> dict[str, Any] | None:
    """Find one row by item_key in the golden CSV. ``None`` when absent."""
    if not csv_path.exists():
        return None
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("item_key") or "").strip() == item_key:
                return row
    return None
