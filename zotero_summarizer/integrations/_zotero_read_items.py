"""Item-detail methods of ZoteroReader (items / notes / detail / pdf) — mixin."""
from __future__ import annotations

import sqlite3  # noqa: F401  (type hints)
from pathlib import Path  # noqa: F401
from typing import Any  # noqa: F401

from zotero_summarizer.integrations._zotero_read_common import (
    _ANNOTATION_TYPE_NAMES,
    _NON_BIBLIOGRAPHIC_TYPES_SQL,
)


class ZoteroItemsMixin:
    def get_items(
        self,
        collection_key: str | None = None,
        search: str | None = None,
        tag: str | None = None,
        limit: int = 100,
        offset: int = 0,
        include_abstract: bool = True,
    ) -> dict[str, Any]:
        """Return paginated top-level library items with tags and PDF hints.

        ``include_abstract=False`` omits the (often large) abstract correlated
        subquery and returns ``abstract=""`` — used by the whole-library display
        and rank/tag write paths, which never read the abstract, to avoid hauling
        ~megabytes of unused text per scan. The scoring path keeps it True."""
        safe_limit = max(1, min(limit, 500))
        safe_offset = max(0, offset)
        where_clauses = [
            "di.itemID IS NULL",
            f"it.typeName NOT IN ({_NON_BIBLIOGRAPHIC_TYPES_SQL})",
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

        abstract_sql = (
            """COALESCE((
                    SELECT v.value
                    FROM itemData id
                    JOIN fields f ON f.fieldID = id.fieldID
                    JOIN itemDataValues v ON v.valueID = id.valueID
                    WHERE id.itemID = i.itemID AND f.fieldName = 'abstractNote'
                    LIMIT 1
                ), '')"""
            if include_abstract
            else "''"
        )

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
                {abstract_sql} AS abstract,
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
            ORDER BY i.dateModified DESC, i.itemID DESC
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

    def get_all_items(
        self,
        collection_key: str | None = None,
        search: str | None = None,
        tag: str | None = None,
        page_size: int = 500,
        include_abstract: bool = True,
    ) -> dict[str, Any]:
        """Every matching top-level item, paginating internally past ``get_items``'
        500-per-call clamp. Loops ``offset`` by the returned page length, using the
        reported ``total`` (or a short page) as the stop condition. Same row shape as
        ``get_items``; returns ``{items, total}``. Use for whole-library passes
        (full-library scoring, the Zotero rank/tag writes).

        Contract: deterministic order (dateModified DESC, itemID DESC tiebreaker)
        and AT-MOST-ONCE per ``item_key`` — an item is de-duplicated across pages,
        so a concurrent write that shifts a row between pages can't double-emit it
        (completeness is best-effort under concurrent mutation; the at-most-once
        guarantee is what keeps the resulting Zotero ranking unambiguous).
        ``include_abstract=False`` skips the abstract subquery on every page."""
        safe_page = max(1, min(page_size, 500))
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        offset = 0
        while True:
            page = self.get_items(
                collection_key=collection_key, search=search, tag=tag,
                limit=safe_page, offset=offset, include_abstract=include_abstract,
            )
            rows = page.get("items") or []
            for row in rows:
                key = str(row.get("item_key") or "")
                if key and key in seen:
                    continue  # paging artifact under a concurrent write — emit once
                if key:
                    seen.add(key)
                out.append(row)
            offset += len(rows)
            total = int(page.get("total") or 0)
            if not rows or len(rows) < safe_page or offset >= total:
                break
        return {"items": out, "total": len(out)}

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

    @staticmethod
    def _read_item_authors(conn: sqlite3.Connection, item_id: int) -> list[str]:
        """Ordered author display names for an item."""
        rows = conn.execute(
            "SELECT c.firstName, c.lastName, c.fieldMode FROM itemCreators ic "
            "JOIN creators c ON c.creatorID = ic.creatorID "
            "WHERE ic.itemID = ? ORDER BY ic.orderIndex",
            (item_id,),
        ).fetchall()
        authors = []
        for row in rows:
            last = str(row["lastName"] or "").strip()
            first = str(row["firstName"] or "").strip()
            name = last if int(row["fieldMode"] or 0) == 1 else f"{first} {last}".strip()
            if name:
                authors.append(name)
        return authors

    def _read_item_collections(self, conn: sqlite3.Connection, item_id: int) -> list[dict[str, Any]]:
        """An item's collection memberships with resolved paths."""
        rows = conn.execute(
            "SELECT c.collectionID, c.key, c.collectionName FROM collectionItems ci "
            "JOIN collections c ON c.collectionID = ci.collectionID WHERE ci.itemID = ?",
            (item_id,),
        ).fetchall()
        collection_map = self._load_collection_map(conn)
        return [
            {
                "key": str(row["key"]),
                "name": str(row["collectionName"] or ""),
                "path": self._collection_path(int(row["collectionID"]), collection_map),
            }
            for row in rows
        ]

    def _read_attachments(self, conn: sqlite3.Connection, item_id: int) -> tuple[list[dict[str, Any]], str | None]:
        """Read an item's attachments; return (attachments, first resolved PDF path)."""
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
        attachments: list[dict[str, Any]] = []
        pdf_path: str | None = None
        for row in attachment_rows:
            attachment_key = str(row["attachment_key"] or "")
            resolved_path = self._resolve_attachment_path(
                attachment_key=attachment_key, stored_path=str(row["path"] or ""),
            )
            is_pdf = str(row["contentType"] or "").lower() == "application/pdf"
            if is_pdf and resolved_path and pdf_path is None:
                pdf_path = resolved_path
            attachments.append({
                "attachment_key": attachment_key,
                "content_type": str(row["contentType"] or ""),
                "link_mode": row["linkMode"],
                "stored_path": str(row["path"] or ""),
                "resolved_path": resolved_path,
                "exists": bool(resolved_path and Path(resolved_path).exists()),
                "date_added": str(row["dateAdded"] or ""),
                "date_modified": str(row["dateModified"] or ""),
            })
        return attachments, pdf_path

    def get_item_detail(self, item_key: str) -> dict[str, Any] | None:
        """Return rich metadata, notes, tags, collections, and attachments for one item."""

        def _read(conn: sqlite3.Connection) -> dict[str, Any] | None:
            item_row = conn.execute(
                f"""
                SELECT i.itemID, i.key, i.dateAdded, i.dateModified, i.libraryID
                FROM items i
                JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
                LEFT JOIN deletedItems di ON di.itemID = i.itemID
                WHERE i.key = ?
                  AND di.itemID IS NULL
                  AND it.typeName NOT IN ({_NON_BIBLIOGRAPHIC_TYPES_SQL})
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

            authors = self._read_item_authors(conn, item_id)

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

            collections = self._read_item_collections(conn, item_id)

            attachments, pdf_path = self._read_attachments(conn, item_id)

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

            # PDF annotations live in itemAnnotations, keyed by attachment.itemID.
            # The dateAdded column is on items, not itemAnnotations — join items.
            annotation_rows = conn.execute(
                """
                SELECT a.itemID, a.text, a.comment, a.pageLabel, a.type,
                       a.color, ann.dateAdded
                FROM itemAnnotations a
                JOIN items ann ON ann.itemID = a.itemID
                JOIN itemAttachments att ON att.itemID = a.parentItemID
                WHERE att.parentItemID = ?
                ORDER BY ann.dateAdded ASC
                """,
                (item_id,),
            ).fetchall()
            annotations = [
                {
                    "text": str(row["text"] or ""),
                    "comment": str(row["comment"] or ""),
                    "page_label": str(row["pageLabel"] or ""),
                    "type": _ANNOTATION_TYPE_NAMES.get(
                        int(row["type"]), f"unknown_{int(row['type'])}"
                    ),
                    "color": str(row["color"] or ""),
                    "date_added": str(row["dateAdded"] or ""),
                }
                for row in annotation_rows
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
                "annotations": annotations,
                "attachments": attachments,
                "pdf_path": pdf_path,
                "has_pdf": pdf_path is not None,
                "reading_priority": self._priority_from_tags(tags),
                "date_added": str(item_row["dateAdded"] or ""),
                "date_modified": str(item_row["dateModified"] or ""),
            }

        return self._execute_read(_read)
