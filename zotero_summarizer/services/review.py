"""Phase 1.14 — review-mode service layer.

Operations against ``processed_feed_items`` rows whose ``decision`` is
``awaiting_review``. The web UI calls these via :mod:`api.routes.review`.

Single responsibility: state transitions for review-mode items + golden CSV
append. No HTTP concerns (those live in the route module).
"""
from __future__ import annotations

import csv as _csv
import json as _json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator

from zotero_summarizer.models import SummarizeResponse
from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.services.goldenset import GoldenSample, _PRIORITY_TO_RELEVANCE
from zotero_summarizer.storage import feeds as feeds_storage

LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Connection helper (mirrors feeds.py)
# ---------------------------------------------------------------------------


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    path = get_settings().triage_db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        feeds_storage.init_feeds_schema(conn)
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def list_awaiting(since_hours: int = 720, limit: int = 1000) -> list[dict[str, Any]]:
    """Return every ``awaiting_review`` row enriched with parsed SHAP/aux/summary."""
    return list_by_state(feeds_storage.DECISION_AWAITING_REVIEW, since_hours, limit)


def list_by_state(state: str, since_hours: int = 720, limit: int = 1000) -> list[dict[str, Any]]:
    """Return every row with ``decision == state`` enriched with parsed payload.

    The review UI uses this for both ``awaiting_review`` (the LLM-or-gate-only
    triage queue) and ``gate_rejected`` (items the classifier dropped before
    LLM — exposed so the user can spot-check false negatives and relabel).
    """
    with _conn() as conn:
        rows = feeds_storage.select_by_decisions(
            conn,
            decisions=[state],
            since_hours=since_hours,
            limit=limit,
        )
    return [_decorate_row(r) for r in rows]


def _decorate_row(row: dict[str, Any]) -> dict[str, Any]:
    """Parse the JSON payload column into structured fields for the UI."""
    out = dict(row)
    blob = (row.get("shap_contribs_json") or "").strip()
    payload: dict[str, Any] = {}
    if blob:
        payload = _json.loads(blob)
    out["shap"] = payload.get("shap")
    out["aux_context"] = payload.get("aux_context")
    out["summary"] = payload.get("summary")
    out["audit_pick"] = bool(payload.get("audit_pick"))   # Phase 1.15 (2.3)
    return out


def _fetch_row(conn: sqlite3.Connection, processed_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM processed_feed_items WHERE id = ?",
        (int(processed_id),),
    ).fetchone()
    if row is None:
        raise KeyError(f"processed_feed_items id={processed_id} not found")
    return dict(row)


# ---------------------------------------------------------------------------
# User actions
# ---------------------------------------------------------------------------


def approve(processed_id: int) -> dict[str, Any]:
    """Flip awaiting_review → user_approved.

    Does NOT queue pending_changes — feed items don't exist in the user's
    Zotero library yet, so the library-centric pending-changes pipeline
    (which expects an existing item_key) would fail with "Item not found".
    The actual Zotero write happens later in :func:`apply_all_approved`,
    which calls ``writer.apply_feed_materialization`` (the daemon's
    direct-create path).
    """
    with _conn() as conn:
        row = _fetch_row(conn, processed_id)
        _require_state(row, feeds_storage.DECISION_AWAITING_REVIEW)
        # _unpack_summary is a sanity check — fails fast if the row predates
        # Phase 1.14 and has no stored LLM summary.
        _unpack_summary(row)
        feeds_storage.update_to_decision(
            conn,
            feed_library_id=int(row["feed_library_id"]),
            feed_item_id=int(row["feed_item_id"]),
            decision=feeds_storage.DECISION_USER_APPROVED,
            decision_reason="user_approved_in_review_ui",
        )
        conn.commit()
    return {"processed_id": processed_id, "state": feeds_storage.DECISION_USER_APPROVED}


def reject(processed_id: int, *, write_to_golden: bool = True) -> dict[str, Any]:
    """Flip awaiting_review → user_rejected; optionally append dont_read to golden CSV."""
    return _label_and_terminate(
        processed_id,
        new_state=feeds_storage.DECISION_USER_REJECTED,
        label="dont_read",
        write_to_golden=write_to_golden,
        reason="user_rejected_in_review_ui",
    )


def relabel(processed_id: int, new_priority: str) -> dict[str, Any]:
    """Override priority + append to golden CSV.

    ``new_priority`` must be one of must_read / should_read / could_read /
    dont_read.

    Accepts rows in either ``awaiting_review`` (the LLM/gate-only triage
    queue) or ``gate_rejected`` (items the gate dropped pre-LLM). The latter
    lets the user correct false negatives — they have no stored LLM summary,
    so a minimal SummarizeResponse is synthesised on the fly when needed.

    Outcomes:
      * ``new_priority == "dont_read"``: row moves to ``user_rejected``;
        golden CSV gets a dont_read row (positive confirmation if the
        original was gate_rejected, terminal rejection if awaiting_review).
      * other priorities: row moves to ``user_approved``, pending_changes
        queued for Zotero materialisation, golden CSV gets the new label.
    """
    if new_priority not in _PRIORITY_TO_RELEVANCE:
        raise ValueError(
            f"new_priority must be one of {sorted(_PRIORITY_TO_RELEVANCE)}; got {new_priority!r}"
        )
    if new_priority == "dont_read":
        return _confirm_or_reject_to_dont_read(processed_id)
    # Approve-track relabel: flip state to user_approved, persist the chosen
    # priority into the row's payload so `apply_all_approved` can build the
    # right note, append golden CSV. NO pending_changes queueing — feed items
    # don't exist in Zotero yet; materialization happens in apply_all_approved.
    with _conn() as conn:
        row = _fetch_row(conn, processed_id)
        _require_actionable(row)
        _store_relabel_priority(conn, row, new_priority)
        feeds_storage.update_to_decision(
            conn,
            feed_library_id=int(row["feed_library_id"]),
            feed_item_id=int(row["feed_item_id"]),
            decision=feeds_storage.DECISION_USER_APPROVED,
            decision_reason=f"user_relabel:{new_priority}:from_{row.get('decision')}",
        )
        conn.commit()
    appended = append_to_golden(
        row,
        label=new_priority,
        note=f"relabel via review UI ({new_priority}; from {row.get('decision')})",
    )
    return {
        "processed_id": processed_id,
        "state": feeds_storage.DECISION_USER_APPROVED,
        "golden_csv_row_added": appended,
    }


def _store_relabel_priority(
    conn: sqlite3.Connection,
    row: dict[str, Any],
    new_priority: str,
) -> None:
    """Persist the relabel target back into shap_contribs_json so that
    :func:`apply_all_approved` can synthesise the correct note later.

    For awaiting_review items: overrides ``summary.reading_priority``.
    For gate_rejected items: synthesises a minimal summary if absent.
    """
    summary = _build_summary_for_queue(row, new_priority)
    blob = (row.get("shap_contribs_json") or "").strip()
    payload: dict[str, Any] = _json.loads(blob) if blob else {}
    payload["summary"] = summary.model_dump()
    conn.execute(
        "UPDATE processed_feed_items SET shap_contribs_json = ?, "
        "reading_priority = ?, updated_at = datetime('now') WHERE id = ?",
        (_json.dumps(payload), new_priority, int(row["id"])),
    )


def _confirm_or_reject_to_dont_read(processed_id: int) -> dict[str, Any]:
    """``relabel(dont_read)`` for both awaiting_review and gate_rejected.

    Flips the row to ``user_rejected`` and appends a dont_read row to the
    golden CSV. For gate_rejected items this means "user confirmed the gate"
    — strong training signal that the model was right.
    """
    with _conn() as conn:
        row = _fetch_row(conn, processed_id)
        _require_actionable(row)
        prior_state = row.get("decision")
        feeds_storage.update_to_decision(
            conn,
            feed_library_id=int(row["feed_library_id"]),
            feed_item_id=int(row["feed_item_id"]),
            decision=feeds_storage.DECISION_USER_REJECTED,
            decision_reason=f"user_relabel:dont_read:from_{prior_state}",
        )
        conn.commit()
    appended = append_to_golden(
        row,
        label="dont_read",
        note=f"relabel via review UI (dont_read; from {prior_state})",
    )
    return {"processed_id": processed_id, "golden_csv_row_added": appended}


def confirm_remaining_gate_rejected(since_hours: int = 720) -> dict[str, Any]:
    """Bulk-confirm: append a dont_read row to golden CSV for every
    ``gate_rejected`` item the user hasn't already relabelled.

    Semantics: "no click = confirmation" — the user has implicitly agreed
    that the gate was correct for these items. Idempotent: rows whose
    item_key is already in the golden CSV are skipped (append_to_golden
    detects duplicates).

    Decision in DB stays ``gate_rejected`` — the user didn't act, they just
    confirmed the model's verdict. Subsequent retrain picks up the new
    negative-class rows from the golden CSV.

    Returns ``{"appended", "skipped_duplicate", "skipped_no_feed_id"}``.
    """
    with _conn() as conn:
        rows = feeds_storage.select_by_decisions(
            conn,
            decisions=[feeds_storage.DECISION_GATE_REJECTED],
            since_hours=since_hours,
            limit=10000,
        )
    appended = 0
    skipped_duplicate = 0
    skipped_no_feed_id = 0
    for row in rows:
        if int(row.get("feed_item_id") or 0) <= 0:
            skipped_no_feed_id += 1
            continue
        was_new = append_to_golden(
            row,
            label="dont_read",
            note="implicit_confirm_gate_rejected (no user action in review UI)",
        )
        if was_new:
            appended += 1
        else:
            skipped_duplicate += 1
    return {
        "appended": appended,
        "skipped_duplicate": skipped_duplicate,
        "skipped_no_feed_id": skipped_no_feed_id,
        "total_considered": len(rows),
    }


def apply_all_approved(since_hours: int = 720) -> dict[str, Any]:
    """Materialize every ``user_approved`` row into Zotero.

    Bypasses the pending_changes pipeline (which is designed for existing
    library items) and calls :meth:`ZoteroWriter.apply_feed_materialization`
    — the same daemon-direct path used by ``run_daily_selection``. Per-row
    failures are caught + logged + reported in the response (batch contract:
    one bad row must not block the rest of the user's queue).

    On success: row → ``DECISION_SELECTED`` + ``materialized_zotero_key``
    stamped + 7-day outcome window scheduled.

    Returns ``{"applied", "failed_count", "failed": [{"id", "title", "error"}, ...]}``.
    """
    from zotero_summarizer.integrations.zotero_write import ZoteroWriter
    from zotero_summarizer.services import pending as pending_service
    from zotero_summarizer.services.feeds import (
        _feed_payload_from_row, _generate_zotero_key, _matched_collections_from_row,
        _summary_from_row, _tags_from_row,
    )

    settings_ = get_settings()
    writer = ZoteroWriter(settings_.zotero_data_dir)
    inbox_name = "Inbox"   # matches daemon default

    with _conn() as conn:
        rows = feeds_storage.select_by_decisions(
            conn,
            decisions=[feeds_storage.DECISION_USER_APPROVED],
            since_hours=since_hours,
            limit=5000,
        )

    applied = 0
    failed: list[dict[str, Any]] = []
    used_keys: set[str] = set()
    for row in rows:
        row_id = int(row["id"])
        try:
            new_key = _generate_zotero_key(used_keys)
            stored = _pick_summary_for_apply(row)
            # Stored summary is present for every Phase-1.14 row (LLM or
            # gate-only synth or relabel-synth). _summary_from_row covers
            # pre-1.14 rows that only carry scalar fields.
            summary = stored if stored is not None else _summary_from_row(row)
            feed_payload = _feed_payload_from_row(row)
            tags = _tags_from_row(row, is_black_swan=False, black_swan_tag="")
            note_html = pending_service.build_triage_note_html(
                title=str(row.get("title") or ""),
                summary=summary,
                is_black_swan=False,
                surprise_score=None,
                run_id=f"review_apply:{row_id}",
            )
            writer.apply_feed_materialization(
                new_item_key=new_key,
                feed_payload=feed_payload,
                inbox_collection_name=inbox_name,
                matched_collections=_matched_collections_from_row(row),
                tags=tags,
                note_title=f"Triage: {str(row.get('title') or '')[:80]}",
                note_html=note_html,
                provenance_tag=pending_service.SYSTEM_TAG_FEEDS_V3,
            )
            with _conn() as conn:
                feeds_storage.update_to_decision(
                    conn,
                    feed_library_id=int(row["feed_library_id"]),
                    feed_item_id=int(row["feed_item_id"]),
                    decision=feeds_storage.DECISION_SELECTED,
                    decision_reason="materialized_via_review_ui",
                    planned_zotero_key=new_key,
                )
                feeds_storage.record_materialization(
                    conn,
                    feed_library_id=int(row["feed_library_id"]),
                    feed_item_id=int(row["feed_item_id"]),
                    materialized_zotero_key=new_key,
                    outcome_window_days=7,
                )
                conn.commit()
            applied += 1
        except Exception as exc:
            # Batch-apply contract: log and continue so one Zotero-locked row
            # doesn't strand the rest of the user's approvals. Per-row error
            # is surfaced in the response.
            LOGGER.exception("apply_all_approved failed for row id=%s", row_id)
            failed.append({
                "id": row_id,
                "title": str(row.get("title") or ""),
                "error": str(exc),
            })

    return {
        "applied": applied,
        "failed_count": len(failed),
        "failed": failed[:20],
    }


def _pick_summary_for_apply(row: dict[str, Any]):
    """Return the stored SummarizeResponse (or None) from shap_contribs_json.

    Used by apply_all_approved to prefer the LLM/relabel-synthesised summary
    over the sparse fallback rebuilt from the row's scalar fields.
    """
    blob = (row.get("shap_contribs_json") or "").strip()
    if not blob:
        return None
    payload = _json.loads(blob)
    summary_dict = payload.get("summary")
    if summary_dict is None:
        return None
    return SummarizeResponse.model_validate(summary_dict)


def _label_and_terminate(
    processed_id: int,
    *,
    new_state: str,
    label: str,
    write_to_golden: bool,
    reason: str,
) -> dict[str, Any]:
    """Reject/terminal-relabel path: flip state, optionally update golden CSV."""
    with _conn() as conn:
        row = _fetch_row(conn, processed_id)
        _require_state(row, feeds_storage.DECISION_AWAITING_REVIEW)
        feeds_storage.update_to_decision(
            conn,
            feed_library_id=int(row["feed_library_id"]),
            feed_item_id=int(row["feed_item_id"]),
            decision=new_state,
            decision_reason=reason,
        )
        conn.commit()
    appended = False
    if write_to_golden:
        appended = append_to_golden(row, label=label, note=f"{reason} via review UI")
    return {"processed_id": processed_id, "golden_csv_row_added": appended}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_ACTIONABLE_STATES = frozenset({
    feeds_storage.DECISION_AWAITING_REVIEW,
    feeds_storage.DECISION_GATE_REJECTED,
})


def _require_state(row: dict[str, Any], expected: str) -> None:
    if row.get("decision") != expected:
        raise ValueError(
            f"row id={row.get('id')} is in state {row.get('decision')!r}, "
            f"expected {expected!r}"
        )


def _require_actionable(row: dict[str, Any]) -> None:
    """Allow review-UI mutations on awaiting_review OR gate_rejected rows."""
    if row.get("decision") not in _ACTIONABLE_STATES:
        raise ValueError(
            f"row id={row.get('id')} is in state {row.get('decision')!r}, "
            f"expected one of {sorted(_ACTIONABLE_STATES)}"
        )


# Integer relevance scores for the SummarizeResponse synthesised at relabel
# time. Matches `SummarizeResponse.relevance_score: int = Field(..., ge=1, le=5)`.
_RELEVANCE_INT = {"must_read": 5, "should_read": 4, "could_read": 3, "dont_read": 1}


def _unpack_summary(row: dict[str, Any]) -> SummarizeResponse:
    """Parse the LLM ``SummarizeResponse`` saved alongside the awaiting row.

    Used by :func:`approve`, which only operates on ``awaiting_review`` rows
    (those always have a stored summary). For gate_rejected items use
    :func:`_build_summary_for_queue` instead — it synthesises on the fly.
    """
    blob = (row.get("shap_contribs_json") or "").strip()
    if not blob:
        raise ValueError(
            f"row id={row.get('id')} has no summary payload; cannot approve"
        )
    payload = _json.loads(blob)
    summary_dict = payload.get("summary")
    if summary_dict is None:
        raise ValueError(
            f"row id={row.get('id')} has shap/aux but no LLM summary"
        )
    return SummarizeResponse.model_validate(summary_dict)


def _build_summary_for_queue(row: dict[str, Any], new_priority: str) -> SummarizeResponse:
    """Return a SummarizeResponse for the pending-changes queue.

    Prefers the LLM summary stored in ``shap_contribs_json`` (awaiting_review
    rows always carry one). Falls back to a minimal synthesis using the row's
    composite_score + corpus_affinity when the row is gate_rejected — those
    never had an LLM call so no summary was stored. The synthesised summary
    is what the Zotero triage note will display; it makes clear that the
    classification came from the user via the review UI, not from the LLM.
    """
    blob = (row.get("shap_contribs_json") or "").strip()
    payload = _json.loads(blob) if blob else {}
    summary_dict = payload.get("summary")
    if summary_dict is not None:
        summary = SummarizeResponse.model_validate(summary_dict)
        summary.reading_priority = new_priority
        return summary
    # gate_rejected → no LLM ever ran. Synthesise minimal.
    # composite_relevance_score / corpus_affinity_score / prestige_score all
    # have sensible defaults on SummarizeResponse — omit rather than reach
    # for `or 0.0`-style masking on nullable SQL columns.
    return SummarizeResponse(
        executive_summary=(
            "(promoted from gate-rejected via review UI — no LLM rationale; "
            "see SHAP attribution for the original gate decision)"
        ),
        relevance_score=_RELEVANCE_INT[new_priority],
        reading_priority=new_priority,
        triage_rationale=(
            f"User relabelled gate-rejected item to {new_priority!r} "
            "via Feed Review UI."
        ),
        triage_confidence=0.5,
        suggested_collections=[],
        tags=["zs:user-promoted-from-gate-reject"],
    )


def _fetch_feed_metadata(*, feed_library_id: int, feed_item_id: int) -> dict[str, str]:
    """Read the live abstract/authors/venue/year from Zotero's feedItems table.

    Returns ``{}`` when the feed item is gone (user manually deleted it from
    Zotero between triage and review). That's the only "absence" we tolerate;
    every other failure (Zotero DB unreadable, schema mismatch) propagates.
    """
    from zotero_summarizer.integrations.zotero_read import ZoteroReader

    if feed_library_id <= 0 or feed_item_id <= 0:
        return {}
    reader = ZoteroReader(get_settings().zotero_data_dir)
    items = reader.get_feed_items(feed_library_id=feed_library_id, limit=5000)
    match = next((i for i in items if int(i.get("item_id") or 0) == feed_item_id), None)
    if match is None:
        LOGGER.info(
            "feed item gone from Zotero (feed=%d, item=%d); appending row without abstract",
            feed_library_id, feed_item_id,
        )
        return {}
    pub_date = str(match.get("publication_date") or "")
    return {
        "abstract": str(match.get("abstract") or ""),
        "authors": str(match.get("authors") or ""),
        "publication_title": str(match.get("publication_title") or ""),
        "venue": str(match.get("publication_title") or ""),
        "year": pub_date[:4] if pub_date[:4].isdigit() else "",
    }


def append_to_golden(
    row: dict[str, Any],
    *,
    label: str,
    note: str,
    golden_csv_path: Path | None = None,
) -> bool:
    """Append one row to ``zotero-summarizer-golden.csv``.

    Writes a :class:`GoldenSample`-shaped row so the CSV stays schema-
    compatible with the golden-set training pipeline. The sha256 of the CSV
    changes after this call, which the next ``feeds run`` start (or per-tick
    check in ``feeds serve``) will detect and trigger a background retrain.
    Returns False if the row is already present (idempotent on duplicate
    item_key).
    """
    if label not in _PRIORITY_TO_RELEVANCE:
        raise ValueError(f"unknown label {label!r}")
    settings_ = get_settings()
    csv_path = golden_csv_path or (settings_.project_root / "zotero-summarizer-golden.csv")
    if not csv_path.exists():
        raise FileNotFoundError(
            f"golden CSV not found at {csv_path}; run `goldenset export` first"
        )

    feed_item_id = int(row.get("feed_item_id") or 0)
    new_key = f"feed:{feed_item_id}" if feed_item_id else f"processed:{row.get('id')}"
    # Resolve abstract + authors + venue from the live Zotero feedItems table.
    # `summary.abstract_preview` is 200-char truncated and gate-only synth rows
    # don't have it at all, leaving training rows useless. The feed item is
    # cheap to look up here and gives us the full abstract + author list.
    feed_meta = _fetch_feed_metadata(
        feed_library_id=int(row.get("feed_library_id") or 0),
        feed_item_id=feed_item_id,
    )
    abstract = feed_meta.get("abstract", "")
    authors = feed_meta.get("authors", "")
    venue = feed_meta.get("publication_title", "") or feed_meta.get("venue", "")
    year = feed_meta.get("year", "")

    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = _csv.DictReader(f)
        existing = list(reader)
        # Trust the header row even when the CSV has no data rows yet.
        existing_fields = list(reader.fieldnames or [])
    if not existing_fields:
        raise ValueError(
            f"golden CSV at {csv_path} has no header — cannot append a row"
        )
    if any((r.get("item_key") or "") == new_key for r in existing):
        LOGGER.info("golden CSV already contains %s; skipping append", new_key)
        return False

    sample = GoldenSample(
        item_key=new_key,
        title=str(row.get("title") or ""),
        authors=authors,
        year=year,
        venue=venue,
        doi=str(row.get("doi") or ""),
        url="",
        abstract=abstract,
        matched_emojis="",
        # Sprint-1+ wiring fix (May 2026): conscious UI relabel must NOT be
        # tier=first_glance — that tier is the goldenset audit marker for
        # the *automated* preview during feed ingestion, and the Sprint-1
        # training filter drops it as noise. A relabel is a deliberate
        # user verdict (positive or negative) on a specific item, so it
        # gets `feed_user_label` which `domain.is_training_eligible`
        # explicitly accepts.
        gold_signal_tier="feed_user_label",
        note_count=0,
        annotation_count=0,
        collection_count=0,
        collections="",
        in_trash=False,
        days_since_added=-1,
        gold_priority_inferred=label,
        gold_signal_strength="high",
        gold_inferred_relevance=_PRIORITY_TO_RELEVANCE[label],
        gold_priority_final=label,
        gold_notes=note,
        our_composite_score="",
        our_prestige_score="",
        our_priority="",
        our_corpus_affinity="",
    )
    new_row: dict[str, str] = {
        k: (v if isinstance(v, str) else str(v))
        for k, v in asdict(sample).items()
    }
    for col in existing_fields:
        new_row.setdefault(col, "")

    tmp = csv_path.with_suffix(csv_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = _csv.DictWriter(f, fieldnames=existing_fields)
        writer.writeheader()
        for r in existing:
            writer.writerow(r)
        writer.writerow({k: new_row.get(k, "") for k in existing_fields})
    tmp.replace(csv_path)
    return True
