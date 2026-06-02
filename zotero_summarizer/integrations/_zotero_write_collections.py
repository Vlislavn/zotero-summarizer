"""Collection add/remove methods of ZoteroWriter (mixin)."""
from __future__ import annotations

import sqlite3
from typing import Any

from zotero_summarizer.integrations._zotero_write_common import (  # noqa: F401
    ZoteroWriteError,
    generate_unique_key,
)


class ZoteroCollectionMixin:
    def _ensure_collection(
        self,
        conn: sqlite3.Connection,
        collection_name: str,
        collection_columns: set[str],
    ) -> int:
        """Find-or-create a top-level user collection by name.

        Called when materialization needs the "Inbox" collection and it
        doesn't exist yet — auto-creating it preserves the user's flow
        (first daemon tick on a fresh library creates Inbox once).
        """
        existing = conn.execute(
            "SELECT collectionID FROM collections WHERE lower(collectionName)=lower(?) AND parentCollectionID IS NULL LIMIT 1",
            (collection_name,),
        ).fetchone()
        if existing:
            return int(existing["collectionID"])

        user_library_row = conn.execute(
            "SELECT libraryID FROM libraries WHERE type='user' LIMIT 1"
        ).fetchone()
        if not user_library_row:
            raise ZoteroWriteError("Cannot auto-create collection: no user library")
        user_library_id = int(user_library_row["libraryID"])

        new_key = self._generate_unique_collection_key(conn)
        now = self._sqlite_timestamp_now()
        insert_values: dict[str, Any] = {
            "collectionName": collection_name,
            "libraryID": user_library_id,
            "key": new_key,
        }
        if "version" in collection_columns:
            insert_values["version"] = 1
        if "synced" in collection_columns:
            insert_values["synced"] = 0
        if "clientDateModified" in collection_columns:
            insert_values["clientDateModified"] = now
        columns_sql = ", ".join(insert_values.keys())
        placeholders = ", ".join("?" for _ in insert_values)
        cursor = conn.execute(
            f"INSERT INTO collections ({columns_sql}) VALUES ({placeholders})",
            tuple(insert_values.values()),
        )
        return int(cursor.lastrowid)

    def _generate_unique_collection_key(self, conn: sqlite3.Connection) -> str:
        return generate_unique_key(conn, "collections", self._KEY_ALPHABET, "collection")

    def _apply_collection_change(
        self,
        conn: sqlite3.Connection,
        item_key: str,
        payload: dict[str, Any],
        item_columns: set[str],
        collection_columns: set[str],
        collection_item_columns: set[str],
    ) -> None:
        row = conn.execute(
            "SELECT itemID FROM items WHERE key = ? LIMIT 1",
            (item_key,),
        ).fetchone()
        if not row:
            raise ZoteroWriteError(f"Item not found: {item_key}")

        item_id = int(row["itemID"])
        if not {"itemID", "collectionID"}.issubset(collection_item_columns):
            raise ZoteroWriteError("Unsupported Zotero schema: required collectionItems columns missing")

        collection_key = str(payload.get("collection_key") or "").strip()
        collection_path = str(payload.get("collection_path") or payload.get("collection_name") or "").strip()
        if not collection_key and not collection_path:
            raise ZoteroWriteError("Collection payload is empty")

        collection_id = self._find_collection_id(conn, collection_key, collection_path, collection_columns)
        if collection_id is None:
            missing_ref = collection_key or collection_path
            raise ZoteroWriteError(f"Collection not found: {missing_ref}")

        conn.execute(
            "INSERT OR IGNORE INTO collectionItems (itemID, collectionID) VALUES (?, ?)",
            (item_id, collection_id),
        )
        self._touch_item(conn, item_id, item_columns)

    def _apply_collection_remove(
        self,
        conn: sqlite3.Connection,
        item_key: str,
        payload: dict[str, Any],
        item_columns: set[str],
        collection_columns: set[str],
    ) -> None:
        row = conn.execute(
            "SELECT itemID FROM items WHERE key = ? LIMIT 1",
            (item_key,),
        ).fetchone()
        if not row:
            raise ZoteroWriteError(f"Item not found: {item_key}")

        item_id = int(row["itemID"])
        collection_key = str(payload.get("collection_key") or "").strip()
        collection_path = str(payload.get("collection_path") or payload.get("collection_name") or "").strip()
        if not collection_key and not collection_path:
            raise ZoteroWriteError("Collection payload is empty")

        collection_id = self._find_collection_id(conn, collection_key, collection_path, collection_columns)
        if collection_id is None:
            return  # Already not in collection

        conn.execute(
            "DELETE FROM collectionItems WHERE itemID = ? AND collectionID = ?",
            (item_id, collection_id),
        )
        self._touch_item(conn, item_id, item_columns)

    def remove_items_from_collection(
        self,
        item_keys: list[str],
        collection_name: str,
        root_only: bool = True,
    ) -> int:
        """Remove items from a collection by name. Returns count of items removed."""
        if not item_keys or not collection_name.strip():
            return 0

        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.Error as _:
                pass

            if root_only:
                coll_row = conn.execute(
                    """
                    SELECT collectionID FROM collections
                    WHERE lower(collectionName) = lower(?)
                      AND parentCollectionID IS NULL
                    LIMIT 1
                    """,
                    (collection_name.strip(),),
                ).fetchone()
            else:
                coll_row = conn.execute(
                    """
                    SELECT collectionID FROM collections
                    WHERE lower(collectionName) = lower(?)
                    LIMIT 1
                    """,
                    (collection_name.strip(),),
                ).fetchone()

            if not coll_row:
                return 0

            collection_id = int(coll_row["collectionID"])
            item_columns = self._table_columns(conn, "items")
            removed = 0
            for item_key in item_keys:
                safe_key = str(item_key).strip()
                if not safe_key:
                    continue
                item_row = conn.execute(
                    "SELECT itemID FROM items WHERE key = ? LIMIT 1",
                    (safe_key,),
                ).fetchone()
                if not item_row:
                    continue
                item_id = int(item_row["itemID"])
                cursor = conn.execute(
                    "DELETE FROM collectionItems WHERE itemID = ? AND collectionID = ?",
                    (item_id, collection_id),
                )
                if int(cursor.rowcount or 0) > 0:
                    removed += 1
                    self._touch_item(conn, item_id, item_columns)

            conn.commit()
            return removed
        finally:
            conn.close()
