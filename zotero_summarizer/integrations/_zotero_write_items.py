"""Item creation + feed materialization methods of ZoteroWriter (mixin)."""
from __future__ import annotations

import re
import sqlite3
from typing import Any

from zotero_summarizer.integrations._zotero_write_common import LOGGER, ZoteroWriteError  # noqa: F401


class ZoteroItemWriteMixin:
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
                    tuple(int(i) for i in feed_item_ids),
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
                f"DB still locked after retries — items will remain unread in Zotero until next tick: {exc}"
            ) from exc
        except sqlite3.Error as exc:
            raise ZoteroWriteError(f"Failed to mark feed items read: {exc}") from exc

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
        if self.is_connector_running():
            LOGGER.info(
                "Zotero is running — apply_feed_materialization will retry up to 3× if DB is locked"
            )

        backup_path: str | None = None
        if create_backup:
            backup_path = self.backup_database()

        def _do() -> dict[str, Any]:
            conn = sqlite3.connect(str(self.db_path), timeout=15)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                except sqlite3.Error:
                    pass
                conn.execute("PRAGMA busy_timeout=15000")

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

        try:
            return self._retry_on_lock(_do, ctx="apply_feed_materialization")
        except sqlite3.OperationalError as exc:
            raise ZoteroWriteError(
                f"Zotero DB locked after retries — item stays in triaged_pending for next selection run: {exc}"
            ) from exc
