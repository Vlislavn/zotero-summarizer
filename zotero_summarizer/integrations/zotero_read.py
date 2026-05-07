from __future__ import annotations

import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from zotero_summarizer.domain import is_valid_reading_priority


class ZoteroReadError(RuntimeError):
    """Raised when reading from the local Zotero database fails."""


class ZoteroReader:
    """Read-only adapter over Zotero's local SQLite database."""

    _RETRY_DELAYS_SECONDS = (0.0, 0.05)
    _SQLITE_TIMEOUT_SECONDS = 0.2

    def __init__(self, zotero_data_dir: str | Path | None = None) -> None:
        data_dir = Path(zotero_data_dir or (Path.home() / "Zotero")).expanduser().resolve()
        db_path = data_dir / "zotero.sqlite"
        storage_dir = data_dir / "storage"

        if not data_dir.exists():
            raise ZoteroReadError(f"Zotero data directory not found: {data_dir}")
        if not db_path.exists():
            raise ZoteroReadError(f"Zotero database not found: {db_path}")

        self.data_dir = data_dir
        self.db_path = db_path
        self.storage_dir = storage_dir

    def get_library_stats(self) -> dict[str, Any]:
        """Return high-level counts for the local Zotero library."""
        query_items = """
            SELECT COUNT(*) AS value
            FROM items i
            JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
            LEFT JOIN deletedItems di ON di.itemID = i.itemID
            WHERE di.itemID IS NULL
              AND it.typeName NOT IN ('attachment', 'note')
        """
        query_collections = "SELECT COUNT(*) AS value FROM collections"
        query_tags = "SELECT COUNT(*) AS value FROM tags"
        query_items_with_pdf = """
            SELECT COUNT(DISTINCT ia.parentItemID) AS value
            FROM itemAttachments ia
            JOIN items parent ON parent.itemID = ia.parentItemID
            JOIN itemTypes it ON it.itemTypeID = parent.itemTypeID
            LEFT JOIN deletedItems di ON di.itemID = parent.itemID
            WHERE ia.parentItemID IS NOT NULL
              AND lower(COALESCE(ia.contentType, '')) = 'application/pdf'
              AND di.itemID IS NULL
              AND it.typeName NOT IN ('attachment', 'note')
        """

        def _read(conn: sqlite3.Connection) -> dict[str, Any]:
            total_items = int(conn.execute(query_items).fetchone()["value"])
            total_collections = int(conn.execute(query_collections).fetchone()["value"])
            total_tags = int(conn.execute(query_tags).fetchone()["value"])
            items_with_pdf = int(conn.execute(query_items_with_pdf).fetchone()["value"])
            return {
                "total_items": total_items,
                "total_collections": total_collections,
                "total_tags": total_tags,
                "items_with_pdf": items_with_pdf,
            }

        return self._execute_read(_read)

    def get_collections(self) -> list[dict[str, Any]]:
        """Return the collection tree with per-collection item counts."""

        def _read(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            collection_rows = conn.execute(
                """
                SELECT collectionID, key, collectionName, parentCollectionID
                FROM collections
                """
            ).fetchall()
            count_rows = conn.execute(
                """
                SELECT ci.collectionID, COUNT(ci.itemID) AS item_count
                FROM collectionItems ci
                JOIN items i ON i.itemID = ci.itemID
                JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
                LEFT JOIN deletedItems di ON di.itemID = i.itemID
                WHERE di.itemID IS NULL
                  AND it.typeName NOT IN ('attachment', 'note')
                GROUP BY ci.collectionID
                """
            ).fetchall()

            count_by_collection_id = {
                int(row["collectionID"]): int(row["item_count"]) for row in count_rows
            }

            nodes: dict[int, dict[str, Any]] = {}
            roots: list[dict[str, Any]] = []
            for row in collection_rows:
                collection_id = int(row["collectionID"])
                nodes[collection_id] = {
                    "collection_id": collection_id,
                    "key": str(row["key"]),
                    "name": str(row["collectionName"] or ""),
                    "parent_collection_id": row["parentCollectionID"],
                    "item_count": count_by_collection_id.get(collection_id, 0),
                    "children": [],
                }

            for node in nodes.values():
                parent_id_raw = node["parent_collection_id"]
                if parent_id_raw is None:
                    roots.append(node)
                    continue
                parent_id = int(parent_id_raw)
                parent = nodes.get(parent_id)
                if parent is None:
                    roots.append(node)
                else:
                    parent["children"].append(node)

            self._sort_collection_nodes(roots)
            return roots

        return self._execute_read(_read)

    def get_tags(self, limit: int = 500) -> list[dict[str, Any]]:
        """Return library tags with usage counts on regular items."""
        safe_limit = max(1, min(limit, 5000))

        def _read(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = conn.execute(
                """
                SELECT t.name, COUNT(DISTINCT it.itemID) AS item_count
                FROM tags t
                JOIN itemTags it ON it.tagID = t.tagID
                JOIN items i ON i.itemID = it.itemID
                JOIN itemTypes typ ON typ.itemTypeID = i.itemTypeID
                LEFT JOIN deletedItems di ON di.itemID = i.itemID
                WHERE di.itemID IS NULL
                  AND typ.typeName NOT IN ('attachment', 'note')
                GROUP BY t.tagID
                ORDER BY item_count DESC, t.name ASC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
            return [
                {
                    "tag": str(row["name"] or ""),
                    "item_count": int(row["item_count"] or 0),
                }
                for row in rows
            ]

        return self._execute_read(_read)

    def get_items(
        self,
        collection_key: str | None = None,
        search: str | None = None,
        tag: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return paginated top-level library items with tags and PDF hints."""
        safe_limit = max(1, min(limit, 500))
        safe_offset = max(0, offset)
        where_clauses = [
            "di.itemID IS NULL",
            "it.typeName NOT IN ('attachment', 'note')",
        ]
        params: list[Any] = []

        if collection_key:
            where_clauses.append(
                """
                EXISTS (
                    SELECT 1
                    FROM collectionItems ci
                    JOIN collections c ON c.collectionID = ci.collectionID
                    WHERE ci.itemID = i.itemID AND c.key = ?
                )
                """
            )
            params.append(collection_key)

        if tag:
            where_clauses.append(
                """
                EXISTS (
                    SELECT 1
                    FROM itemTags itg
                    JOIN tags t ON t.tagID = itg.tagID
                    WHERE itg.itemID = i.itemID AND lower(t.name) = lower(?)
                )
                """
            )
            params.append(tag)

        if search and search.strip():
            token = f"%{search.strip().lower()}%"
            where_clauses.append(
                """
                (
                    lower(COALESCE((
                        SELECT v.value
                        FROM itemData id
                        JOIN fields f ON f.fieldID = id.fieldID
                        JOIN itemDataValues v ON v.valueID = id.valueID
                        WHERE id.itemID = i.itemID AND f.fieldName = 'title'
                        LIMIT 1
                    ), '')) LIKE ?
                    OR lower(COALESCE((
                        SELECT v.value
                        FROM itemData id
                        JOIN fields f ON f.fieldID = id.fieldID
                        JOIN itemDataValues v ON v.valueID = id.valueID
                        WHERE id.itemID = i.itemID AND f.fieldName = 'abstractNote'
                        LIMIT 1
                    ), '')) LIKE ?
                    OR lower(COALESCE((
                        SELECT group_concat(t.name, ' ')
                        FROM itemTags itg
                        JOIN tags t ON t.tagID = itg.tagID
                        WHERE itg.itemID = i.itemID
                    ), '')) LIKE ?
                )
                """
            )
            params.extend([token, token, token])

        where_sql = " AND ".join(where_clauses)

        count_sql = f"""
            SELECT COUNT(*) AS total
            FROM items i
            JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
            LEFT JOIN deletedItems di ON di.itemID = i.itemID
            WHERE {where_sql}
        """

        list_sql = f"""
            SELECT
                i.itemID,
                i.key AS item_key,
                i.dateAdded,
                i.dateModified,
                COALESCE((
                    SELECT v.value
                    FROM itemData id
                    JOIN fields f ON f.fieldID = id.fieldID
                    JOIN itemDataValues v ON v.valueID = id.valueID
                    WHERE id.itemID = i.itemID AND f.fieldName = 'title'
                    LIMIT 1
                ), '') AS title,
                COALESCE((
                    SELECT v.value
                    FROM itemData id
                    JOIN fields f ON f.fieldID = id.fieldID
                    JOIN itemDataValues v ON v.valueID = id.valueID
                    WHERE id.itemID = i.itemID AND f.fieldName = 'abstractNote'
                    LIMIT 1
                ), '') AS abstract,
                COALESCE((
                    SELECT v.value
                    FROM itemData id
                    JOIN fields f ON f.fieldID = id.fieldID
                    JOIN itemDataValues v ON v.valueID = id.valueID
                    WHERE id.itemID = i.itemID AND f.fieldName = 'date'
                    LIMIT 1
                ), '') AS publication_date,
                COALESCE((
                    SELECT group_concat(author_name, '; ')
                    FROM (
                        SELECT
                            CASE
                                WHEN c.fieldMode = 1 THEN COALESCE(c.lastName, '')
                                ELSE trim(COALESCE(c.firstName, '') || ' ' || COALESCE(c.lastName, ''))
                            END AS author_name
                        FROM itemCreators ic
                        JOIN creators c ON c.creatorID = ic.creatorID
                        WHERE ic.itemID = i.itemID
                        ORDER BY ic.orderIndex
                    )
                ), '') AS authors,
                COALESCE((
                    SELECT group_concat(t.name, '|||')
                    FROM itemTags itg
                    JOIN tags t ON t.tagID = itg.tagID
                    WHERE itg.itemID = i.itemID
                ), '') AS tag_blob,
                COALESCE((
                    SELECT group_concat(c.collectionName, '|||')
                    FROM collectionItems ci
                    JOIN collections c ON c.collectionID = ci.collectionID
                    WHERE ci.itemID = i.itemID
                ), '') AS collection_blob,
                COALESCE((
                    SELECT ai.key
                    FROM itemAttachments ia
                    JOIN items ai ON ai.itemID = ia.itemID
                    WHERE ia.parentItemID = i.itemID
                      AND lower(COALESCE(ia.contentType, '')) = 'application/pdf'
                    ORDER BY ai.dateAdded DESC
                    LIMIT 1
                ), '') AS pdf_attachment_key,
                COALESCE((
                    SELECT ia.path
                    FROM itemAttachments ia
                    JOIN items ai ON ai.itemID = ia.itemID
                    WHERE ia.parentItemID = i.itemID
                      AND lower(COALESCE(ia.contentType, '')) = 'application/pdf'
                    ORDER BY ai.dateAdded DESC
                    LIMIT 1
                ), '') AS pdf_attachment_path
            FROM items i
            JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
            LEFT JOIN deletedItems di ON di.itemID = i.itemID
            WHERE {where_sql}
            ORDER BY i.dateModified DESC
            LIMIT ? OFFSET ?
        """

        def _read(conn: sqlite3.Connection) -> dict[str, Any]:
            total = int(conn.execute(count_sql, params).fetchone()["total"])
            rows = conn.execute(list_sql, [*params, safe_limit, safe_offset]).fetchall()
            items: list[dict[str, Any]] = []
            for row in rows:
                pdf_path = self._resolve_attachment_path(
                    attachment_key=str(row["pdf_attachment_key"] or ""),
                    stored_path=str(row["pdf_attachment_path"] or ""),
                )
                tags = self._split_blob(str(row["tag_blob"] or ""))
                items.append(
                    {
                        "item_key": str(row["item_key"]),
                        "title": str(row["title"] or "Untitled"),
                        "authors": str(row["authors"] or ""),
                        "publication_date": str(row["publication_date"] or ""),
                        "abstract": str(row["abstract"] or ""),
                        "tags": tags,
                        "collections": self._split_blob(str(row["collection_blob"] or "")),
                        "reading_priority": self._priority_from_tags(tags),
                        "has_pdf": bool(pdf_path),
                        "pdf_path": pdf_path,
                        "date_added": str(row["dateAdded"] or ""),
                        "date_modified": str(row["dateModified"] or ""),
                    }
                )

            return {
                "items": items,
                "total": total,
                "limit": safe_limit,
                "offset": safe_offset,
            }

        return self._execute_read(_read)

    def get_item_notes(self, item_key: str) -> list[dict[str, Any]]:
        """Return child notes for a specific parent item key."""

        def _read(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            parent = conn.execute("SELECT itemID FROM items WHERE key = ?", (item_key,)).fetchone()
            if not parent:
                return []
            parent_item_id = int(parent["itemID"])
            rows = conn.execute(
                """
                SELECT child.key AS note_key, child.dateAdded, child.dateModified, n.note
                FROM itemNotes n
                JOIN items child ON child.itemID = n.itemID
                WHERE n.parentItemID = ?
                ORDER BY child.dateModified DESC
                """,
                (parent_item_id,),
            ).fetchall()
            return [
                {
                    "note_key": str(row["note_key"]),
                    "note": str(row["note"] or ""),
                    "date_added": str(row["dateAdded"] or ""),
                    "date_modified": str(row["dateModified"] or ""),
                }
                for row in rows
            ]

        return self._execute_read(_read)

    def get_item_detail(self, item_key: str) -> dict[str, Any] | None:
        """Return rich metadata, notes, tags, collections, and attachments for one item."""

        def _read(conn: sqlite3.Connection) -> dict[str, Any] | None:
            item_row = conn.execute(
                """
                SELECT i.itemID, i.key, i.dateAdded, i.dateModified, i.libraryID
                FROM items i
                JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
                LEFT JOIN deletedItems di ON di.itemID = i.itemID
                WHERE i.key = ?
                  AND di.itemID IS NULL
                  AND it.typeName NOT IN ('attachment', 'note')
                LIMIT 1
                """,
                (item_key,),
            ).fetchone()
            if not item_row:
                return None

            item_id = int(item_row["itemID"])
            fields_rows = conn.execute(
                """
                SELECT f.fieldName, v.value
                FROM itemData id
                JOIN fields f ON f.fieldID = id.fieldID
                JOIN itemDataValues v ON v.valueID = id.valueID
                WHERE id.itemID = ?
                """,
                (item_id,),
            ).fetchall()
            fields = {str(row["fieldName"]): str(row["value"] or "") for row in fields_rows}

            author_rows = conn.execute(
                """
                SELECT c.firstName, c.lastName, c.fieldMode
                FROM itemCreators ic
                JOIN creators c ON c.creatorID = ic.creatorID
                WHERE ic.itemID = ?
                ORDER BY ic.orderIndex
                """,
                (item_id,),
            ).fetchall()
            authors = []
            for row in author_rows:
                if int(row["fieldMode"] or 0) == 1:
                    name = str(row["lastName"] or "").strip()
                else:
                    name = (f"{str(row['firstName'] or '').strip()} {str(row['lastName'] or '').strip()}").strip()
                if name:
                    authors.append(name)

            tag_rows = conn.execute(
                """
                SELECT t.name
                FROM itemTags it
                JOIN tags t ON t.tagID = it.tagID
                WHERE it.itemID = ?
                ORDER BY t.name COLLATE NOCASE ASC
                """,
                (item_id,),
            ).fetchall()
            tags = [str(row["name"] or "") for row in tag_rows if str(row["name"] or "").strip()]

            collection_rows = conn.execute(
                """
                SELECT c.collectionID, c.key, c.collectionName
                FROM collectionItems ci
                JOIN collections c ON c.collectionID = ci.collectionID
                WHERE ci.itemID = ?
                """,
                (item_id,),
            ).fetchall()
            collection_map = self._load_collection_map(conn)
            collections = []
            for row in collection_rows:
                collection_id = int(row["collectionID"])
                collections.append(
                    {
                        "key": str(row["key"]),
                        "name": str(row["collectionName"] or ""),
                        "path": self._collection_path(collection_id, collection_map),
                    }
                )

            attachment_rows = conn.execute(
                """
                SELECT ai.key AS attachment_key, ai.dateAdded, ai.dateModified,
                       ia.path, ia.contentType, ia.linkMode
                FROM itemAttachments ia
                JOIN items ai ON ai.itemID = ia.itemID
                WHERE ia.parentItemID = ?
                ORDER BY ai.dateAdded ASC
                """,
                (item_id,),
            ).fetchall()
            attachments = []
            pdf_path = None
            for row in attachment_rows:
                attachment_key = str(row["attachment_key"] or "")
                resolved_path = self._resolve_attachment_path(
                    attachment_key=attachment_key,
                    stored_path=str(row["path"] or ""),
                )
                is_pdf = str(row["contentType"] or "").lower() == "application/pdf"
                if is_pdf and resolved_path and pdf_path is None:
                    pdf_path = resolved_path
                attachments.append(
                    {
                        "attachment_key": attachment_key,
                        "content_type": str(row["contentType"] or ""),
                        "link_mode": row["linkMode"],
                        "stored_path": str(row["path"] or ""),
                        "resolved_path": resolved_path,
                        "exists": bool(resolved_path and Path(resolved_path).exists()),
                        "date_added": str(row["dateAdded"] or ""),
                        "date_modified": str(row["dateModified"] or ""),
                    }
                )

            note_rows = conn.execute(
                """
                SELECT child.key AS note_key, child.dateAdded, child.dateModified, n.note
                FROM itemNotes n
                JOIN items child ON child.itemID = n.itemID
                WHERE n.parentItemID = ?
                ORDER BY child.dateModified DESC
                """,
                (item_id,),
            ).fetchall()
            notes = [
                {
                    "note_key": str(row["note_key"]),
                    "note": str(row["note"] or ""),
                    "date_added": str(row["dateAdded"] or ""),
                    "date_modified": str(row["dateModified"] or ""),
                }
                for row in note_rows
            ]
            return {
                "item_key": str(item_row["key"]),
                "title": fields.get("title", "Untitled"),
                "abstract": fields.get("abstractNote", ""),
                "publication_date": fields.get("date", ""),
                "doi": fields.get("DOI", ""),
                "url": fields.get("url", ""),
                "authors": authors,
                "tags": tags,
                "collections": collections,
                "notes": notes,
                "attachments": attachments,
                "pdf_path": pdf_path,
                "has_pdf": pdf_path is not None,
                "reading_priority": self._priority_from_tags(tags),
                "date_added": str(item_row["dateAdded"] or ""),
                "date_modified": str(item_row["dateModified"] or ""),
            }

        return self._execute_read(_read)

    def get_pdf_path(self, item_key: str) -> str | None:
        """Return the first local PDF path for an item, if available."""
        detail = self.get_item_detail(item_key)
        if not detail:
            return None
        return str(detail.get("pdf_path") or "") or None

    def _connect(self) -> sqlite3.Connection:
        return self._connect_db(self.db_path)

    def _connect_db(self, db_path: Path) -> sqlite3.Connection:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=self._SQLITE_TIMEOUT_SECONDS)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        conn.execute(f"PRAGMA busy_timeout = {int(self._SQLITE_TIMEOUT_SECONDS * 1000)}")
        return conn

    def _execute_read(self, fn):
        last_error: Exception | None = None
        for delay in self._RETRY_DELAYS_SECONDS:
            if delay > 0:
                time.sleep(delay)
            try:
                conn = self._connect()
                try:
                    return fn(conn)
                finally:
                    conn.close()
            except sqlite3.OperationalError as exc:
                last_error = exc
                if self._is_busy_error(exc):
                    continue
                raise ZoteroReadError(f"Failed to query Zotero DB: {exc}") from exc
            except sqlite3.Error as exc:
                raise ZoteroReadError(f"Failed to query Zotero DB: {exc}") from exc
        if last_error is not None:
            try:
                return self._execute_snapshot_read(fn)
            except ZoteroReadError:
                raise ZoteroReadError(f"Zotero DB is busy: {last_error}") from last_error
        raise ZoteroReadError("Unable to query Zotero DB")

    def _execute_snapshot_read(self, fn):
        with tempfile.TemporaryDirectory(prefix="zotero-snapshot-") as tmp_dir:
            snapshot_dir = Path(tmp_dir)
            snapshot_db_path = snapshot_dir / self.db_path.name
            self._copy_database_snapshot(snapshot_db_path)
            conn = self._connect_db(snapshot_db_path)
            try:
                return fn(conn)
            except sqlite3.Error as exc:
                raise ZoteroReadError(f"Failed to query Zotero snapshot DB: {exc}") from exc
            finally:
                conn.close()

    def _copy_database_snapshot(self, snapshot_db_path: Path) -> None:
        for suffix in ("", "-wal", "-shm", "-journal"):
            source_path = Path(f"{self.db_path}{suffix}")
            if not source_path.exists():
                continue
            target_path = Path(f"{snapshot_db_path}{suffix}")
            shutil.copy2(source_path, target_path)

    @staticmethod
    def _is_busy_error(exc: sqlite3.OperationalError) -> bool:
        message = str(exc).lower()
        return "locked" in message or "busy" in message

    def _load_collection_map(self, conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT collectionID, collectionName, parentCollectionID
            FROM collections
            """
        ).fetchall()
        return {
            int(row["collectionID"]): {
                "name": str(row["collectionName"] or ""),
                "parent": row["parentCollectionID"],
            }
            for row in rows
        }

    def _collection_path(self, collection_id: int, collection_map: dict[int, dict[str, Any]]) -> str:
        parts: list[str] = []
        seen: set[int] = set()
        current_id: int | None = collection_id
        while current_id is not None and current_id not in seen:
            seen.add(current_id)
            node = collection_map.get(current_id)
            if not node:
                break
            name = str(node.get("name") or "").strip()
            if name:
                parts.append(name)
            parent = node.get("parent")
            current_id = int(parent) if parent is not None else None
        return " > ".join(reversed(parts))

    @staticmethod
    def _sort_collection_nodes(nodes: list[dict[str, Any]]) -> None:
        nodes.sort(key=lambda node: str(node.get("name") or "").lower())
        for node in nodes:
            ZoteroReader._sort_collection_nodes(node.get("children", []))

    @staticmethod
    def _split_blob(blob: str) -> list[str]:
        if not blob:
            return []
        return [part.strip() for part in blob.split("|||") if part.strip()]

    @staticmethod
    def _priority_from_tags(tags: list[str]) -> str | None:
        for tag in tags:
            if tag.startswith("zs:"):
                value = tag.split(":", 1)[1].strip()
                if is_valid_reading_priority(value):
                    return value
        return None

    def _resolve_attachment_path(self, attachment_key: str, stored_path: str) -> str | None:
        value = (stored_path or "").strip()
        if not value:
            return None

        candidate: Path | None = None
        if value.startswith("storage:"):
            relative_name = value.split(":", 1)[1].strip()
            candidate = self.storage_dir / attachment_key / relative_name
        elif value.startswith("file://"):
            parsed = urlparse(value)
            candidate = Path(unquote(parsed.path))
        else:
            raw_path = Path(value).expanduser()
            if raw_path.is_absolute():
                candidate = raw_path
            elif attachment_key:
                candidate = self.storage_dir / attachment_key / value
            else:
                candidate = self.data_dir / value

        if candidate is None:
            return None
        try:
            return str(candidate.resolve())
        except OSError:
            return str(candidate)
