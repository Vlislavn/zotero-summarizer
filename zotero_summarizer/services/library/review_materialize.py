"""Materialize approved feed-review rows into Zotero."""
from __future__ import annotations

import logging
from typing import Any

from zotero_summarizer.domain import VERDICT_SOURCE_USER, label_tag_for_priority
from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.services.library.review_summary import pick_stored_summary
from zotero_summarizer.storage import feeds as feeds_storage, repositories
from zotero_summarizer.storage.feed_identity import row_feed_keys


LOGGER = logging.getLogger(__name__)


def materialize_row(
    row: dict[str, Any],
    *,
    writer: Any,
    used_keys: set[str],
    reason: str = "review_apply",
    collection_name: str = "Inbox",
    label_priority: str | None = None,
) -> str:
    """Materialize one feed row into Zotero and return the new item key."""
    from zotero_summarizer.services.triage.feeds import (
        _feed_payload_from_row, _generate_zotero_key, _matched_collections_from_row,
        _summary_from_row, _tags_from_row,
    )
    from zotero_summarizer.services.zotero import pending as pending_service

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
    with feeds_storage.open_triage_conn(get_settings().triage_db_path) as conn:
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

    if not rows:
        return {
            "applied": 0,
            "pending_sync": 0,
            "zotero_sync_error": None,
            "failed_count": 0,
            "failed": [],
        }

    settings_ = get_settings()
    try:
        writer = ZoteroWriter(settings_.zotero_data_dir)
    except ZoteroWriteError as exc:  # Missing Zotero is an explicit local-first pending-sync state.
        LOGGER.warning("apply_all_approved: Zotero writer unavailable; rows remain pending sync: %s", exc)
        with feeds_storage.open_triage_conn(settings_.triage_db_path) as conn:
            for row in rows:
                feeds_storage.record_zotero_sync_status(
                    conn,
                    feed_library_id=int(row["feed_library_id"]),
                    feed_item_id=int(row["feed_item_id"]),
                    status="pending",
                )
                feeds_storage.record_app_outcome(
                    conn,
                    feed_library_id=int(row["feed_library_id"]),
                    feed_item_id=int(row["feed_item_id"]),
                    final_outcome=feeds_storage.OUTCOME_KEPT_UNREAD_APP,
                    signal_weight=feeds_storage.OUTCOME_WEIGHT[feeds_storage.OUTCOME_KEPT_UNREAD_APP],
                )
            conn.commit()
        return {
            "applied": 0,
            "pending_sync": len(rows),
            "zotero_sync_error": str(exc),
            "failed_count": 0,
            "failed": [],
        }

    # ponytail: O(N) snapshot of approvals/verdicts; stream batches if library size requires it.
    verdicts = {verdict["item_key"]: verdict
                for verdict in repositories.list_all_label_verdicts(settings_.triage_db_path)}
    used_keys: set[str] = set()
    for row in rows:
        verdict = next((verdicts[key] for key in row_feed_keys(row) if key in verdicts), None)
        priority = verdict["user_priority"] if verdict is not None and verdict["source"] == VERDICT_SOURCE_USER else None
        if priority == "dont_read":
            raise ValueError(f"Approved row {row['id']} now has a dont_read verdict")
        materialize_row(row, writer=writer, used_keys=used_keys, label_priority=priority)

    return {
        "applied": len(rows),
        "pending_sync": 0,
        "zotero_sync_error": None,
        "failed_count": 0,
        "failed": [],
    }
