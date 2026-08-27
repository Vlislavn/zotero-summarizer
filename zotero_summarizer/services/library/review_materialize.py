"""Materialize approved feed-review rows into Zotero."""
from __future__ import annotations

import logging
from typing import Any

from zotero_summarizer.domain import label_tag_for_priority
from zotero_summarizer.services._common import settings as get_settings
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
) -> str:
    """Materialize one feed row into Zotero and return the new item key."""
    from zotero_summarizer.services.triage.feeds import (
        _feed_payload_from_row, _generate_zotero_key, _matched_collections_from_row,
        _summary_from_row, _tags_from_row,
    )
    from zotero_summarizer.services.zotero import pending as pending_service

    row_id = int(row["id"])
    new_key = _generate_zotero_key(used_keys)
    stored = pick_stored_summary(row)
    summary = stored if stored is not None else _summary_from_row(row)
    feed_payload = _feed_payload_from_row(row)
    tags = _tags_from_row(is_black_swan=False, black_swan_tag="")
    if label_priority:
        tags = [*tags, label_tag_for_priority(label_priority)]
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
        feeds_storage.update_to_decision(
            conn,
            feed_library_id=int(row["feed_library_id"]),
            feed_item_id=int(row["feed_item_id"]),
            decision=feeds_storage.DECISION_SELECTED,
            decision_reason=f"materialized_via_{reason}",
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
    return new_key


def apply_all_approved(since_hours: int | None = None) -> dict[str, Any]:
    """Materialize every approved row; keep unavailable-Zotero rows pending."""
    from zotero_summarizer.integrations.zotero_write import ZoteroWriter

    with feeds_storage.open_triage_conn(get_settings().triage_db_path) as conn:
        rows = feeds_storage.select_by_decisions(
            conn,
            decisions=[feeds_storage.DECISION_USER_APPROVED],
            since_hours=since_hours,
            limit=5000,
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
    except Exception as exc:  # noqa: BLE001 - Zotero is optional for approved feed rows.
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

    applied = 0
    failed: list[dict[str, Any]] = []
    used_keys: set[str] = set()
    for row in rows:
        row_id = int(row["id"])
        try:
            materialize_row(row, writer=writer, used_keys=used_keys, reason="review_apply")
            applied += 1
        except Exception as exc:
            LOGGER.exception("apply_all_approved failed for row id=%s", row_id)
            failed.append({
                "id": row_id,
                "title": str(row.get("title") or ""),
                "error": str(exc),
            })

    return {
        "applied": applied,
        "pending_sync": 0,
        "zotero_sync_error": None,
        "failed_count": len(failed),
        "failed": failed[:20],
    }
