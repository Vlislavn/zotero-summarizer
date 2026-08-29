"""Feed-read bookkeeping methods of ZoteroWriter."""

from __future__ import annotations

import sqlite3
from typing import Any

from zotero_summarizer.integrations._zotero_write_common import (
    LOGGER,
    ZoteroWriteError,
)


class ZoteroFeedWriteMixin:
    def _apply_mark_feed_item_read(
        self,
        conn: sqlite3.Connection,
        item_key: str,
        payload: dict[str, Any],
    ) -> None:
        """Mark one Zotero feed item read from its internal numeric ID."""
        _ = item_key
        feed_library_id = int(payload.get("feed_library_id") or 0)
        feed_item_id = int(payload.get("feed_item_id") or 0)
        if feed_library_id <= 0 or feed_item_id <= 0:
            raise ZoteroWriteError(
                "mark_feed_item_read payload requires feed_library_id + feed_item_id"
            )
        conn.execute(
            "UPDATE feedItems SET readTime = datetime('now') WHERE itemID = ?",
            (feed_item_id,),
        )

    def mark_feed_items_read(self, feed_item_ids: list[int]) -> int:
        """Idempotently clear Zotero's unread badge for a batch of feed items."""
        if not feed_item_ids:
            return 0
        if self.is_connector_running():
            LOGGER.info("Zotero is running — mark_feed_items_read will retry on lock")

        def _do() -> int:
            conn = sqlite3.connect(str(self.db_path), timeout=15)
            conn.row_factory = sqlite3.Row
            try:
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                except sqlite3.Error:
                    pass
                conn.execute("PRAGMA busy_timeout=15000")
                placeholders = ",".join("?" for _ in feed_item_ids)
                cursor = conn.execute(
                    f"UPDATE feedItems SET readTime = datetime('now') "
                    f"WHERE itemID IN ({placeholders}) AND readTime IS NULL",
                    tuple(int(item_id) for item_id in feed_item_ids),
                )
                conn.commit()
                return int(cursor.rowcount or 0)
            except sqlite3.Error:
                conn.rollback()
                raise
            finally:
                conn.close()

        try:
            return self._retry_on_lock(_do, ctx="mark_feed_items_read")
        except sqlite3.OperationalError as exc:
            raise ZoteroWriteError(
                "DB still locked after retries — items will remain unread in Zotero "
                f"until next tick: {exc}"
            ) from exc
        except sqlite3.Error as exc:
            raise ZoteroWriteError(f"Failed to mark feed items read: {exc}") from exc


__all__ = ["ZoteroFeedWriteMixin"]
