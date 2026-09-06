"""Collection add/remove methods of ZoteroWriter (mixin)."""
from __future__ import annotations

import sqlite3
from typing import Any

from zotero_summarizer.integrations._zotero_read_common import _USER_LIBRARY_ID_SELECT
from zotero_summarizer.integrations._zotero_write_common import (  # noqa: F401
    ZoteroWriteError,
    generate_unique_key,
    resolve_user_library_item_id,
)


class ZoteroCollectionMixin:
    def _find_collection_id(self, conn: sqlite3.Connection, key: str, path: str) -> int | None:
        if key:
            row = conn.execute(
                f"SELECT collectionID FROM collections WHERE key = ? "
                f"AND libraryID = ({_USER_LIBRARY_ID_SELECT}) LIMIT 1", (key,),
            ).fetchone()
            return int(row["collectionID"]) if row else None
        by_path = self._find_collection_id_by_path(conn, path)
        if by_path is not None:
            return by_path
        row = conn.execute(
            f"SELECT collectionID FROM collections WHERE lower(collectionName) = lower(?) "
            f"AND libraryID = ({_USER_LIBRARY_ID_SELECT}) ORDER BY collectionID LIMIT 1", (path,),
        ).fetchone()
        return int(row["collectionID"]) if row else None

    @staticmethod
    def _find_collection_id_by_path(conn: sqlite3.Connection, path: str) -> int | None:
        parent = None
        for part in (part.strip() for part in path.split(">") if part.strip()):
            row = conn.execute(
                f"SELECT collectionID FROM collections WHERE parentCollectionID IS ? "
                f"AND lower(collectionName) = lower(?) AND libraryID = ({_USER_LIBRARY_ID_SELECT}) "
                "ORDER BY collectionID LIMIT 1", (parent, part),
            ).fetchone()
            if row is None:
                return None
            parent = int(row["collectionID"])
        return parent

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
            f"SELECT collectionID FROM collections WHERE lower(collectionName)=lower(?) "
            f"AND parentCollectionID IS NULL AND libraryID = ({_USER_LIBRARY_ID_SELECT}) LIMIT 1",
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

        new_key = generate_unique_key(conn, "collections", self._KEY_ALPHABET, "collection")
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

    def _resolve_collection_target(
        self,
        conn: sqlite3.Connection,
        item_key: str,
        payload: dict[str, Any],
    ) -> tuple[int, int | None, str]:
        """Resolve ``(item_id, collection_id, missing_ref)`` for a collection
        add/remove. ``collection_id`` is ``None`` when no collection matches
        ``collection_key``/``collection_path`` in ``payload``; ``missing_ref``
        names the collection so the caller can apply its own not-found policy
        (add raises, remove treats it as a no-op)."""
        item_id = resolve_user_library_item_id(conn, item_key)
        collection_key = str(payload.get("collection_key") or "").strip()
        collection_path = str(payload.get("collection_path") or payload.get("collection_name") or "").strip()
        if not collection_key and not collection_path:
            raise ZoteroWriteError("Collection payload is empty")

        collection_id = self._find_collection_id(conn, collection_key, collection_path)
        return item_id, collection_id, collection_key or collection_path

    def _apply_collection_change(
        self,
        conn: sqlite3.Connection,
        item_key: str,
        payload: dict[str, Any],
        item_columns: set[str],
        collection_item_columns: set[str],
    ) -> None:
        if not {"itemID", "collectionID"}.issubset(collection_item_columns):
            raise ZoteroWriteError("Unsupported Zotero schema: required collectionItems columns missing")

        item_id, collection_id, missing_ref = self._resolve_collection_target(
            conn, item_key, payload
        )
        if collection_id is None:
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
    ) -> None:
        item_id, collection_id, _missing_ref = self._resolve_collection_target(
            conn, item_key, payload
        )
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
        """Remove items from a collection by name. Returns count of items removed.

        Wrapped in ``_retry_on_lock`` like the other Zotero writes (see
        ``zotero_write.apply_changes`` / ``_zotero_write_items.mark_feed_items_read``)
        so a transient 'database is locked' from a still-open Zotero connector is
        retried instead of failing this best-effort batch outright.
        """
        if not item_keys or not collection_name.strip():
            return 0

        def _do() -> int:
            conn = sqlite3.connect(str(self.db_path), timeout=15)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA journal_mode=WAL")
                coll_row = conn.execute(
                    f"SELECT collectionID FROM collections WHERE lower(collectionName) = lower(?) "
                    f"AND libraryID = ({_USER_LIBRARY_ID_SELECT}) "
                    "AND (NOT ? OR parentCollectionID IS NULL) ORDER BY collectionID LIMIT 1",
                    (collection_name.strip(), root_only),
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
                    item_id = resolve_user_library_item_id(conn, safe_key, required=False)
                    if item_id is None:
                        continue
                    cursor = conn.execute(
                        "DELETE FROM collectionItems WHERE itemID = ? AND collectionID = ?",
                        (item_id, collection_id),
                    )
                    if int(cursor.rowcount or 0) > 0:
                        removed += 1
                        self._touch_item(conn, item_id, item_columns)

                conn.commit()
                return removed
            except sqlite3.Error:
                conn.rollback()
                raise
            finally:
                conn.close()

        try:
            return self._retry_on_lock(_do, ctx="remove_items_from_collection")
        except sqlite3.OperationalError as exc:
            raise ZoteroWriteError(
                f"Failed to remove items from collection {collection_name!r}: DB still locked after retries: {exc}"
            ) from exc
