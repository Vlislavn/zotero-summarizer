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
            item_data_columns = self._table_columns(conn, "itemData")
            item_data_value_columns = self._table_columns(conn, "itemDataValues")
            creators_columns = self._table_columns(conn, "creators")
            item_creators_columns = self._table_columns(conn, "itemCreators")

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
                    elif change_type == "create_item_from_feed":
                        self._apply_create_item_from_feed(
                            conn,
                            new_item_key=item_key,
                            payload=payload_dict,
                            item_columns=item_columns,
                            item_data_columns=item_data_columns,
                            item_data_value_columns=item_data_value_columns,
                            creators_columns=creators_columns,
                            item_creators_columns=item_creators_columns,
                        )
                    elif change_type == "promote_from_inbox":
                        # Promote = remove from "Inbox" collection; the user's target
                        # collection assignments are queued as separate add_to_collection
                        # changes by the orchestrator (already supported above).
                        self._apply_collection_remove(
                            conn,
                            item_key=item_key,
                            payload={"collection_path": "Inbox"},
                            item_columns=item_columns,
                            collection_columns=collection_columns,
                        )
                    elif change_type == "mark_feed_item_read":
                        self._apply_mark_feed_item_read(
                            conn,
                            item_key=item_key,
                            payload=payload_dict,
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

    def _apply_create_item_from_feed(
        self,
        conn: sqlite3.Connection,
        new_item_key: str,
        payload: dict[str, Any],
        item_columns: set[str],
        item_data_columns: set[str],
        item_data_value_columns: set[str],
        creators_columns: set[str],
        item_creators_columns: set[str],
    ) -> None:
        """Create a top-level Zotero item from a feed payload.

        The new item lands in the user's personal library (libraryID where
        type='user'). Zotero's own "Find Available PDF" preference fetches PDFs
        after the item is created — we never download PDFs ourselves.

        Companion changes (add_to_collection for "Inbox" and matched user
        collections, tag_changes, add_note) are queued separately by the
        orchestrator and reference the same `new_item_key`.
        """
        title = str(payload.get("title") or "").strip()
        if not title:
            raise ZoteroWriteError("Feed item payload missing title")

        # Reject pre-existing keys to keep create idempotent.
        existing = conn.execute(
            "SELECT itemID FROM items WHERE key = ? LIMIT 1",
            (new_item_key,),
        ).fetchone()
        if existing:
            # Idempotent re-apply: skip silently. The orchestrator's
            # processed_feed_items table is the source of truth for "did we
            # already do this"; this branch is for retry safety only.
            return

        item_type_name = str(payload.get("item_type") or "journalArticle").strip()
        if item_type_name not in self._ALLOWED_FEED_ITEM_TYPES:
            item_type_name = "journalArticle"
        item_type_id = self._get_item_type_id(conn, item_type_name)
        if item_type_id is None:
            raise ZoteroWriteError(f"Could not find item type: {item_type_name}")

        user_library_row = conn.execute(
            "SELECT libraryID FROM libraries WHERE type='user' LIMIT 1"
        ).fetchone()
        if not user_library_row:
            raise ZoteroWriteError("No user library found in Zotero")
        user_library_id = int(user_library_row["libraryID"])

        required_item_columns = {"itemTypeID", "libraryID", "key"}
        if not required_item_columns.issubset(item_columns):
            raise ZoteroWriteError("Unsupported Zotero schema: required items columns missing")

        now = self._sqlite_timestamp_now()
        insert_values: dict[str, Any] = {
            "itemTypeID": item_type_id,
            "libraryID": user_library_id,
            "key": new_item_key,
        }
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

        columns_sql = ", ".join(insert_values.keys())
        placeholders = ", ".join("?" for _ in insert_values)
        cursor = conn.execute(
            f"INSERT INTO items ({columns_sql}) VALUES ({placeholders})",
            tuple(insert_values.values()),
        )
        new_item_id = int(cursor.lastrowid)

        # Write itemData rows for every supplied field.
        required_data_columns = {"itemID", "fieldID", "valueID"}
        if not required_data_columns.issubset(item_data_columns):
            raise ZoteroWriteError("Unsupported Zotero schema: required itemData columns missing")
        if "valueID" not in item_data_value_columns or "value" not in item_data_value_columns:
            raise ZoteroWriteError("Unsupported Zotero schema: itemDataValues columns missing")

        for payload_key, field_name in self._FEED_PAYLOAD_TO_FIELD:
            value = payload.get(payload_key)
            if value is None:
                continue
            text = str(value).strip()
            if not text:
                continue
            field_id = self._get_field_id(conn, field_name)
            if field_id is None:
                # Schema lacks this field; safely skip rather than fail the whole insert.
                continue
            value_id = self._upsert_item_data_value(conn, text)
            conn.execute(
                "INSERT OR IGNORE INTO itemData (itemID, fieldID, valueID) VALUES (?, ?, ?)",
                (new_item_id, field_id, value_id),
            )

        # Authors -> creators + itemCreators
        authors_raw = payload.get("authors")
        if authors_raw:
            self._insert_creators(
                conn,
                item_id=new_item_id,
                authors=authors_raw,
                creators_columns=creators_columns,
                item_creators_columns=item_creators_columns,
            )

    @staticmethod
    def _get_field_id(conn: sqlite3.Connection, field_name: str) -> int | None:
        row = conn.execute(
            "SELECT fieldID FROM fields WHERE fieldName = ? LIMIT 1",
            (field_name,),
        ).fetchone()
        return int(row["fieldID"]) if row else None

    @staticmethod
    def _upsert_item_data_value(conn: sqlite3.Connection, value: str) -> int:
        row = conn.execute(
            "SELECT valueID FROM itemDataValues WHERE value = ? LIMIT 1",
            (value,),
        ).fetchone()
        if row:
            return int(row["valueID"])
        cursor = conn.execute(
            "INSERT INTO itemDataValues (value) VALUES (?)",
            (value,),
        )
        return int(cursor.lastrowid)

    def _insert_creators(
        self,
        conn: sqlite3.Connection,
        item_id: int,
        authors: Any,
        creators_columns: set[str],
        item_creators_columns: set[str],
    ) -> None:
        if isinstance(authors, str):
            entries = [a.strip() for a in re.split(r"[;\n]", authors) if a.strip()]
        elif isinstance(authors, list):
            entries = [str(a).strip() for a in authors if str(a).strip()]
        else:
            return
        if not entries:
            return

        # Default to creatorTypeID for 'author' (most Zotero schemas: ID 8).
        author_type_row = conn.execute(
            "SELECT creatorTypeID FROM creatorTypes WHERE creatorType='author' LIMIT 1"
        ).fetchone()
        author_type_id = int(author_type_row["creatorTypeID"]) if author_type_row else 8

        required_creators_cols = {"firstName", "lastName", "fieldMode"}
        if not required_creators_cols.issubset(creators_columns):
            return  # Schema unexpected; skip silently rather than failing the whole create.

        required_item_creators_cols = {"itemID", "creatorID", "creatorTypeID", "orderIndex"}
        if not required_item_creators_cols.issubset(item_creators_columns):
            return

        for order_index, entry in enumerate(entries):
            first, last, field_mode = self._split_author_name(entry)
            existing = conn.execute(
                """
                SELECT creatorID FROM creators
                WHERE COALESCE(firstName,'')=? AND COALESCE(lastName,'')=? AND fieldMode=?
                LIMIT 1
                """,
                (first, last, field_mode),
            ).fetchone()
            if existing:
                creator_id = int(existing["creatorID"])
            else:
                cursor = conn.execute(
                    "INSERT INTO creators (firstName, lastName, fieldMode) VALUES (?, ?, ?)",
                    (first, last, field_mode),
                )
                creator_id = int(cursor.lastrowid)
            conn.execute(
                "INSERT OR IGNORE INTO itemCreators (itemID, creatorID, creatorTypeID, orderIndex) VALUES (?, ?, ?, ?)",
                (item_id, creator_id, author_type_id, order_index),
            )

    @staticmethod
    def _split_author_name(name: str) -> tuple[str, str, int]:
        """Split an author string into (firstName, lastName, fieldMode).

        Zotero's fieldMode=1 means single-name entry (lastName-only, used for
        institutions, single-name authors). Otherwise fieldMode=0 with split.
        """
        text = name.strip()
        if not text:
            return ("", "", 1)
        # "Last, First" form
        if "," in text:
            last, _, first = text.partition(",")
            return (first.strip(), last.strip(), 0)
        # "First Last" form — split on last whitespace
        parts = text.rsplit(None, 1)
        if len(parts) == 2:
            return (parts[0].strip(), parts[1].strip(), 0)
        # Single token — treat as institution/single-name
        return ("", text, 1)

    def _apply_mark_feed_item_read(
        self,
        conn: sqlite3.Connection,
        item_key: str,
        payload: dict[str, Any],
    ) -> None:
        """Write `feedItems.readTime = datetime('now')` for a single feed item.

        Phase 1.5: the daemon marks every processed feed item read so Zotero's
        unread badge clears naturally. Format `YYYY-MM-DD HH:MM:SS` UTC matches
        what Zotero's own client writes (verified against 11 pre-existing rows
        in the user's live DB during pre-flight).

        The payload provides `feed_library_id` + `feed_item_id` (Zotero's
        internal `feedItems.itemID`). We do NOT key on the Zotero `key` field
        here because feed items don't necessarily have one — only top-level
        items in the user library do.
        """
        # item_key is unused for this change type (feed items are keyed by
        # feedItems.itemID, not by the user-library `items.key` field) but kept
        # in the signature to match the uniform dispatch shape used everywhere
        # else in apply_changes().
        _ = item_key
        feed_library_id = int(payload.get("feed_library_id") or 0)
        feed_item_id = int(payload.get("feed_item_id") or 0)
        if feed_library_id <= 0 or feed_item_id <= 0:
            raise ZoteroWriteError("mark_feed_item_read payload requires feed_library_id + feed_item_id")
        # SQLite's datetime('now') is UTC, matches Zotero's own readTime format.
        conn.execute(
            "UPDATE feedItems SET readTime = datetime('now') WHERE itemID = ?",
            (feed_item_id,),
        )

    def mark_feed_items_read(self, feed_item_ids: list[int]) -> int:
        """Directly mark a batch of feed items as read in Zotero.

        Phase 1.5 daemon entry point — bypasses the pending-changes review
        queue because (a) the write is idempotent and recoverable, (b) the
        user explicitly asked for the unread badge to clear automatically,
        and (c) readTime is a Zotero-internal display field, not user data.

        Returns the number of feedItems rows actually updated. MCP path
        REJECTS this method via change_type isolation (defense in depth).
        """
        if not feed_item_ids:
            return 0
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.Error:
                pass
            placeholders = ",".join("?" for _ in feed_item_ids)
            cursor = conn.execute(
                f"UPDATE feedItems SET readTime = datetime('now') "
                f"WHERE itemID IN ({placeholders}) AND readTime IS NULL",
                tuple(int(i) for i in feed_item_ids),
            )
            conn.commit()
            return int(cursor.rowcount or 0)
        except sqlite3.Error as exc:
            conn.rollback()
            raise ZoteroWriteError(f"Failed to mark feed items read: {exc}") from exc
        finally:
            conn.close()

    def apply_feed_materialization(
        self,
        *,
        new_item_key: str,
        feed_payload: dict[str, Any],
        inbox_collection_name: str,
        matched_collections: list[str],
        tags: list[str],
        note_title: str,
        note_html: str,
        provenance_tag: str | None = None,
        create_backup: bool = False,
    ) -> dict[str, Any]:
        """Apply ALL pieces of a feed materialization atomically.

        Phase 1.5 daemon-direct-write path: skips the pending-changes queue
        because feed items create NEW Zotero items in the Inbox collection
        (low blast radius — user can delete them later). The pending queue
        remains for library-centric tag/note/collection changes on existing
        items, which is the original safety case.

        Order:
          1. INSERT items + itemData + creators (top-level Zotero item)
          2. INSERT collectionItems for "Inbox"
          3. INSERT collectionItems for each matched user collection
          4. INSERT/LINK each tag (including auto-tags for slash-prefixed)
          5. INSERT itemNotes (the v3 triage note)
          6. Stamp items.dateModified

        Wrapped in a single transaction; rolled back on any failure.
        Returns {"item_key": ..., "applied_steps": [...]}.
        """
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        backup_path: str | None = None
        if create_backup:
            backup_path = self.backup_database()
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
            item_data_columns = self._table_columns(conn, "itemData")
            item_data_value_columns = self._table_columns(conn, "itemDataValues")
            creators_columns = self._table_columns(conn, "creators")
            item_creators_columns = self._table_columns(conn, "itemCreators")

            applied_steps: list[str] = []

            # 1. Create the top-level item.
            self._apply_create_item_from_feed(
                conn,
                new_item_key=new_item_key,
                payload=feed_payload,
                item_columns=item_columns,
                item_data_columns=item_data_columns,
                item_data_value_columns=item_data_value_columns,
                creators_columns=creators_columns,
                item_creators_columns=item_creators_columns,
            )
            applied_steps.append("create_item")

            # 2. Inbox collection.
            try:
                self._apply_collection_change(
                    conn,
                    item_key=new_item_key,
                    payload={"collection_path": inbox_collection_name},
                    item_columns=item_columns,
                    collection_columns=collection_columns,
                    collection_item_columns=collection_item_columns,
                )
                applied_steps.append("add_to_inbox")
            except ZoteroWriteError:
                # Auto-create the Inbox collection if missing, then retry.
                self._ensure_collection(conn, inbox_collection_name, collection_columns)
                self._apply_collection_change(
                    conn,
                    item_key=new_item_key,
                    payload={"collection_path": inbox_collection_name},
                    item_columns=item_columns,
                    collection_columns=collection_columns,
                    collection_item_columns=collection_item_columns,
                )
                applied_steps.append("add_to_inbox_after_autocreate")

            # 3. Matched user collections (best-effort: skip ones that don't exist).
            seen_paths = {inbox_collection_name.casefold()}
            for path in matched_collections or []:
                clean = str(path or "").strip()
                if not clean or clean.casefold() in seen_paths:
                    continue
                seen_paths.add(clean.casefold())
                try:
                    self._apply_collection_change(
                        conn,
                        item_key=new_item_key,
                        payload={"collection_path": clean},
                        item_columns=item_columns,
                        collection_columns=collection_columns,
                        collection_item_columns=collection_item_columns,
                    )
                    applied_steps.append(f"add_to_collection:{clean}")
                except ZoteroWriteError:
                    # Don't fail materialization for missing user collections.
                    pass

            # 4. Tags (+ provenance tag with auto-tag type if provided).
            all_tags = list(self._normalize_tags(tags))
            if provenance_tag and provenance_tag not in all_tags:
                all_tags.append(provenance_tag)
            self._apply_tag_change(
                conn,
                item_key=new_item_key,
                payload={"add_tags": all_tags, "remove_tags": []},
                item_columns=item_columns,
                tag_columns=tag_columns,
                item_tag_columns=item_tag_columns,
            )
            applied_steps.append("apply_tags")

            # 5. Note.
            if note_html and note_html.strip():
                self._apply_note_change(
                    conn,
                    item_key=new_item_key,
                    payload={"note_title": note_title, "note_html": note_html},
                    item_columns=item_columns,
                    note_columns=note_columns,
                )
                applied_steps.append("add_note")

            conn.commit()
            return {
                "item_key": new_item_key,
                "applied_steps": applied_steps,
                "backup_path": backup_path,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

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
        for _ in range(32):
            key = "".join(random.choice(self._KEY_ALPHABET) for _ in range(8))
            row = conn.execute("SELECT 1 FROM collections WHERE key = ? LIMIT 1", (key,)).fetchone()
            if row is None:
                return key
        raise ZoteroWriteError("Could not generate a unique Zotero collection key")

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
