"""Summary reconstruction + golden-CSV append helpers for feed review.

Split out of review.py; the public append helpers are re-exported there."""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from zotero_summarizer.models import SummarizeResponse
from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.services.golden.goldenset import GoldenSample, _PRIORITY_TO_RELEVANCE
from zotero_summarizer.services.golden.csv_store import edit_csv
from zotero_summarizer.services.triage.daily_select._candidate import parse_payload
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.storage.feed_identity import row_feed_keys

LOGGER = logging.getLogger(__name__)

_RELEVANCE_INT = {"must_read": 5, "should_read": 4, "could_read": 3, "dont_read": 1}

# Trash taxonomy — the SAME "never show again" key the Today slate and the ingest
# dedup use (``daily_select`` / ``_tick_dedup``). A paper the user threw away
# (``user_rejected`` from Today, or ``trashed`` / ``deleted_all`` inside Zotero)
# re-arrives under a fresh feed_item_id, slipping past identity dedup.
_TRASH_DECISIONS = (feeds_storage.DECISION_USER_REJECTED,)
_TRASH_OUTCOMES = (feeds_storage.OUTCOME_TRASHED, feeds_storage.OUTCOME_DELETED_ALL)


def _drop_trashed_rearrivals(
    conn: sqlite3.Connection, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Drop rows whose stable GUID matches a paper the user explicitly trashed.

    The review-listing counterpart to ``daily_select._drop_trashed_guids``: the
    Today *slate* already suppresses trashed re-arrivals, but the ``gate_rejected``
    spot-check (and the Review page) listed straight from the table, so a paper
    the user trashed kept resurfacing under a new feed_item_id. Matching on the
    GUID — the one id that survives re-ingestion — makes the never-show-again
    guard uniform across every Today surface. Re-exported by ``review``.
    """
    trashed_guids = feeds_storage.fetch_trashed_guids(
        conn, decisions=_TRASH_DECISIONS, outcomes=_TRASH_OUTCOMES,
    )
    if not trashed_guids:
        return rows
    return [r for r in rows if str(r.get("guid") or "").strip() not in trashed_guids]


def _parse_stored_summary(
    row: dict[str, Any], *, required: bool
) -> SummarizeResponse | None:
    """Parse the LLM ``SummarizeResponse`` stored in ``shap_contribs_json``.

    ``required=True`` raises when the row has no stored summary (the
    :func:`_unpack_summary` sanity-check policy); ``required=False`` returns
    ``None`` instead (the :func:`pick_stored_summary` fallback policy).
    """
    blob = (row.get("shap_contribs_json") or "").strip()
    if not blob:
        if required:
            raise ValueError(
                f"row id={row.get('id')} has no summary payload; cannot approve"
            )
        return None
    payload = parse_payload(row)
    version = int(payload.get("summary_schema_version", 0))
    if version not in {0, 1}:
        raise ValueError(f"unsupported stored summary schema version: {version}")
    summary_dict = payload.get("summary")
    if summary_dict is None:
        if required:
            raise ValueError(
                f"row id={row.get('id')} has shap/aux but no LLM summary"
            )
        return None
    return SummarizeResponse.model_validate(summary_dict)


def _unpack_summary(row: dict[str, Any]) -> SummarizeResponse:
    """Parse the LLM ``SummarizeResponse`` saved alongside the awaiting row.

    Used by :func:`approve`, which only operates on ``awaiting_review`` rows
    (those always have a stored summary). For gate_rejected items use
    :func:`_build_summary_for_queue` instead — it synthesises on the fly.
    """
    return _parse_stored_summary(row, required=True)


def pick_stored_summary(row: dict[str, Any]) -> SummarizeResponse | None:
    """Return the stored ``SummarizeResponse`` (or ``None``) from ``shap_contribs_json``.

    Used by ``apply_all_approved`` to prefer the LLM/relabel-synthesised
    summary over the sparse fallback rebuilt from the row's scalar fields.
    """
    return _parse_stored_summary(row, required=False)


def _build_summary_for_queue(row: dict[str, Any], new_priority: str) -> SummarizeResponse:
    """Return a SummarizeResponse for the pending-changes queue.

    Prefers the LLM summary stored in ``shap_contribs_json`` (awaiting_review
    rows always carry one). Falls back to a minimal synthesis using the row's
    composite_score + corpus_affinity when the row is gate_rejected — those
    never had an LLM call so no summary was stored. The synthesised summary
    is what the Zotero triage note will display; it makes clear that the
    classification came from the user via the review UI, not from the LLM.
    """
    payload = parse_payload(row)
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


def _fetch_app_rss_metadata(*, rss_feed_id: int, rss_item_id: int) -> dict[str, str]:
    settings_ = get_settings()
    conn = sqlite3.connect(str(settings_.triage_db_path))
    conn.row_factory = sqlite3.Row
    try:
        feeds_storage.init_feeds_schema(conn)
        row = conn.execute(
            """
            SELECT abstract, authors, publication_title, publication_date, url
            FROM rss_items
            WHERE id = ? AND rss_feed_id = ?
            """,
            (int(rss_item_id), int(rss_feed_id)),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {}
    pub_date = str(row["publication_date"] or "")
    return {
        "abstract": str(row["abstract"] or ""),
        "authors": str(row["authors"] or ""),
        "publication_title": str(row["publication_title"] or ""),
        "venue": str(row["publication_title"] or ""),
        "year": pub_date[:4] if pub_date[:4].isdigit() else "",
        "url": str(row["url"] or ""),
    }


def _fetch_feed_metadata(
    *,
    feed_library_id: int,
    feed_item_id: int,
    source_type: str = "zotero",
) -> dict[str, str]:
    """Read full feed metadata from app RSS storage or Zotero's feedItems table.

    Returns ``{}`` when the feed item is gone (user manually deleted it from
    the source between triage and review). That's the only "absence" we tolerate;
    every other failure (Zotero DB unreadable, schema mismatch) propagates.
    """
    from zotero_summarizer.integrations.zotero_read import ZoteroReader

    if feed_library_id <= 0 or feed_item_id <= 0:
        return {}
    if source_type == "app_rss":
        return _fetch_app_rss_metadata(
            rss_feed_id=feed_library_id,
            rss_item_id=feed_item_id,
        )
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


def feed_meta_from_rss_item(rss_meta: dict[str, Any]) -> dict[str, str]:
    """Feed display metadata from an app-owned ``rss_items`` row (keyed by stable_feed_key)
    — the Zotero-INDEPENDENT counterpart of :func:`_fetch_feed_metadata` (which reads
    Zotero's feedItems table and raises when Zotero is absent). Same shape so callers are
    interchangeable. ``{}`` in → all-empty out."""
    pub_date = str(rss_meta.get("publication_date") or "")
    return {
        "abstract": str(rss_meta.get("abstract") or ""),
        "authors": str(rss_meta.get("authors") or ""),
        "publication_title": str(rss_meta.get("publication_title") or ""),
        "venue": str(rss_meta.get("publication_title") or rss_meta.get("feed_name") or ""),
        "year": pub_date[:4] if pub_date[:4].isdigit() else "",
        "url": str(rss_meta.get("url") or rss_meta.get("canonical_url") or ""),
    }


def _write_golden_sample(sample: GoldenSample, csv_path: Path) -> bool:
    """Append one :class:`GoldenSample` to the golden CSV, preserving the
    existing header/columns. Idempotent on ``item_key`` (returns False when the
    key is already present). Shared by the feed-row and verdict appenders."""
    with edit_csv(csv_path) as (fields, rows):
        if any(row["item_key"] == sample.item_key for row in rows):
            return False
        new_row = asdict(sample)
        rows.append({key: new_row.get(key, "") for key in fields})
    return True


def append_verdict_to_golden(
    item_key: str,
    *,
    title: str,
    abstract: str,
    priority: str,
    authors: str = "",
    venue: str = "",
    year: str = "",
    doi: str = "",
    comment: str = "",
    signal_tier: str = "feed_user_label",
    golden_csv_path: Path | None = None,
) -> bool:
    """Append a golden row for a user verdict on ANY ``item_key`` (feed / note /
    Zotero), so the verdict is a first-class training example even when the item
    has no derived golden row (e.g. a paper added from Today then marked
    ``dont_read`` — its materialized library key is unengaged, so the engagement-
    only export never produced a row to overlay). Idempotent: skips when the key
    already has a row (the hybrid overlay covers existing rows)."""
    if priority not in _PRIORITY_TO_RELEVANCE:
        raise ValueError(f"unknown priority {priority!r}")
    csv_path = golden_csv_path or get_settings().golden_csv_path
    sample = GoldenSample(
        item_key=item_key, title=title, authors=authors, year=year, venue=venue,
        doi=doi, url="", abstract=abstract, matched_emojis="",
        gold_signal_tier=signal_tier, note_count=0, annotation_count=0,
        collection_count=0, collections="", in_trash=False, days_since_added=-1,
        gold_priority_inferred=priority, gold_signal_strength="high",
        gold_inferred_relevance=_PRIORITY_TO_RELEVANCE[priority],
        gold_priority_final=priority, gold_notes=comment,
        our_composite_score="", our_prestige_score="", our_priority="", our_corpus_affinity="",
    )
    return _write_golden_sample(sample, csv_path)


def prepare_training_sample(
    row: dict[str, Any],
    *,
    label: str,
    note: str,
    signal_tier: str = "feed_user_label",
) -> GoldenSample:
    """Resolve full metadata without publishing a label or training example."""
    if label not in _PRIORITY_TO_RELEVANCE:
        raise ValueError(f"unknown label {label!r}")
    feed_item_id = int(row.get("feed_item_id") or 0)
    new_key = row_feed_keys(row)[0]
    # Resolve abstract + authors + venue from the live Zotero feedItems table.
    # `summary.abstract_preview` is 200-char truncated and gate-only synth rows
    # don't have it at all, leaving training rows useless. The feed item is
    # cheap to look up here and gives us the full abstract + author list.
    feed_meta = _fetch_feed_metadata(
        feed_library_id=int(row.get("feed_library_id") or 0),
        feed_item_id=feed_item_id,
        source_type=str(row.get("source_type") or "zotero"),
    )
    abstract = feed_meta.get("abstract", "")
    authors = feed_meta.get("authors", "")
    venue = feed_meta.get("publication_title", "") or feed_meta.get("venue", "")
    year = feed_meta.get("year", "")

    return GoldenSample(
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
        # defaults to `feed_user_label` which `domain.is_training_eligible`
        # explicitly accepts. The caller may pass a softer tier (e.g.
        # `feed_interest` for a pre-read "Add to library" click) to lower
        # the training weight (see services.label_weights).
        gold_signal_tier=signal_tier,
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


def append_to_golden(
    row: dict[str, Any], *, label: str, note: str,
    signal_tier: str = "feed_user_label", golden_csv_path: Path | None = None,
) -> bool:
    """Append a metadata-rich CSV sample for the standalone/Today callers."""
    csv_path = golden_csv_path or get_settings().golden_csv_path
    if not csv_path.exists():
        raise FileNotFoundError(f"golden CSV not found at {csv_path}; run `goldenset export` first")
    sample = prepare_training_sample(row, label=label, note=note, signal_tier=signal_tier)
    return _write_golden_sample(sample, csv_path)
