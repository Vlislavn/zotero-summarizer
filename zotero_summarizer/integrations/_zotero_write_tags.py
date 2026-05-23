"""Tag/note appliers + low-level write helpers of ZoteroWriter (mixin)."""
from __future__ import annotations

import random
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from zotero_summarizer.integrations._zotero_write_common import ZoteroWriteError  # noqa: F401


class ZoteroTagMixin:
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
            # System-managed tags (slash-prefixed) get itemTags.type=1 so Zotero
            # renders them as auto-assigned (subtler chip) rather than user tags.
            link_type = 1 if tag_name.startswith("/") else 0
            self._ensure_item_tag(conn, item_id, tag_id, item_tag_columns, tag_type=link_type)

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

    def _apply_upsert_note_change(
        self,
        conn: sqlite3.Connection,
        item_key: str,
        payload: dict[str, Any],
        item_columns: set[str],
        note_columns: set[str],
    ) -> None:
        """Insert OR update a single marked note on an item.

        Finds the item's child note whose HTML contains ``payload['marker']`` and
        replaces its content (so re-saving a verdict never duplicates the note);
        with no marker or no existing match, falls back to a plain insert.
        """
        marker = str(payload.get("marker") or "").strip()
        note_html = str(payload.get("note_html") or "").strip()
        if not note_html:
            raise ZoteroWriteError("Note payload is empty")

        parent_row = conn.execute(
            "SELECT itemID FROM items WHERE key = ? LIMIT 1", (item_key,)
        ).fetchone()
        if not parent_row:
            raise ZoteroWriteError(f"Parent item not found: {item_key}")
        parent_item_id = int(parent_row["itemID"])

        existing = None
        if marker:
            existing = conn.execute(
                "SELECT itemID FROM itemNotes WHERE parentItemID = ? AND note LIKE ? LIMIT 1",
                (parent_item_id, f"%{marker}%"),
            ).fetchone()
        if existing is None:
            self._apply_note_change(
                conn,
                item_key=item_key,
                payload=payload,
                item_columns=item_columns,
                note_columns=note_columns,
            )
            return

        note_item_id = int(existing["itemID"])
        note_title = str(payload.get("note_title") or "").strip() or self._note_title_from_html(note_html)
        set_clauses = ["note = ?"]
        params: list[Any] = [note_html]
        if "title" in note_columns:
            set_clauses.append("title = ?")
            params.append(note_title)
        params.append(note_item_id)
        conn.execute(
            f"UPDATE itemNotes SET {', '.join(set_clauses)} WHERE itemID = ?",
            tuple(params),
        )
        self._touch_item(conn, note_item_id, item_columns)
        self._touch_item(conn, parent_item_id, item_columns)

    # Map of payload key -> Zotero field name; only fields present in the
    # `fields` table will actually be written, so this list can include all
    # plausible feed-supplied fields without breaking on schema variation.
    _FEED_PAYLOAD_TO_FIELD = (
        ("title", "title"),
        ("abstract", "abstractNote"),
        ("url", "url"),
        ("doi", "DOI"),
        ("publication_date", "date"),
        ("publication_title", "publicationTitle"),
        ("language", "language"),
    )

    _ALLOWED_FEED_ITEM_TYPES = frozenset({"journalArticle", "preprint", "webpage"})

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

    def _ensure_tag(
        self,
        conn: sqlite3.Connection,
        tag_name: str,
        tag_columns: set[str],
        *,
        tag_type: int | None = None,
    ) -> int:
        """Find-or-insert a tag. Slash-prefixed names default to system tags (type=1).

        Zotero's `tags.type` discriminates user-applied (0) from auto-assigned (1)
        tags — the latter render with a subtler chip in Zotero's UI and are the
        convention used by 3rd-party plugins (Zotero Actions & Tags, Beaver, etc.)
        for system-managed labels like /zs/feeds-v3.
        """
        existing = self._find_tag_id(conn, tag_name)
        if existing is not None:
            return existing

        if tag_type is None:
            tag_type = 1 if tag_name.startswith("/") else 0

        insert_values: dict[str, Any] = {"name": tag_name}
        if "type" in tag_columns:
            insert_values["type"] = int(tag_type)

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
    def _ensure_item_tag(
        conn: sqlite3.Connection,
        item_id: int,
        tag_id: int,
        item_tag_columns: set[str],
        *,
        tag_type: int = 0,
    ) -> None:
        """Link a tag to an item. tag_type=1 = auto/system-assigned (per Zotero convention)."""
        if "type" in item_tag_columns:
            conn.execute(
                "INSERT OR IGNORE INTO itemTags (itemID, tagID, type) VALUES (?, ?, ?)",
                (item_id, tag_id, int(tag_type)),
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
        now = ZoteroTagMixin._sqlite_timestamp_now()

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
