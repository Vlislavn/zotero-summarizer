"""feeds: materialize a daily-selected pick into Zotero.

Daily selection happens hours after the triage tick that scored an item, so the
full :class:`SummarizeResponse` is long gone from memory. These helpers
reconstruct the note/payload/tags from the persisted ``processed_feed_items``
row (re-querying the original Zotero feed item for fresh metadata) and write the
item — Inbox + matched collections + tags + v3 note — flipping its DB decision.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.models import SummarizeResponse
from zotero_summarizer.services.zotero import pending as pending_service
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.services.triage.feeds._common import (
    LOGGER,
    _generate_zotero_key,
    _infer_item_type,
    _triage_conn,
    get_settings,
)


@dataclass
class _PendingScoredRow:
    """Lightweight scored row used by plateau_select (compatible interface)."""

    composite_score: float
    surprise_score: float
    is_black_swan: bool
    row: dict[str, Any]
    key: str
    # Optional full-text-refined summary (Part 1.8 two-stage triage). When set,
    # the materialization loop uses this in place of `_summary_from_row(...)`.
    refined_summary: SummarizeResponse | None = None


@dataclass(frozen=True)
class _MaterializeCtx:
    """Run-scoped config + base reason threaded into per-pick materialization."""

    inbox_collection_name: str
    black_swan_tag: str
    outcome_window_days: int
    decision_reason: str


def _summary_from_row(row: dict[str, Any]) -> SummarizeResponse:
    """Reconstruct a minimal SummarizeResponse from a processed_feed_items row.

    The row only stores the score + a few fields; we rebuild a sparse
    SummarizeResponse so the note builder has something to render.
    """
    matched = json.loads(row.get("matched_collections_json") or "[]")
    return SummarizeResponse(
        title=str(row.get("title") or ""),
        doi=str(row.get("doi") or ""),
        summary="",
        relevance_score=int(round(float(row.get("composite_score") or 0))),
        composite_relevance_score=float(row.get("composite_score") or 0.0),
        reading_priority=str(row.get("reading_priority") or "could_read"),
        tags=[],
        triage_rationale="",
        triage_confidence=0.0,
        executive_summary="",
        should_deep_read="",
        key_sections_to_read=[],
        relevance_to_research="",
        controversial_points="",
        industry_academy_impact="",
        unknown_unknowns="",
        implementation_quickstart="",
        key_findings=[],
        methods="",
        limitations="",
        suggested_collections=list(matched),
        corpus_affinity_score=float(row.get("corpus_affinity") or 0.0),
        matched_goal="",
    )


def _feed_payload_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Build the create_item_from_feed payload from a stored row.

    The original Zotero feed item still exists in Zotero's ``feedItems`` table —
    we re-query it here for fresh metadata rather than storing the full abstract
    in ``processed_feed_items``.
    """
    feed_library_id = int(row.get("feed_library_id") or 0)
    feed_item_id = int(row.get("feed_item_id") or 0)
    reader = ZoteroReader(get_settings().zotero_data_dir)
    items = reader.get_feed_items(feed_library_id=feed_library_id, limit=5000)
    match = next((i for i in items if int(i.get("item_id") or 0) == feed_item_id), None)
    if not match:
        # Item disappeared from Zotero (manually deleted from the feed?).
        # Materialize with what's in our DB instead.
        return {
            "title": str(row.get("title") or "Untitled"),
            "abstract": "",
            "url": "",
            "doi": str(row.get("doi") or ""),
            "publication_date": "",
            "publication_title": "",
            "item_type": "journalArticle",
        }
    return {
        "title": match.get("title") or row.get("title") or "Untitled",
        "abstract": match.get("abstract") or "",
        "url": match.get("url") or "",
        "doi": match.get("doi") or row.get("doi") or "",
        "publication_date": match.get("publication_date") or "",
        "publication_title": match.get("publication_title") or "",
        "authors": match.get("authors") or "",
        "item_type": _infer_item_type(match),
    }


def _matched_collections_from_row(row: dict[str, Any]) -> list[str]:
    """Decode the stored matched-collections JSON (corrupt JSON ⇒ no matches)."""
    try:
        return json.loads(row.get("matched_collections_json") or "[]")
    except json.JSONDecodeError:
        return []


def _tags_from_row(
    *,
    is_black_swan: bool,
    black_swan_tag: str,
) -> list[str]:
    """Build the tag list for a materialized item.

    The machine no longer stamps a ``zs:<priority>`` tag — the human
    ``label:<priority>`` is the only priority namespace now (retired 2026-06), and
    the daemon must not set the user's ground-truth label. Adds just the
    black-swan tag when applicable; the provenance tag ``/zs/feeds-v3`` is
    appended separately by ``apply_feed_materialization``.
    """
    tags: list[str] = []
    if is_black_swan and black_swan_tag:
        tags.append(black_swan_tag)
    return tags


def materialize_pick(
    pick: _PendingScoredRow,
    *,
    writer: ZoteroWriter,
    run_id: str,
    used_keys: set[str],
    ctx: _MaterializeCtx,
) -> dict[str, Any] | None:
    """Materialize one selected pick into Zotero + flip its DB decision.

    Returns ``None`` on success, or an ``{key, error}`` dict when the write
    failed. A DB-lock / ``triaged_pending`` error defers the item to the next
    run (warning only); any other error is logged with a full traceback. This
    per-item boundary keeps one bad row from aborting the whole daily run.
    """
    LOGGER.info(
        "[%s] → inbox: %r  composite=%.2f%s",
        run_id, str(pick.row.get("title") or "")[:60], pick.composite_score,
        "  [black-swan]" if pick.is_black_swan else "",
    )
    try:
        new_key = _generate_zotero_key(used_keys)
        pick.row["planned_zotero_key"] = new_key
        summary = pick.refined_summary or _summary_from_row(pick.row)
        feed_payload = _feed_payload_from_row(pick.row)
        matched = _matched_collections_from_row(pick.row)
        tags = _tags_from_row(is_black_swan=pick.is_black_swan, black_swan_tag=ctx.black_swan_tag)
        note_html = pending_service.build_triage_note_html(
            title=str(pick.row.get("title") or ""),
            summary=summary,
            is_black_swan=pick.is_black_swan,
            surprise_score=pick.surprise_score if pick.is_black_swan else None,
            run_id=run_id,
        )
        writer.apply_feed_materialization(
            new_item_key=new_key,
            feed_payload=feed_payload,
            inbox_collection_name=ctx.inbox_collection_name,
            matched_collections=matched,
            tags=tags,
            note_title=f"Triage: {str(pick.row.get('title') or '')[:80]}",
            note_html=note_html,
            provenance_tag=pending_service.SYSTEM_TAG_FEEDS_V3,
            create_backup=False,
        )
        decision = (
            feeds_storage.DECISION_BLACK_SWAN
            if pick.is_black_swan
            else feeds_storage.DECISION_SELECTED
        )
        with _triage_conn() as conn:
            feeds_storage.update_to_decision(
                conn,
                feed_library_id=int(pick.row.get("feed_library_id") or 0),
                feed_item_id=int(pick.row.get("feed_item_id") or 0),
                decision=decision,
                decision_reason=ctx.decision_reason if not pick.is_black_swan else "surprise_pick",
                is_black_swan=pick.is_black_swan,
                planned_zotero_key=new_key,
            )
            feeds_storage.record_materialization(
                conn,
                feed_library_id=int(pick.row.get("feed_library_id") or 0),
                feed_item_id=int(pick.row.get("feed_item_id") or 0),
                materialized_zotero_key=new_key,
                outcome_window_days=ctx.outcome_window_days,
            )
            conn.commit()
        LOGGER.info(
            "[%s] materialized: %r  key=%s",
            run_id, str(pick.row.get("title") or "")[:60], new_key,
        )
        return None
    except Exception as exc:
        _exc_str = str(exc)
        if "triaged_pending" in _exc_str or "database is locked" in _exc_str.lower():
            LOGGER.warning(
                "[%s] materialization deferred for key %s (DB locked — item queued for next selection run): %s",
                run_id, pick.key, exc,
            )
        else:
            LOGGER.exception("[%s] materialization failed for key %s", run_id, pick.key)
        return {"key": pick.key, "error": _exc_str}
