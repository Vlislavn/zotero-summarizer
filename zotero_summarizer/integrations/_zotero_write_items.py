"""Item creation + feed materialization methods of ZoteroWriter (mixin)."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from zotero_summarizer.integrations._zotero_write_common import (  # noqa: F401
    LOGGER,
    WriteColumns,
    ZoteroWriteError,
    read_write_columns,
    resolve_user_library_item_id,
)


class ZoteroItemWriteMixin:
    def _apply_create_item_from_feed(
        self,
        conn: sqlite3.Connection,
        *,
        new_item_key: str,
        payload: dict[str, Any],
        cols: WriteColumns,
    ) -> None:
        """Create a top-level Zotero item from a feed payload.

        The new item lands in the user's personal library (libraryID where
        type='user'). Zotero's own "Find Available PDF" preference fetches PDFs
        after the item is created — we never download PDFs ourselves.

        Companion changes (add_to_collection for "Inbox" and matched user
        collections, tag_changes, add_note) are queued separately by the
        orchestrator and reference the same `new_item_key`.
        """
        new_item_id = self._insert_feed_item_row(
            conn, new_item_key=new_item_key, payload=payload, item_columns=cols.items
        )
        if new_item_id is None:
            return
        self._insert_feed_item_data(conn, new_item_id, payload, cols)
        authors_raw = payload.get("authors")
        if authors_raw:
            self._insert_creators(
                conn,
                item_id=new_item_id,
                authors=authors_raw,
                creators_columns=cols.creators,
                item_creators_columns=cols.item_creators,
            )

    def _insert_feed_item_row(
        self,
        conn: sqlite3.Connection,
        *,
        new_item_key: str,
        payload: dict[str, Any],
        item_columns: set[str],
    ) -> int | None:
        """Insert the top-level Zotero row; return None for an idempotent replay."""
        title = str(payload.get("title") or "").strip()
        if not title:
            raise ZoteroWriteError("Feed item payload missing title")
        existing = conn.execute(
            "SELECT itemID FROM items WHERE key = ? LIMIT 1",
            (new_item_key,),
        ).fetchone()
        if existing:
            return None
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
            raise ZoteroWriteError(
                "Unsupported Zotero schema: required items columns missing"
            )

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
        return int(cursor.lastrowid)

    def _insert_feed_item_data(
        self,
        conn: sqlite3.Connection,
        item_id: int,
        payload: dict[str, Any],
        cols: WriteColumns,
    ) -> None:
        """Write every supported bibliographic field for a new feed item."""
        if not {"itemID", "fieldID", "valueID"}.issubset(cols.item_data):
            raise ZoteroWriteError(
                "Unsupported Zotero schema: required itemData columns missing"
            )
        if (
            "valueID" not in cols.item_data_values
            or "value" not in cols.item_data_values
        ):
            raise ZoteroWriteError(
                "Unsupported Zotero schema: itemDataValues columns missing"
            )

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
                (item_id, field_id, value_id),
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

        required_item_creators_cols = {
            "itemID",
            "creatorID",
            "creatorTypeID",
            "orderIndex",
        }
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

    def _add_matched_collections(
        self,
        conn: sqlite3.Connection,
        *,
        new_item_key: str,
        matched_collections: list[str] | None,
        inbox_collection_name: str,
        cols: WriteColumns,
    ) -> list[str]:
        """Add every requested user collection, raising if any target is invalid."""
        steps: list[str] = []
        seen_paths = {inbox_collection_name.casefold()}
        for path in matched_collections or []:
            clean = str(path or "").strip()
            if not clean or clean.casefold() in seen_paths:
                continue
            seen_paths.add(clean.casefold())
            self._apply_collection_change(
                conn,
                item_key=new_item_key,
                payload={"collection_path": clean},
                item_columns=cols.items,
                collection_item_columns=cols.collection_items,
            )
            steps.append(f"add_to_collection:{clean}")
        return steps

    def _apply_materialization_tags(
        self,
        conn: sqlite3.Connection,
        *,
        new_item_key: str,
        tags: list[str],
        provenance_tag: str | None,
        cols: WriteColumns,
    ) -> None:
        """Apply the item's tags, appending the provenance auto-tag when provided."""
        all_tags = list(self._normalize_tags(tags))
        if provenance_tag and provenance_tag not in all_tags:
            all_tags.append(provenance_tag)
        self._apply_tag_change(
            conn,
            item_key=new_item_key,
            payload={"add_tags": all_tags, "remove_tags": []},
            item_columns=cols.items,
            tag_columns=cols.tags,
            item_tag_columns=cols.item_tags,
        )

    def _add_inbox_collection(
        self,
        conn: sqlite3.Connection,
        new_item_key: str,
        inbox_collection_name: str,
        cols: WriteColumns,
    ) -> str:
        """Add the Inbox link, creating that workflow collection when absent."""
        payload = {"collection_path": inbox_collection_name}
        if self._find_collection_id(conn, "", inbox_collection_name) is None:
            self._ensure_collection(conn, inbox_collection_name, cols.collections)
        self._apply_collection_change(
            conn,
            item_key=new_item_key,
            payload=payload,
            item_columns=cols.items,
            collection_item_columns=cols.collection_items,
        )
        return "add_to_inbox"

    def _materialize_in_conn(
        self,
        conn: sqlite3.Connection,
        new_item_key: str,
        feed_payload: dict[str, Any],
        inbox_collection_name: str,
        tags: list[str],
        note_title: str = "",
        note_html: str = "",
        matched_collections: list[str] | None = None,
        provenance_tag: str | None = None,
    ) -> list[str]:
        """Apply the item, collection, tag, and note steps on one transaction."""
        if resolve_user_library_item_id(conn, new_item_key, required=False) is not None:
            return []  # A committed operation includes all steps, even if the user later edits it.
        cols = read_write_columns(lambda table: self._table_columns(conn, table))
        self._apply_create_item_from_feed(
            conn, new_item_key=new_item_key, payload=feed_payload, cols=cols
        )
        steps = [
            "create_item",
            self._add_inbox_collection(conn, new_item_key, inbox_collection_name, cols),
        ]
        steps.extend(
            self._add_matched_collections(
                conn,
                new_item_key=new_item_key,
                matched_collections=matched_collections,
                inbox_collection_name=inbox_collection_name,
                cols=cols,
            )
        )
        self._apply_materialization_tags(
            conn,
            new_item_key=new_item_key,
            tags=tags,
            provenance_tag=provenance_tag,
            cols=cols,
        )
        steps.append("apply_tags")
        if note_html and note_html.strip():
            self._apply_note_change(
                conn,
                item_key=new_item_key,
                payload={"note_title": note_title, "note_html": note_html},
                item_columns=cols.items,
                note_columns=cols.item_notes,
            )
            steps.append("add_note")
        return steps

    def _run_feed_materialization(
        self,
        new_item_key: str,
        feed_payload: dict[str, Any],
        inbox_collection_name: str,
        tags: list[str],
        note_title: str = "",
        note_html: str = "",
        matched_collections: list[str] | None = None,
        provenance_tag: str | None = None,
        backup_path: str | None = None,
    ) -> dict[str, Any]:
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=15000")
            conn.execute("BEGIN IMMEDIATE")
            steps = self._materialize_in_conn(
                conn,
                new_item_key,
                feed_payload,
                inbox_collection_name,
                tags,
                note_title,
                note_html,
                matched_collections,
                provenance_tag,
            )
            conn.commit()
            if backup_path is not None:
                self._prune_backups()
            return {
                "item_key": new_item_key,
                "applied_steps": steps,
                "backup_path": backup_path,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def apply_feed_materialization(
        self,
        *,
        new_item_key: str,
        feed_payload: dict[str, Any],
        inbox_collection_name: str,
        tags: list[str],
        note_title: str,
        note_html: str,
        matched_collections: list[str] | None = None,
        provenance_tag: str | None = None,
        create_backup: bool = True,
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
        if self.is_connector_running():
            raise ZoteroWriteError(
                "Zotero is running; close it before adding items to protect its live state"
            )

        backup_path: str | None = None
        if create_backup:
            backup_path = self.backup_database()

        try:
            return self._retry_on_lock(
                lambda: self._run_feed_materialization(
                    new_item_key,
                    feed_payload,
                    inbox_collection_name,
                    tags,
                    note_title,
                    note_html,
                    matched_collections,
                    provenance_tag,
                    backup_path,
                ),
                ctx="apply_feed_materialization",
            )
        except sqlite3.OperationalError as exc:
            raise ZoteroWriteError(
                f"Zotero DB locked after retries — item stays in triaged_pending for next selection run: {exc}"
            ) from exc
