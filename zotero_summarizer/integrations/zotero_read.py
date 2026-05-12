from __future__ import annotations

import re
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


# Strip C0/C1 control chars (preserving tab/newline/cr) plus Unicode tag chars
# (U+E0000-U+E007F). The tag-char range was infamously used to smuggle invisible
# prompt-injection payloads in 2024 — see Greshake et al. USENIX Security 2024
# (arXiv:2302.12173v3) and Anthropic's Dec 2024 indirect-prompt-injection guidance.
# All feed-supplied strings pass through this on read so the rest of the pipeline
# cannot accidentally hand untrusted control chars to an LLM.
_INJECTION_CHAR_PATTERN = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\U000e0000-\U000e007f]"
)


_ARXIV_RE = re.compile(
    r"(?:arxiv[.:/]|arxiv\.org/(?:abs|pdf)/)([0-9]{4}\.[0-9]{4,5}(?:v\d+)?|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})",
    re.IGNORECASE,
)


def _arxiv_id_from_url_or_doi(url: str, doi: str) -> str:
    """Extract an arXiv ID from a feed item's URL or DOI fields."""
    for value in (url, doi):
        if not value:
            continue
        match = _ARXIV_RE.search(value)
        if match:
            return match.group(1)
    return ""


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

    def get_user_library_id(self) -> int:
        """Return the libraryID of the user's personal library (type='user')."""

        def _read(conn: sqlite3.Connection) -> int:
            row = conn.execute(
                "SELECT libraryID FROM libraries WHERE type='user' LIMIT 1"
            ).fetchone()
            if not row:
                raise ZoteroReadError("No user library found in Zotero database")
            return int(row["libraryID"])

        return self._execute_read(_read)

    def get_feed_groups(self) -> list[dict[str, Any]]:
        """Return every Zotero RSS feed (one library per feed) with metadata.

        Each feed is its own row in the `feeds` table keyed by `libraryID`. The
        return shape mirrors `get_collections()` so the UI / CLI can render them
        the same way.
        """

        def _read(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = conn.execute(
                """
                SELECT
                    f.libraryID,
                    f.name,
                    f.url,
                    f.lastUpdate,
                    f.lastCheck,
                    f.lastCheckError,
                    f.refreshInterval
                FROM feeds f
                JOIN libraries l ON l.libraryID = f.libraryID
                WHERE l.type = 'feed'
                ORDER BY lower(f.name) ASC
                """
            ).fetchall()
            return [
                {
                    "library_id": int(row["libraryID"]),
                    "name": self._sanitize_text(str(row["name"] or "")),
                    "url": self._sanitize_text(str(row["url"] or "")),
                    "last_update": str(row["lastUpdate"] or ""),
                    "last_check": str(row["lastCheck"] or ""),
                    "last_check_error": str(row["lastCheckError"] or "") or None,
                    "refresh_interval_minutes": int(row["refreshInterval"] or 0),
                }
                for row in rows
            ]

        return self._execute_read(_read)

    def get_feed_items(
        self,
        feed_library_id: int | None = None,
        since: str | None = None,
        limit: int = 1000,
        unread_only: bool = False,
        order: str = "newest_first",
    ) -> list[dict[str, Any]]:
        """Return feed items joined with their metadata fields.

        Feed items live in `items` (with a feed libraryID) plus the sparse
        `feedItems` table (which contributes `guid`, `readTime`, `translatedTime`).
        Title / abstract / URL / DOI / date come from `itemData` + `fields`.

        Args:
            feed_library_id: filter to a single feed library. If None, returns
                items from all feed libraries (joined via `libraries.type='feed'`).
            since: optional ISO timestamp (inclusive) — items with `dateAdded >= since`.
                Phase 1.5 daemon ignores --since by default and uses unread_only=True
                instead; this kwarg remains for the one-shot CLI / preview.
            limit: maximum rows returned (capped at 5000).
            unread_only: when True, only return items where `feedItems.readTime IS NULL`.
                This is the Phase 1.5 daemon's canonical work queue.
            order: "newest_first" (default, dateAdded DESC) or "oldest_first"
                (dateAdded ASC, useful for round-robin oldest-unread scan).
        """
        safe_limit = max(1, min(int(limit), 5000))
        where = ["l.type = 'feed'"]
        params: list[Any] = []
        if feed_library_id is not None:
            where.append("i.libraryID = ?")
            params.append(int(feed_library_id))
        if since:
            where.append("i.dateAdded >= ?")
            params.append(str(since))
        if unread_only:
            where.append("fi.readTime IS NULL")
        where_sql = " AND ".join(where)
        order_sql = "i.dateAdded ASC" if order == "oldest_first" else "i.dateAdded DESC"

        def _read(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            sql = f"""
                SELECT
                    i.itemID,
                    i.libraryID AS feed_library_id,
                    l_feed.name AS feed_name,
                    fi.guid,
                    fi.readTime,
                    fi.translatedTime,
                    i.dateAdded,
                    i.dateModified,
                    (
                        SELECT v.value
                        FROM itemData id JOIN fields f ON f.fieldID=id.fieldID
                        JOIN itemDataValues v ON v.valueID=id.valueID
                        WHERE id.itemID=i.itemID AND f.fieldName='title' LIMIT 1
                    ) AS title,
                    (
                        SELECT v.value
                        FROM itemData id JOIN fields f ON f.fieldID=id.fieldID
                        JOIN itemDataValues v ON v.valueID=id.valueID
                        WHERE id.itemID=i.itemID AND f.fieldName='abstractNote' LIMIT 1
                    ) AS abstract,
                    (
                        SELECT v.value
                        FROM itemData id JOIN fields f ON f.fieldID=id.fieldID
                        JOIN itemDataValues v ON v.valueID=id.valueID
                        WHERE id.itemID=i.itemID AND f.fieldName='url' LIMIT 1
                    ) AS url,
                    (
                        SELECT v.value
                        FROM itemData id JOIN fields f ON f.fieldID=id.fieldID
                        JOIN itemDataValues v ON v.valueID=id.valueID
                        WHERE id.itemID=i.itemID AND f.fieldName='DOI' LIMIT 1
                    ) AS doi,
                    (
                        SELECT v.value
                        FROM itemData id JOIN fields f ON f.fieldID=id.fieldID
                        JOIN itemDataValues v ON v.valueID=id.valueID
                        WHERE id.itemID=i.itemID AND f.fieldName='date' LIMIT 1
                    ) AS publication_date,
                    (
                        SELECT v.value
                        FROM itemData id JOIN fields f ON f.fieldID=id.fieldID
                        JOIN itemDataValues v ON v.valueID=id.valueID
                        WHERE id.itemID=i.itemID AND f.fieldName='publicationTitle' LIMIT 1
                    ) AS publication_title,
                    (
                        SELECT group_concat(
                            CASE WHEN c.fieldMode=1
                                 THEN COALESCE(c.lastName, '')
                                 ELSE trim(COALESCE(c.firstName, '')||' '||COALESCE(c.lastName, ''))
                            END,
                            '; '
                        )
                        FROM itemCreators ic JOIN creators c ON c.creatorID=ic.creatorID
                        WHERE ic.itemID=i.itemID
                    ) AS authors,
                    it.typeName AS item_type
                FROM items i
                JOIN libraries l ON l.libraryID=i.libraryID
                JOIN itemTypes it ON it.itemTypeID=i.itemTypeID
                LEFT JOIN feedItems fi ON fi.itemID=i.itemID
                LEFT JOIN feeds l_feed ON l_feed.libraryID=l.libraryID
                WHERE {where_sql}
                ORDER BY {order_sql}
                LIMIT ?
            """
            rows = conn.execute(sql, [*params, safe_limit]).fetchall()
            results: list[dict[str, Any]] = []
            for row in rows:
                arxiv_id = _arxiv_id_from_url_or_doi(
                    str(row["url"] or ""), str(row["doi"] or "")
                )
                results.append(
                    {
                        "item_id": int(row["itemID"]),
                        "feed_library_id": int(row["feed_library_id"]),
                        "feed_name": self._sanitize_text(str(row["feed_name"] or "")),
                        "guid": self._sanitize_text(str(row["guid"] or "")),
                        "title": self._sanitize_text(str(row["title"] or "")),
                        "abstract": self._sanitize_text(str(row["abstract"] or "")),
                        "url": self._sanitize_text(str(row["url"] or "")),
                        "doi": self._sanitize_text(str(row["doi"] or "")),
                        "arxiv_id": arxiv_id,
                        "publication_date": str(row["publication_date"] or ""),
                        "publication_title": self._sanitize_text(
                            str(row["publication_title"] or "")
                        ),
                        "authors": self._sanitize_text(str(row["authors"] or "")),
                        "item_type": str(row["item_type"] or "journalArticle"),
                        "read_time": str(row["readTime"] or "") or None,
                        "date_added": str(row["dateAdded"] or ""),
                        "date_modified": str(row["dateModified"] or ""),
                    }
                )
            return results

        return self._execute_read(_read)

    def find_by_external_id(
        self, doi: str | None = None, arxiv_id: str | None = None
    ) -> str | None:
        """Look up an existing user-library item by DOI or arXiv ID.

        Used for dedup before materializing a feed item: if the paper is already
        in the user's library, skip creating a duplicate.
        Returns the item key if found, else None.
        """
        if not doi and not arxiv_id:
            return None

        def _read(conn: sqlite3.Connection) -> str | None:
            user_lib_row = conn.execute(
                "SELECT libraryID FROM libraries WHERE type='user' LIMIT 1"
            ).fetchone()
            if not user_lib_row:
                return None
            user_lib_id = int(user_lib_row["libraryID"])

            # DOI direct match (Zotero stores DOI in the DOI field)
            if doi:
                doi_norm = doi.strip().lower()
                if doi_norm:
                    row = conn.execute(
                        """
                        SELECT i.key FROM items i
                        JOIN itemData id ON id.itemID=i.itemID
                        JOIN fields f ON f.fieldID=id.fieldID
                        JOIN itemDataValues v ON v.valueID=id.valueID
                        LEFT JOIN deletedItems di ON di.itemID=i.itemID
                        WHERE i.libraryID=? AND f.fieldName='DOI'
                          AND lower(v.value)=?
                          AND di.itemID IS NULL
                        LIMIT 1
                        """,
                        (user_lib_id, doi_norm),
                    ).fetchone()
                    if row:
                        return str(row["key"])

            # arXiv ID: check URL field (Zotero typically stores arXiv as URL)
            if arxiv_id:
                arxiv_norm = arxiv_id.strip().lower()
                if arxiv_norm:
                    pattern = f"%{arxiv_norm}%"
                    row = conn.execute(
                        """
                        SELECT i.key FROM items i
                        JOIN itemData id ON id.itemID=i.itemID
                        JOIN fields f ON f.fieldID=id.fieldID
                        JOIN itemDataValues v ON v.valueID=id.valueID
                        LEFT JOIN deletedItems di ON di.itemID=i.itemID
                        WHERE i.libraryID=? AND f.fieldName IN ('url', 'DOI')
                          AND lower(v.value) LIKE ?
                          AND di.itemID IS NULL
                        LIMIT 1
                        """,
                        (user_lib_id, pattern),
                    ).fetchone()
                    if row:
                        return str(row["key"])
            return None

        return self._execute_read(_read)

    def get_item_membership(self, item_key: str) -> dict[str, Any]:
        """Return collection + trash + engagement-tag membership for one item.

        Used by the Phase 1.5 outcome detector: an item the agent materialized
        N days ago is now queried to see whether the user kept it (collection
        membership), filed it (out of Inbox), trashed it (deletedItems), or
        engaged with it (🧠/👀 tags).

        Returns a dict with:
          - exists (bool): the item still exists in `items`
          - is_trashed (bool): row present in `deletedItems`
          - collection_keys (list[str]): collection keys this item belongs to
          - collection_names (list[str]): collection names (parallel to keys)
          - is_in_inbox (bool): membership in the "Inbox" collection
          - tags (list[str]): every tag on this item
          - has_engagement_tag (bool): any tag containing 🧠 or 👀

        For a hard-deleted item (gone from `items`), returns
        `{"exists": False, "is_trashed": False, "collection_keys": [],
          "collection_names": [], "is_in_inbox": False, "tags": [],
          "has_engagement_tag": False}`.
        """

        def _read(conn: sqlite3.Connection) -> dict[str, Any]:
            row = conn.execute(
                "SELECT itemID FROM items WHERE key = ? LIMIT 1",
                (item_key,),
            ).fetchone()
            if not row:
                return {
                    "exists": False,
                    "is_trashed": False,
                    "collection_keys": [],
                    "collection_names": [],
                    "is_in_inbox": False,
                    "tags": [],
                    "has_engagement_tag": False,
                }
            item_id = int(row["itemID"])
            trashed_row = conn.execute(
                "SELECT 1 FROM deletedItems WHERE itemID = ? LIMIT 1",
                (item_id,),
            ).fetchone()
            is_trashed = trashed_row is not None
            collection_rows = conn.execute(
                """
                SELECT c.key AS collection_key, c.collectionName AS collection_name
                FROM collectionItems ci
                JOIN collections c ON c.collectionID = ci.collectionID
                WHERE ci.itemID = ?
                """,
                (item_id,),
            ).fetchall()
            collection_keys: list[str] = []
            collection_names: list[str] = []
            for crow in collection_rows:
                collection_keys.append(str(crow["collection_key"] or ""))
                collection_names.append(str(crow["collection_name"] or ""))
            is_in_inbox = any(name.casefold() == "inbox" for name in collection_names)
            tag_rows = conn.execute(
                """
                SELECT t.name
                FROM itemTags it
                JOIN tags t ON t.tagID = it.tagID
                WHERE it.itemID = ?
                """,
                (item_id,),
            ).fetchall()
            tags = [str(t["name"] or "") for t in tag_rows if str(t["name"] or "").strip()]
            has_engagement = any("🧠" in t or "👀" in t for t in tags)
            return {
                "exists": True,
                "is_trashed": is_trashed,
                "collection_keys": collection_keys,
                "collection_names": collection_names,
                "is_in_inbox": is_in_inbox,
                "tags": tags,
                "has_engagement_tag": has_engagement,
            }

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

    def _connect_db(self, db_path: Path, *, immutable: bool = False) -> sqlite3.Connection:
        # immutable=1 disables WAL replay and change detection. Safe ONLY for snapshot
        # copies in a temp dir (where the file truly won't change). Never apply to the
        # live Zotero DB while Zotero may be writing — that produces stale reads.
        params = "mode=ro&immutable=1" if immutable else "mode=ro"
        uri = f"file:{db_path}?{params}"
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
            # immutable=1 tells SQLite to skip WAL replay on the snapshot copy. This
            # gives us a consistent point-in-time view even if the source DB's WAL/SHM
            # was mid-flight when we copied — without needing write access to checkpoint.
            conn = self._connect_db(snapshot_db_path, immutable=True)
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
    def _sanitize_text(value: str) -> str:
        """Strip injection-risk control + Unicode tag chars from feed-supplied text.

        Defense layer 1 against indirect prompt injection: feed abstracts go
        directly into the triage LLM prompt. Without this strip, U+E0000-U+E007F
        tag chars or control chars can smuggle hidden instructions past visual
        review. The triage prompt also wraps the content in <untrusted_input>
        tags as layer 2 (see goals.yaml prompts.triage).
        """
        if not value:
            return ""
        return _INJECTION_CHAR_PATTERN.sub("", value)

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
