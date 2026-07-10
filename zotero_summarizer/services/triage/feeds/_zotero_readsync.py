"""feeds: reconcile app-side read state back into Zotero's unread badge.

Since the app-RSS source migration every triaged item is ``source_type=app_rss``
and its read mark lands only in the app's own ``rss_items.read_at`` — Zotero's
``feedItems.readTime`` (the thing the bold unread badge reads) was never updated
again. Zotero keeps polling the same upstream feeds itself, so both sides store
the same RSS entry id as ``guid``: this module sweeps Zotero's unread feed items
each tick and marks read every one whose guid the app has already read.

Zotero stays an optional adapter — no reader/writer, no sync, next tick retries.
The sweep also covers user actions (e.g. Today "Trash") without extra wiring:
those set ``rss_items.read_at`` app-side and the next tick reconciles them.
"""
from __future__ import annotations

from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriteError, ZoteroWriter
from zotero_summarizer.services.triage.feeds._common import LOGGER, _triage_conn


def sync_zotero_read_state(
    *,
    zotero_reader: ZoteroReader | None,
    writer: ZoteroWriter | None,
    tick_id: str,
    batch_limit: int = 500,
) -> int:
    """Mark Zotero-unread feed items read when the app already read them (by guid).

    Returns the number of ``feedItems`` rows marked. A Zotero write failure
    (e.g. DB locked while Zotero is open) is logged and reported as 0 — the
    same rows are still unread next tick, so the sweep self-heals; this
    lock-tolerant posture mirrors ``mark_processed_read``.
    """
    if zotero_reader is None or writer is None:
        return 0
    unread = zotero_reader.get_unread_feed_guid_map()
    if not unread:
        return 0
    with _triage_conn() as conn:
        rows = conn.execute(
            "SELECT guid FROM rss_items WHERE read_at IS NOT NULL AND guid IS NOT NULL AND guid != ''"
        ).fetchall()
    app_read_guids = {str(row["guid"]) for row in rows}
    matched = [item_id for guid, item_id in unread.items() if guid in app_read_guids]
    if not matched:
        return 0
    # ponytail: guid-only match (measured 309/471 immediate hits on live data);
    # add DOI/arXiv fallback only if a measured gap shows up.
    batch = matched[: max(1, int(batch_limit))]
    try:
        marked = writer.mark_feed_items_read(batch)
    except ZoteroWriteError as exc:
        LOGGER.warning("[%s] zotero read-sync failed (retries next tick): %s", tick_id, exc)
        return 0
    LOGGER.info(
        "[%s] zotero read-sync: marked %d/%d matched (zotero unread=%d)",
        tick_id, marked, len(matched), len(unread),
    )
    return marked


__all__ = ["sync_zotero_read_state"]
