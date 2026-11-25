"""Lookup methods of ZoteroReader (find / membership / tags) — mixin."""
from __future__ import annotations

import re
import sqlite3  # noqa: F401  (type hints)
from typing import Any  # noqa: F401

from zotero_summarizer.domain import normalize_arxiv_id, normalize_doi
from zotero_summarizer.integrations._zotero_read_common import (
    _NON_BIBLIOGRAPHIC_TYPES_SQL,
    _field_value_sql,
    _USER_LIBRARY_ID_SELECT,
)


_LIVE_FIELD_ROWS = f"""
    FROM items i
    JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
    JOIN itemData id ON id.itemID = i.itemID
    JOIN fields f ON f.fieldID = id.fieldID
    JOIN itemDataValues v ON v.valueID = id.valueID
    LEFT JOIN deletedItems di ON di.itemID = i.itemID
    WHERE i.libraryID = ? AND di.itemID IS NULL
      AND it.typeName NOT IN ({_NON_BIBLIOGRAPHIC_TYPES_SQL})
"""
_EARLIEST_FIRST = "ORDER BY i.dateAdded, i.itemID"
_ARXIV_ID_RE = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-z\-]+(?:\.[a-z]{2})?/\d{7})$")
_MIN_TITLE_CHARS = 12
_YEAR_RE = re.compile(r"(?<!\d)((?:15|16|17|18|19|20)\d\d)(?!\d)")


def _doi_match(conn: sqlite3.Connection, lib: int, doi: str) -> str | None:
    # Feed DOI may be `https://doi.org/10.x` while Zotero stores bare `10.x` (or vice
    # versa): match every common stored variant of the normalized DOI.
    bare = normalize_doi(doi)
    if not bare:
        return None
    variants = [bare, f"doi:{bare}", f"https://doi.org/{bare}", f"http://doi.org/{bare}"]
    row = conn.execute(
        f"SELECT i.key {_LIVE_FIELD_ROWS} AND f.fieldName = 'DOI' "
        f"AND lower(v.value) IN ({','.join('?' for _ in variants)}) {_EARLIEST_FIRST} LIMIT 1",
        (lib, *variants),
    ).fetchone()
    return str(row["key"]) if row else None


def _arxiv_match(conn: sqlite3.Connection, lib: int, arxiv_id: str) -> str | None:
    """Exact arXiv id (not a substring of a longer id/DOI): the LIKE only pre-filters."""
    want = normalize_arxiv_id(arxiv_id)
    if not _ARXIV_ID_RE.match(want):
        return None
    mention = re.compile(rf"(?<![0-9a-z-]){re.escape(want)}(?:v\d+)?(?![0-9])")
    rows = conn.execute(
        f"SELECT i.key, f.fieldName AS field, v.value AS value {_LIVE_FIELD_ROWS} "
        f"AND f.fieldName IN ('url', 'DOI', 'extra', 'archiveID') AND lower(v.value) LIKE ? "
        f"{_EARLIEST_FIRST}",
        (lib, f"%{want}%"),
    )
    for row in rows:
        value = str(row["value"]).lower()
        if ("arxiv" in value or row["field"] == "archiveID") and mention.search(value):
            return str(row["key"])
    return None


def _title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())


def _title_year_match(conn: sqlite3.Connection, lib: int, title: str, year: int) -> str | None:
    want = _title_key(title)
    if len(want) < _MIN_TITLE_CHARS:
        return None
    rows = conn.execute(
        f"SELECT i.key, v.value AS title, {_field_value_sql('date')} AS date "
        f"{_LIVE_FIELD_ROWS} AND f.fieldName = 'title' {_EARLIEST_FIRST}",
        (lib,),
    )
    for row in rows:
        stored = _YEAR_RE.search(str(row["date"]))
        if stored and abs(int(stored.group(1)) - year) <= 1 and _title_key(row["title"]) == want:
            return str(row["key"])
    return None


class ZoteroLookupMixin:
    @staticmethod
    def _bibliographic_item_row(conn: sqlite3.Connection, item_key: str) -> sqlite3.Row | None:
        """The live user-library bibliographic item row for ``item_key`` (else None)."""
        return conn.execute(
            f"""
            SELECT i.itemID, i.key, i.dateAdded, i.dateModified, i.libraryID, it.typeName AS item_type
            FROM items i
            JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
            LEFT JOIN deletedItems di ON di.itemID = i.itemID
            WHERE i.key = ?
              AND i.libraryID = ({_USER_LIBRARY_ID_SELECT})
              AND di.itemID IS NULL
              AND it.typeName NOT IN ({_NON_BIBLIOGRAPHIC_TYPES_SQL})
            LIMIT 1
            """,
            (item_key,),
        ).fetchone()

    def get_pdf_paths(self, item_keys: list[str]) -> dict[str, str | None]:
        """First resolved PDF path per item key (== ``get_item_detail(key)["pdf_path"]``,
        ``None`` for an unknown key) over one connection, without reading fields,
        notes, tags or annotations."""

        def _read(conn: sqlite3.Connection) -> dict[str, str | None]:
            rows = {key: self._bibliographic_item_row(conn, key) for key in item_keys}
            return {key: self._read_attachments(conn, int(row["itemID"]))[1] if row else None
                    for key, row in rows.items()}

        return self._execute_read(_read)

    def find_by_external_id(
        self, doi: str | None = None, arxiv_id: str | None = None,
        *, title: str | None = None, year: int | None = None,
    ) -> str | None:
        """Key of the EARLIEST live user-library item that is the same paper, else None.

        Used before creating an item (feed ingest filter, Search coverage, every Add).
        Tried in order: DOI (bare/``doi:``/URL variants) → exact arXiv id (version
        stripped; read from url/DOI incl. ``10.48550/arxiv.<id>``/Extra/archiveID) →
        normalized title + year (±1, both required, title ≥ 12 chars). Trashed items
        and attachments/notes never match.
        """
        if not (doi or arxiv_id or (title and year)):
            return None

        def _read(conn: sqlite3.Connection) -> str | None:
            user_lib_row = conn.execute(_USER_LIBRARY_ID_SELECT).fetchone()
            if not user_lib_row:
                return None
            lib = int(user_lib_row["libraryID"])
            return (
                (_doi_match(conn, lib, doi) if doi else None)
                or (_arxiv_match(conn, lib, arxiv_id) if arxiv_id else None)
                or (_title_year_match(conn, lib, title, year) if title and year else None)
            )

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
                f"SELECT itemID FROM items "
                f"WHERE key = ? AND libraryID = ({_USER_LIBRARY_ID_SELECT}) LIMIT 1",
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
                f"""
                SELECT t.name, COUNT(DISTINCT it.itemID) AS item_count
                FROM tags t
                JOIN itemTags it ON it.tagID = t.tagID
                JOIN items i ON i.itemID = it.itemID
                JOIN itemTypes typ ON typ.itemTypeID = i.itemTypeID
                LEFT JOIN deletedItems di ON di.itemID = i.itemID
                WHERE di.itemID IS NULL
                  AND i.libraryID = ({_USER_LIBRARY_ID_SELECT})
                  AND typ.typeName NOT IN ({_NON_BIBLIOGRAPHIC_TYPES_SQL})
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

    def get_field_values(self, field_name: str) -> dict[str, str]:
        """``{item_key: value}`` for ONE Zotero field across the whole library —
        only items that actually carry the field. One JOIN query (no per-item IN
        clause, so no SQL-variable-limit risk). Used to read current Call Numbers
        before a rank write so we never overwrite a user's own (non-``zr``) value."""

        def _read(conn: sqlite3.Connection) -> dict[str, str]:
            rows = conn.execute(
                f"""
                SELECT i.key AS item_key, v.value AS value
                FROM items i
                JOIN itemData id ON id.itemID = i.itemID
                JOIN fields f ON f.fieldID = id.fieldID
                JOIN itemDataValues v ON v.valueID = id.valueID
                WHERE f.fieldName = ?
                  AND i.libraryID = ({_USER_LIBRARY_ID_SELECT})
                """,
                (field_name,),
            ).fetchall()
            return {str(row["item_key"]): str(row["value"] or "") for row in rows}

        return self._execute_read(_read)
