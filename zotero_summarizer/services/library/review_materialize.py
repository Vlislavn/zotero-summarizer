"""Materialize approved feed-review rows into Zotero."""
from __future__ import annotations

import logging
from typing import Any

from zotero_summarizer.domain import VERDICT_SOURCE_USER, label_tag_for_priority
from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.services.library.review_eligibility import (
    ReviewedFeed, require_authorization, require_review, review_ready,
)
from zotero_summarizer.services.library.review_summary import pick_stored_summary
from zotero_summarizer.storage import feeds as feeds_storage


LOGGER = logging.getLogger(__name__)


def materialize_row(
    row: dict[str, Any],
    *,
    writer: Any,
    used_keys: set[str],
    reason: str = "review_apply",
    collection_name: str = "Inbox",
    label_priority: str | None = None,
    review_proof: ReviewedFeed | None = None,
) -> str:
    """Materialize a row; a caller may reuse its own pre-write review proof."""
    from zotero_summarizer.services.triage.feeds import (
        _feed_payload_from_row, _generate_zotero_key, _matched_collections_from_row,
        _summary_from_row, _tags_from_row,
    )
    from zotero_summarizer.services.zotero import pending as pending_service

    require_authorization(row, review_proof)  # Direct calls and retries check afresh.
    row_id = int(row["id"])
    new_key = feeds_storage.reserve_materialization_key(
        get_settings().triage_db_path, row_id, _generate_zotero_key(used_keys)
    )
    stored = pick_stored_summary(row)
    summary = stored if stored is not None else _summary_from_row(row)
    feed_payload = _feed_payload_from_row(row)
    tags = _tags_from_row(is_black_swan=False, black_swan_tag="")
    if label_priority:
        tags = [*tags, label_tag_for_priority(label_priority)]
        summary.reading_priority = label_priority
    note_html = pending_service.build_triage_note_html(
        title=str(row.get("title") or ""),
        summary=summary,
        is_black_swan=False,
        surprise_score=None,
        run_id=f"{reason}:{row_id}",
    )
    with feeds_storage.open_triage_conn(get_settings().triage_db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = feeds_storage.current_materialization_intent(conn, row, label_priority)
        if existing:
            feeds_storage.link_materialized_sibling(conn, row, existing)
            conn.commit()
            return existing
        writer.apply_feed_materialization(
            new_item_key=new_key,
            feed_payload=feed_payload,
            inbox_collection_name=collection_name,
            matched_collections=_matched_collections_from_row(row),
            tags=tags,
            note_title=f"Triage: {str(row.get('title') or '')[:80]}",
            note_html=note_html,
            provenance_tag=pending_service.SYSTEM_TAG_FEEDS_V3,
        )
        if feeds_storage.record_materialization(
            conn,
            feed_library_id=int(row["feed_library_id"]),
            feed_item_id=int(row["feed_item_id"]),
            materialized_zotero_key=new_key,
            outcome_window_days=7,
        ):
            feeds_storage.update_to_decision(
                conn,
                feed_library_id=int(row["feed_library_id"]),
                feed_item_id=int(row["feed_item_id"]),
                decision=feeds_storage.DECISION_SELECTED,
                decision_reason=f"materialized_via_{reason}",
            )
        conn.commit()
    return new_key


def _approved_candidates(rows: list[dict[str, Any]], db_path) -> tuple[list[tuple[dict[str, Any], str | None]], list[dict[str, Any]]]:
    """Classify the snapshot without touching Zotero for blocked/stale rows."""
    eligible, failed = [], []
    with feeds_storage.open_triage_conn(db_path) as conn:
        for row in rows:
            verdict = feeds_storage.current_feed_verdict(conn, row)
            if verdict and verdict["user_priority"] == "dont_read":
                conn.execute("BEGIN IMMEDIATE")
                latest = feeds_storage.current_feed_verdict(conn, row)
                if latest and latest["user_priority"] == "dont_read":
                    feeds_storage.cancel_pending_materialization(conn, str(row.get("stable_feed_key") or ""))
                    conn.commit()
                    failed.append({"id": row["id"], "code": "superseded",
                                   "error": "A newer rejection cancelled this Add"})
                    continue
                conn.commit()
                verdict = latest
            priority = verdict["user_priority"] if verdict and verdict["source"] == VERDICT_SOURCE_USER else None
            if not review_ready(row):
                failed.append({"id": row["id"], "code": "review_required",
                               "error": "Generate a review before adding this paper to the library."})
            else:
                eligible.append((row, priority))
    return eligible, failed


def apply_all_approved(since_hours: int | None = None) -> dict[str, Any]:
    """Apply the complete approval snapshot; missing Zotero stays pending.

    Unexpected errors propagate; successfully materialized predecessors remain
    selected and are not repeated on retry. This is not a cross-store transaction.
    """
    from zotero_summarizer.integrations.zotero_write import ZoteroWriteError, ZoteroWriter

    with feeds_storage.open_triage_conn(get_settings().triage_db_path) as conn:
        rows = feeds_storage.select_by_decisions(
            conn,
            decisions=[feeds_storage.DECISION_USER_APPROVED],
            since_hours=since_hours,
            limit=None,
        )

    settings_ = get_settings()
    eligible, failed = _approved_candidates(rows, settings_.triage_db_path)
    if not eligible:
        return {"applied": 0, "pending_sync": 0, "zotero_sync_error": None,
                "failed_count": len(failed), "failed": failed}

    try:
        writer = ZoteroWriter(settings_.zotero_data_dir)
    except ZoteroWriteError as exc:  # Missing Zotero is an explicit local-first pending-sync state.
        LOGGER.warning("apply_all_approved: Zotero writer unavailable; rows remain pending sync: %s", exc)
        pending = 0
        for row, _priority in eligible:
            try:
                with feeds_storage.open_triage_conn(settings_.triage_db_path) as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    pending += int(feeds_storage.mark_pending_if_current(conn, row, "apply_waiting_zotero"))
                    conn.commit()
            except feeds_storage.MaterializationSuperseded as conflict:
                failed.append({"id": row["id"], "code": "superseded", "error": str(conflict)})
        return {
            "applied": 0,
            "pending_sync": pending,
            "zotero_sync_error": str(exc),
            "failed_count": len(failed),
            "failed": failed,
        }

    used_keys: set[str] = set()
    applied = 0
    for row, priority in eligible:
        try:
            materialize_row(row, writer=writer, used_keys=used_keys, label_priority=priority,
                            review_proof=require_review(row))
        except feeds_storage.MaterializationSuperseded as conflict:
            failed.append({"id": row["id"], "code": "superseded", "error": str(conflict)})
        else:
            applied += 1

    return {
        "applied": applied,
        "pending_sync": 0,
        "zotero_sync_error": None,
        "failed_count": len(failed),
        "failed": failed,
    }
