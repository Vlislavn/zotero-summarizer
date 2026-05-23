"""Stage-1 (Today) keep/trash actions for the two-stage reading flow.

``add_to_library`` materializes selected Today cards into the Zotero "Inbox"
collection AND records a positive training label. ``trash`` records a strong
negative training label and marks the feed items read. Both are batch
(multi-select), idempotent, and report per-row failures rather than aborting
the whole batch (the same batch contract as
``services.review.apply_all_approved``).

The fine must/should/could/don't priority is NOT chosen here — the user makes
a coarse keep/trash call before reading. Stage-2 annotation refines it later
(manual-wins, already shipped).
"""
from __future__ import annotations

import sqlite3
from typing import Any

from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.services.library import review
from zotero_summarizer.services._common import LOGGER
from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.storage import repositories

# Provisional positive label for "add to library": the user signalled the
# paper is worth reading, but hasn't read it yet. Stage-2 annotation overrides
# this (the verdict overlay makes the manual label win on retrain).
_ADD_PRIORITY = "should_read"


def _db_path():
    return get_settings().triage_db_path


def _load_rows(item_ids: list[int]) -> list[dict[str, Any]]:
    """Fetch processed_feed_items rows for the given PKs (missing PKs skipped)."""
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    try:
        rows: list[dict[str, Any]] = []
        for pk in item_ids:
            row = feeds_storage.get_processed_feed_item_by_pk(conn, int(pk))
            if row is not None:
                rows.append(dict(row))
        return rows
    finally:
        conn.close()


def _golden_key(row: dict[str, Any]) -> str:
    fid = int(row.get("feed_item_id") or 0)
    return f"feed:{fid}" if fid else f"processed:{row.get('id')}"


def _record_label(
    row: dict[str, Any], priority: str, note: str, *, signal_tier: str = "feed_user_label",
) -> None:
    """Write the training label two ways: golden CSV (for retrain) + the
    label_verdicts overlay (so it persists, wins, and excludes the card from
    the slate via the shipped handled-paper filter).

    ``signal_tier`` sets the golden row's training weight tier — `feed_interest`
    for the soft pre-read "Add to library" signal, `feed_user_label` (default)
    for a confident decision like trash."""
    review.append_to_golden(row, label=priority, note=note, signal_tier=signal_tier)
    repositories.insert_or_update_label_verdict(
        _db_path(),
        item_key=_golden_key(row),
        original_derived_priority=(row.get("reading_priority") or "").strip() or "unknown",
        user_priority=priority,
        comment=note,
    )


def _set_decision(row: dict[str, Any], decision: str, reason: str) -> None:
    conn = sqlite3.connect(str(_db_path()))
    try:
        feeds_storage.update_to_decision(
            conn,
            feed_library_id=int(row["feed_library_id"]),
            feed_item_id=int(row["feed_item_id"]),
            decision=decision,
            decision_reason=reason,
        )
        conn.commit()
    finally:
        conn.close()


def add_to_library(item_ids: list[int]) -> dict[str, Any]:
    """Materialize each selected card into the Zotero Inbox + record a positive
    training label. Returns ``{added, failed_count, failed}``."""
    rows = _load_rows(item_ids)
    writer = ZoteroWriter(get_settings().zotero_data_dir)
    used_keys: set[str] = set()
    added = 0
    failed: list[dict[str, Any]] = []
    for row in rows:
        try:
            review.materialize_row(row, writer=writer, used_keys=used_keys, reason="today_add")
            # Soft, low-weight training signal: "Add" is pre-read interest, not
            # endorsement — feed_interest → WEIGHT_INTEREST (0.3). A later read +
            # label (or Zotero engagement) on the materialized library item
            # carries full weight and dominates this.
            _record_label(row, _ADD_PRIORITY, "added from Today", signal_tier="feed_interest")
            added += 1
        except Exception as exc:
            # Batch contract: a Zotero-locked / bad row must not strand the
            # rest of the user's selection. Surface per-row failures instead.
            LOGGER.exception("add_to_library failed for row id=%s", row.get("id"))
            failed.append({
                "id": row.get("id"),
                "title": str(row.get("title") or ""),
                "error": str(exc),
            })
    return {"added": added, "failed_count": len(failed), "failed": failed[:20]}


def trash(item_ids: list[int]) -> dict[str, Any]:
    """Record a strong negative (dont_read) training label for each selected
    card, flip it to user_rejected, and mark the feed items read. Returns
    ``{trashed, marked_read, failed_count, failed}``."""
    rows = _load_rows(item_ids)
    writer = ZoteroWriter(get_settings().zotero_data_dir)
    trashed = 0
    failed: list[dict[str, Any]] = []
    read_ids: list[int] = []
    for row in rows:
        try:
            _record_label(row, "dont_read", "trashed from Today")
            _set_decision(row, feeds_storage.DECISION_USER_REJECTED, "trashed_from_today")
            fid = int(row.get("feed_item_id") or 0)
            if fid:
                read_ids.append(fid)
            trashed += 1
        except Exception as exc:
            # Batch contract: per-row failure is reported, not fatal.
            LOGGER.exception("trash failed for row id=%s", row.get("id"))
            failed.append({
                "id": row.get("id"),
                "title": str(row.get("title") or ""),
                "error": str(exc),
            })
    marked = writer.mark_feed_items_read(read_ids) if read_ids else 0
    return {
        "trashed": trashed,
        "marked_read": marked,
        "failed_count": len(failed),
        "failed": failed[:20],
    }
