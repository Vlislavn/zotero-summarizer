from __future__ import annotations

import json
import random
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.request import urlopen


class ZoteroWriteError(RuntimeError):
    """Raised when writing to the local Zotero database fails."""


class ZoteroWriter:
    """Write adapter that applies reviewed tag/note changes to Zotero SQLite."""

    _KEY_ALPHABET = "23456789ABCDEFGHIJKLMNPQRSTUVWXYZ"

    def __init__(self, zotero_data_dir: str | Path | None = None) -> None:
        data_dir = Path(zotero_data_dir or (Path.home() / "Zotero")).expanduser().resolve()
        db_path = data_dir / "zotero.sqlite"
        if not data_dir.exists():
            raise ZoteroWriteError(f"Zotero data directory not found: {data_dir}")
        if not db_path.exists():
            raise ZoteroWriteError(f"Zotero database not found: {db_path}")

        self.data_dir = data_dir
        self.db_path = db_path

    def is_connector_running(self) -> bool:
        """Return True when Zotero connector HTTP server responds locally."""
        try:
            with urlopen("http://127.0.0.1:23119/connector/ping", timeout=0.8) as response:
                return int(getattr(response, "status", 0)) == 200
        except Exception:
            return False

    def backup_database(self) -> str:
        """Create timestamped backup of zotero.sqlite and return backup path."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = self.data_dir / f"zotero.sqlite.backup_{timestamp}"
        shutil.copy2(self.db_path, backup_path)
        return str(backup_path)

    def apply_changes(self, changes: Sequence[dict[str, Any]], create_backup: bool = True) -> dict[str, Any]:
        """Apply queued changes and return applied IDs and per-item failures."""
        if not changes:
            return {"applied_ids": [], "failed": [], "backup_path": None}

        backup_path = self.backup_database() if create_backup else None

        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.Error:
                pass

            item_columns = self._table_columns(conn, "items")
            tag_columns = self._table_columns(conn, "tags")
            item_tag_columns = self._table_columns(conn, "itemTags")
            note_columns = self._table_columns(conn, "itemNotes")
            collection_columns = self._table_columns(conn, "collections")
            collection_item_columns = self._table_columns(conn, "collectionItems")

            applied_ids: list[int] = []
            failed: list[dict[str, Any]] = []

            for change in changes:
                change_id = int(change.get("id") or 0)
                savepoint_name = f"change_{change_id or random.randint(1000, 9999)}"
                conn.execute(f"SAVEPOINT {savepoint_name}")
                try:
                    change_type = str(change.get("change_type") or "").strip()
                    item_key = str(change.get("item_key") or "").strip()
                    if not change_type or not item_key:
                        raise ZoteroWriteError("Invalid pending change record")

                    payload = change.get("payload_json", {})
                    payload_dict = self._coerce_payload(payload)

                    if change_type == "tag_changes":
                        self._apply_tag_change(
                            conn,
                            item_key=item_key,
                            payload=payload_dict,
                            item_columns=item_columns,
                            tag_columns=tag_columns,
                            item_tag_columns=item_tag_columns,
                        )
                    elif change_type == "add_note":
                        self._apply_note_change(
                            conn,
                            item_key=item_key,
                            payload=payload_dict,
                            item_columns=item_columns,
                            note_columns=note_columns,
                        )
                    elif change_type == "add_to_collection":
                        self._apply_collection_change(
                            conn,
                            item_key=item_key,
                            payload=payload_dict,
                            item_columns=item_columns,
                            collection_columns=collection_columns,
                            collection_item_columns=collection_item_columns,
                        )
                    elif change_type == "remove_from_collection":
                        self._apply_collection_remove(
                            conn,
                            item_key=item_key,
                            payload=payload_dict,
                            item_columns=item_columns,
                            collection_columns=collection_columns,
                        )
                    else:
                        raise ZoteroWriteError(f"Unsupported change type: {change_type}")

                    conn.execute(f"RELEASE {savepoint_name}")
                    applied_ids.append(change_id)
                except Exception as exc:
                    conn.execute(f"ROLLBACK TO {savepoint_name}")
                    conn.execute(f"RELEASE {savepoint_name}")
                    failed.append({"id": change_id, "error": str(exc)})

            conn.commit()
            return {"applied_ids": applied_ids, "failed": failed, "backup_path": backup_path}
        except sqlite3.Error as exc:
            conn.rollback()
            raise ZoteroWriteError(f"Failed to apply queued changes: {exc}") from exc
        finally:
            conn.close()

    @staticmethod
    def _coerce_payload(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, str):
            text = payload.strip()
            if not text:
                return {}
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ZoteroWriteError("Invalid JSON payload in pending change") from exc
            if not isinstance(parsed, dict):
                raise ZoteroWriteError("Pending change payload must be a JSON object")
            return parsed
        if payload is None:
            return {}
        raise ZoteroWriteError("Pending change payload must be a JSON object")

    def _apply_tag_change(
        self,
        conn: sqlite3.Connection,
        item_key: str,
        payload: dict[str, Any],
        item_columns: set[str],
        tag_columns: set[str],
        item_tag_columns: set[str],
    ) -> None:
        row = conn.execute(
            "SELECT itemID FROM items WHERE key = ? LIMIT 1",
            (item_key,),
        ).fetchone()
        if not row:
            raise ZoteroWriteError(f"Item not found: {item_key}")

        item_id = int(row["itemID"])
        add_tags = self._normalize_tags(payload.get("add_tags", []))
        remove_tags = self._normalize_tags(payload.get("remove_tags", []))

        for tag_name in add_tags:
            tag_id = self._ensure_tag(conn, tag_name, tag_columns)
            self._ensure_item_tag(conn, item_id, tag_id, item_tag_columns)

        for tag_name in remove_tags:
            tag_id = self._find_tag_id(conn, tag_name)
            if tag_id is None:
                continue
            conn.execute(
                "DELETE FROM itemTags WHERE itemID = ? AND tagID = ?",
                (item_id, tag_id),
            )

        self._touch_item(conn, item_id, item_columns)

    def _apply_note_change(
        self,
        conn: sqlite3.Connection,
        item_key: str,
        payload: dict[str, Any],
        item_columns: set[str],
        note_columns: set[str],
    ) -> None:
        parent_row = conn.execute(
            "SELECT itemID, libraryID FROM items WHERE key = ? LIMIT 1",
            (item_key,),
        ).fetchone()
        if not parent_row:
            raise ZoteroWriteError(f"Parent item not found: {item_key}")

        parent_item_id = int(parent_row["itemID"])
        library_id = int(parent_row["libraryID"])

        note_html = str(payload.get("note_html") or "").strip()
        if not note_html:
            raise ZoteroWriteError("Note payload is empty")
        note_title = str(payload.get("note_title") or "").strip()
        if not note_title:
            note_title = self._note_title_from_html(note_html)

        note_item_type_id = self._get_item_type_id(conn, "note")
        if note_item_type_id is None:
            raise ZoteroWriteError("Could not find note item type")

        new_item_key = self._generate_unique_item_key(conn)
        now = self._sqlite_timestamp_now()

        insert_values: dict[str, Any] = {}
        if "itemTypeID" in item_columns:
            insert_values["itemTypeID"] = note_item_type_id
        if "libraryID" in item_columns:
            insert_values["libraryID"] = library_id
        if "key" in item_columns:
            insert_values["key"] = new_item_key
        if "version" in item_columns:
            insert_values["version"] = 1
        if "synced" in item_columns:
            insert_values["synced"] = 0
        if "dateAdded" in item_columns:
            insert_values["dateAdded"] = now
        if "dateModified" in item_columns:
            insert_values["dateModified"] = now
        if "clientDateModified" in item_columns:
            insert_values["clientDateModified"] = now

        required_item_columns = {"itemTypeID", "libraryID", "key"}
        if not required_item_columns.issubset(item_columns):
            raise ZoteroWriteError("Unsupported Zotero schema: required items columns missing")

        columns_sql = ", ".join(insert_values.keys())
        placeholders = ", ".join("?" for _ in insert_values)
        cursor = conn.execute(
            f"INSERT INTO items ({columns_sql}) VALUES ({placeholders})",
            tuple(insert_values.values()),
        )
        note_item_id = int(cursor.lastrowid)

        required_note_columns = {"itemID", "parentItemID", "note"}
        if not required_note_columns.issubset(note_columns):
            raise ZoteroWriteError("Unsupported Zotero schema: required itemNotes columns missing")

        note_insert_values: dict[str, Any] = {
            "itemID": note_item_id,
            "parentItemID": parent_item_id,
            "note": note_html,
        }
        if "title" in note_columns:
            note_insert_values["title"] = note_title

        note_columns_sql = ", ".join(note_insert_values.keys())
        note_placeholders = ", ".join("?" for _ in note_insert_values)
        conn.execute(
            f"INSERT INTO itemNotes ({note_columns_sql}) VALUES ({note_placeholders})",
            tuple(note_insert_values.values()),
        )

        self._touch_item(conn, parent_item_id, item_columns)

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
            except sqlite3.Error:
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

    @staticmethod
    def _normalize_tags(value: Any) -> list[str]:
        if isinstance(value, str):
            raw_tags = [value]
        elif isinstance(value, list):
            raw_tags = [str(v) for v in value]
        else:
            raw_tags = []

        seen: set[str] = set()
        normalized: list[str] = []
        for raw in raw_tags:
            tag = raw.strip()
            if not tag:
                continue
            folded = tag.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            normalized.append(tag)
        return normalized

    def _ensure_tag(self, conn: sqlite3.Connection, tag_name: str, tag_columns: set[str]) -> int:
        existing = self._find_tag_id(conn, tag_name)
        if existing is not None:
            return existing

        insert_values: dict[str, Any] = {"name": tag_name}
        if "type" in tag_columns:
            insert_values["type"] = 0

        columns_sql = ", ".join(insert_values.keys())
        placeholders = ", ".join("?" for _ in insert_values)
        try:
            cursor = conn.execute(
                f"INSERT INTO tags ({columns_sql}) VALUES ({placeholders})",
                tuple(insert_values.values()),
            )
            return int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            existing = self._find_tag_id(conn, tag_name)
            if existing is None:
                raise ZoteroWriteError(f"Unable to create tag: {tag_name}")
            return existing

    @staticmethod
    def _ensure_item_tag(conn: sqlite3.Connection, item_id: int, tag_id: int, item_tag_columns: set[str]) -> None:
        if "type" in item_tag_columns:
            conn.execute(
                "INSERT OR IGNORE INTO itemTags (itemID, tagID, type) VALUES (?, ?, 0)",
                (item_id, tag_id),
            )
        else:
            conn.execute(
                "INSERT OR IGNORE INTO itemTags (itemID, tagID) VALUES (?, ?)",
                (item_id, tag_id),
            )

    @staticmethod
    def _find_tag_id(conn: sqlite3.Connection, tag_name: str) -> int | None:
        row = conn.execute(
            "SELECT tagID FROM tags WHERE lower(name) = lower(?) LIMIT 1",
            (tag_name,),
        ).fetchone()
        if not row:
            return None
        return int(row["tagID"])

    def _find_collection_id(
        self,
        conn: sqlite3.Connection,
        collection_key: str,
        collection_path: str,
        collection_columns: set[str],
    ) -> int | None:
        if collection_key:
            row = conn.execute(
                "SELECT collectionID FROM collections WHERE key = ? LIMIT 1",
                (collection_key,),
            ).fetchone()
            if row:
                return int(row["collectionID"])

        if collection_path:
            by_path = self._find_collection_id_by_path(conn, collection_path, collection_columns)
            if by_path is not None:
                return by_path

            row = conn.execute(
                "SELECT collectionID FROM collections WHERE lower(collectionName) = lower(?) LIMIT 1",
                (collection_path,),
            ).fetchone()
            if row:
                return int(row["collectionID"])

        return None

    @staticmethod
    def _find_collection_id_by_path(
        conn: sqlite3.Connection,
        collection_path: str,
        collection_columns: set[str],
    ) -> int | None:
        parts = [part.strip() for part in collection_path.split(">") if part.strip()]
        if not parts:
            return None

        if "parentCollectionID" not in collection_columns:
            return None

        current_parent: int | None = None
        current_collection_id: int | None = None

        for part in parts:
            if current_parent is None:
                row = conn.execute(
                    """
                    SELECT collectionID
                    FROM collections
                    WHERE parentCollectionID IS NULL AND lower(collectionName) = lower(?)
                    ORDER BY collectionID ASC
                    LIMIT 1
                    """,
                    (part,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT collectionID
                    FROM collections
                    WHERE parentCollectionID = ? AND lower(collectionName) = lower(?)
                    ORDER BY collectionID ASC
                    LIMIT 1
                    """,
                    (current_parent, part),
                ).fetchone()

            if not row:
                return None

            current_collection_id = int(row["collectionID"])
            current_parent = current_collection_id

        return current_collection_id

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row["name"]) for row in rows}

    @staticmethod
    def _get_item_type_id(conn: sqlite3.Connection, type_name: str) -> int | None:
        row = conn.execute(
            "SELECT itemTypeID FROM itemTypes WHERE typeName = ? LIMIT 1",
            (type_name,),
        ).fetchone()
        if not row:
            return None
        return int(row["itemTypeID"])

    @staticmethod
    def _sqlite_timestamp_now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _touch_item(conn: sqlite3.Connection, item_id: int, item_columns: set[str]) -> None:
        assignments: list[str] = []
        params: list[Any] = []
        now = ZoteroWriter._sqlite_timestamp_now()

        if "dateModified" in item_columns:
            assignments.append("dateModified = ?")
            params.append(now)
        if "clientDateModified" in item_columns:
            assignments.append("clientDateModified = ?")
            params.append(now)
        if "version" in item_columns:
            assignments.append("version = COALESCE(version, 0) + 1")
        if "synced" in item_columns:
            assignments.append("synced = 0")

        if not assignments:
            return

        params.append(item_id)
        conn.execute(
            f"UPDATE items SET {', '.join(assignments)} WHERE itemID = ?",
            tuple(params),
        )

    def _generate_unique_item_key(self, conn: sqlite3.Connection) -> str:
        for _ in range(32):
            key = "".join(random.choice(self._KEY_ALPHABET) for _ in range(8))
            row = conn.execute("SELECT 1 FROM items WHERE key = ? LIMIT 1", (key,)).fetchone()
            if row is None:
                return key
        raise ZoteroWriteError("Could not generate a unique Zotero item key")

    @staticmethod
    def _note_title_from_html(note_html: str) -> str:
        plain = re.sub(r"<[^>]+>", " ", note_html)
        plain = re.sub(r"\s+", " ", plain).strip()
        if not plain:
            return "Triage Note"
        return plain[:120]
