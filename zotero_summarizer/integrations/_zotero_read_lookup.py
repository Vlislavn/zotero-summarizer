"""Lookup methods of ZoteroReader (find / membership / tags) — mixin."""
from __future__ import annotations

import sqlite3  # noqa: F401  (type hints)
from typing import Any  # noqa: F401


class ZoteroLookupMixin:
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
