"""Read current facts for retained Today members, without selecting replacements."""
from __future__ import annotations

from contextlib import closing
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services._common import connect_sqlite_ro, settings
from zotero_summarizer.services.library._review_cache import usable_reviews_for_keys
from zotero_summarizer.services.triage.daily_select._candidate import _row_item_key
from zotero_summarizer.services.triage.daily_select._recommendation import recommendation, review_entry
from zotero_summarizer.services.triage.daily_select._review_status import _join_review, _quality_from_row
from zotero_summarizer.storage.feeds_history import current_feed_verdict
from zotero_summarizer.storage.feed_identity import row_feed_keys
from zotero_summarizer.storage.feeds_lookup import (
    get_processed_feed_item_by_pk, has_pending_materialization, materialized_key_for,
)
from zotero_summarizer.storage.review_note_projection import snapshot as note_snapshot


def _member(conn, row: dict[str, Any]) -> dict[str, Any]:
    verdict = current_feed_verdict(conn, row) or {}
    quality = _quality_from_row(row)
    target = materialized_key_for(conn, row)
    note = note_snapshot(conn, row_feed_keys(row)[0])
    sync = row.get("zotero_sync_status")
    if target:
        sync = "synced"
    elif verdict.get("user_priority") == "dont_read":
        sync = "cancelled"
    elif has_pending_materialization(conn, row):
        sync = "pending"
    return {
        "item_id": row["id"], "available": True,
        "item_key": _row_item_key(row),
        "stable_feed_key": str(row.get("stable_feed_key") or ""),
        "decision": row.get("decision"),
        "user_priority": verdict.get("user_priority"), "verdict_source": verdict.get("source"),
        "materialized_zotero_key": target,
        "zotero_sync_status": sync,
        "note_delivery": {key: note[key] for key in ("revision", "target_key", "pending")},
        "source_type": row.get("source_type"),
        "read_time_marked_at": row.get("read_time_marked_at"),
        "final_outcome": row.get("final_outcome"),
        "review_ready": False, "quality": quality,
        "selection_evidence": {"review": {"quality_origin": "legacy_row" if quality else "none"}},
    }


def read_members(item_ids: list[int]) -> dict[str, Any]:
    """One result per distinct requested PK, including missing/handled/old rows."""
    if not item_ids or len(item_ids) > 20 or any(
        type(pk) is not int or not 0 < pk < 2**63 for pk in item_ids
    ):
        raise APIError("validation_error", "Supply 1–20 positive SQLite item IDs", 422)
    members = []
    with closing(connect_sqlite_ro(settings().triage_db_path)) as conn:
        conn.execute("BEGIN")
        for pk in dict.fromkeys(item_ids):
            row = get_processed_feed_item_by_pk(conn, pk)
            members.append(_member(conn, row) if row is not None else {
                "item_id": pk, "available": False, "reason": "not_found",
            })
    keys = [member.get(field, "") for member in members
            for field in ("stable_feed_key", "materialized_zotero_key")]
    # Same usable-review seam as the slate: an outdated-but-valid review keeps the
    # card recommended (badged), so the member status never contradicts the slate
    # with a 'withdrawn' recommendation or a disabled must/should/could.
    reviews = usable_reviews_for_keys(keys)
    for member in members:
        if member["available"]:
            entry = review_entry(member, reviews)
            _join_review(member, reviews)
            member["recommendation"] = recommendation(entry)
            member["review_freshness"] = (entry or {}).get("review_freshness")
            member["review_evidence"] = member.pop("selection_evidence")["review"]
    return {"members": members}
