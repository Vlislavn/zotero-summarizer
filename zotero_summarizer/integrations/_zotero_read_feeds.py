"""Feed-reading methods of ZoteroReader (mixin)."""
from __future__ import annotations

import sqlite3  # noqa: F401  (type hints)
from typing import Any  # noqa: F401

from zotero_summarizer.integrations._zotero_read_common import _arxiv_id_from_url_or_doi


class ZoteroFeedsMixin:
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
