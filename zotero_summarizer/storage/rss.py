"""App-owned RSS feed and item storage."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from zotero_summarizer.storage.feed_identity import stable_feed_key_from_item


def list_rss_feeds(
    conn: sqlite3.Connection,
    *,
    include_disabled: bool = False,
) -> list[dict[str, Any]]:
    where = "" if include_disabled else "WHERE enabled = 1"
    rows = conn.execute(
        f"""
        SELECT *
        FROM rss_feeds
        {where}
        ORDER BY lower(name) ASC, id ASC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def upsert_rss_feed(
    conn: sqlite3.Connection,
    *,
    name: str,
    url: str,
    enabled: bool = True,
    source: str = "app",
    imported_zotero_library_id: int | None = None,
) -> int:
    safe_name = str(name or "").strip()
    safe_url = str(url or "").strip()
    if not safe_name:
        raise ValueError("feed name is required")
    if not safe_url:
        raise ValueError("feed url is required")
    conn.execute(
        """
        INSERT INTO rss_feeds (name, url, enabled, source, imported_zotero_library_id)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            name = excluded.name,
            enabled = excluded.enabled,
            source = excluded.source,
            imported_zotero_library_id = COALESCE(
                excluded.imported_zotero_library_id,
                rss_feeds.imported_zotero_library_id
            ),
            updated_at = datetime('now')
        """,
        (
            safe_name,
            safe_url,
            1 if enabled else 0,
            str(source or "app").strip() or "app",
            imported_zotero_library_id,
        ),
    )
    row = conn.execute("SELECT id FROM rss_feeds WHERE url = ?", (safe_url,)).fetchone()
    if row is None:
        raise RuntimeError(f"rss_feeds upsert failed for {safe_url!r}")
    return int(row["id"])


def update_rss_feed(
    conn: sqlite3.Connection,
    feed_id: int,
    *,
    name: str | None = None,
    url: str | None = None,
    enabled: bool | None = None,
) -> bool:
    assignments: list[str] = ["updated_at = datetime('now')"]
    params: list[Any] = []
    if name is not None:
        safe_name = str(name or "").strip()
        if not safe_name:
            raise ValueError("feed name is required")
        assignments.append("name = ?")
        params.append(safe_name)
    if url is not None:
        safe_url = str(url or "").strip()
        if not safe_url:
            raise ValueError("feed url is required")
        assignments.append("url = ?")
        params.append(safe_url)
    if enabled is not None:
        assignments.append("enabled = ?")
        params.append(1 if enabled else 0)
    params.append(int(feed_id))
    cursor = conn.execute(
        f"UPDATE rss_feeds SET {', '.join(assignments)} WHERE id = ?",
        tuple(params),
    )
    return int(cursor.rowcount or 0) > 0


def delete_rss_feed(conn: sqlite3.Connection, feed_id: int) -> bool:
    conn.execute("DELETE FROM rss_items WHERE rss_feed_id = ?", (int(feed_id),))
    cursor = conn.execute("DELETE FROM rss_feeds WHERE id = ?", (int(feed_id),))
    return int(cursor.rowcount or 0) > 0


def record_rss_fetch_result(
    conn: sqlite3.Connection,
    feed_id: int,
    *,
    error: str | None,
    etag: str | None = None,
    modified: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE rss_feeds
        SET last_fetched_at = datetime('now'),
            last_error = ?,
            etag = COALESCE(?, etag),
            modified = COALESCE(?, modified),
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (error, etag, modified, int(feed_id)),
    )


def upsert_rss_item(
    conn: sqlite3.Connection,
    *,
    rss_feed_id: int,
    item: dict[str, Any],
) -> tuple[int, bool]:
    stable = str(item.get("stable_feed_key") or stable_feed_key_from_item(item) or "").strip()
    if not stable:
        raise ValueError("rss item has no stable feed key")
    title = str(item.get("title") or "").strip() or "Untitled"
    before = conn.execute(
        "SELECT id FROM rss_items WHERE stable_feed_key = ?",
        (stable,),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO rss_items (
            rss_feed_id, stable_feed_key, guid, entry_id, title, abstract, url,
            canonical_url, doi, arxiv_id, publication_date, publication_title,
            authors, item_type, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(stable_feed_key) DO UPDATE SET
            rss_feed_id = excluded.rss_feed_id,
            guid = COALESCE(excluded.guid, rss_items.guid),
            entry_id = COALESCE(excluded.entry_id, rss_items.entry_id),
            title = excluded.title,
            abstract = COALESCE(excluded.abstract, rss_items.abstract),
            url = COALESCE(excluded.url, rss_items.url),
            canonical_url = COALESCE(excluded.canonical_url, rss_items.canonical_url),
            doi = COALESCE(excluded.doi, rss_items.doi),
            arxiv_id = COALESCE(excluded.arxiv_id, rss_items.arxiv_id),
            publication_date = COALESCE(excluded.publication_date, rss_items.publication_date),
            publication_title = COALESCE(excluded.publication_title, rss_items.publication_title),
            authors = COALESCE(excluded.authors, rss_items.authors),
            item_type = excluded.item_type,
            raw_json = COALESCE(excluded.raw_json, rss_items.raw_json),
            fetched_at = datetime('now'),
            updated_at = datetime('now')
        """,
        (
            int(rss_feed_id),
            stable,
            str(item.get("guid") or "") or None,
            str(item.get("entry_id") or item.get("rss_entry_id") or "") or None,
            title,
            str(item.get("abstract") or "") or None,
            str(item.get("url") or item.get("link") or "") or None,
            str(item.get("canonical_url") or item.get("url") or item.get("link") or "") or None,
            str(item.get("doi") or "") or None,
            str(item.get("arxiv_id") or "") or None,
            str(item.get("publication_date") or "") or None,
            str(item.get("publication_title") or "") or None,
            str(item.get("authors") or "") or None,
            str(item.get("item_type") or "journalArticle") or "journalArticle",
            json.dumps(item.get("raw") or {}, ensure_ascii=False) if item.get("raw") else None,
        ),
    )
    row = conn.execute(
        "SELECT id FROM rss_items WHERE stable_feed_key = ?",
        (stable,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"rss_items upsert failed for {stable!r}")
    return int(row["id"]), before is None


def list_rss_items(
    conn: sqlite3.Connection,
    *,
    rss_feed_id: int | None = None,
    unread_only: bool = False,
    limit: int | None = 1000,
    order: str = "newest_first",
) -> list[dict[str, Any]]:
    safe_limit = -1 if limit is None else max(1, min(int(limit), 5000))
    where = ["1=1"]
    params: list[Any] = []
    if rss_feed_id is not None:
        where.append("ri.rss_feed_id = ?")
        params.append(int(rss_feed_id))
    if unread_only:
        where.append("ri.read_at IS NULL")
    order_sql = "ri.created_at ASC" if order == "oldest_first" else "ri.created_at DESC"
    rows = conn.execute(
        f"""
        SELECT
            ri.*,
            rf.name AS feed_name
        FROM rss_items ri
        JOIN rss_feeds rf ON rf.id = ri.rss_feed_id
        WHERE {' AND '.join(where)}
        ORDER BY {order_sql}
        LIMIT ?
        """,
        (*params, safe_limit),
    ).fetchall()
    return [dict(row) for row in rows]


def mark_rss_items_read(conn: sqlite3.Connection, rss_item_ids: list[int]) -> int:
    ids = [int(i) for i in rss_item_ids if int(i or 0) > 0]
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    cursor = conn.execute(
        f"""
        UPDATE rss_items
        SET read_at = datetime('now'), updated_at = datetime('now')
        WHERE id IN ({placeholders}) AND read_at IS NULL
        """,
        ids,
    )
    return int(cursor.rowcount or 0)


def import_zotero_feeds(
    conn: sqlite3.Connection,
    feed_groups: list[dict[str, Any]],
) -> dict[str, int]:
    imported = 0
    skipped = 0
    for feed in feed_groups:
        url = str(feed.get("url") or "").strip()
        name = str(feed.get("name") or "").strip()
        if not url or not name:
            skipped += 1
            continue
        upsert_rss_feed(
            conn,
            name=name,
            url=url,
            enabled=True,
            source="zotero_import",
            imported_zotero_library_id=int(feed.get("library_id") or 0) or None,
        )
        imported += 1
    return {"imported": imported, "skipped": skipped}


def get_rss_item_by_stable_key(
    conn: sqlite3.Connection, stable_feed_key: str
) -> dict[str, Any] | None:
    """Return the rss_items row (with feed name) for a stable feed key, or None."""
    key = str(stable_feed_key or "").strip()
    if not key:
        return None
    row = conn.execute(
        """
        SELECT ri.*, rf.name AS feed_name
        FROM rss_items ri
        JOIN rss_feeds rf ON rf.id = ri.rss_feed_id
        WHERE ri.stable_feed_key = ?
        LIMIT 1
        """,
        (key,),
    ).fetchone()
    return dict(row) if row else None


def list_library_items(
    conn: sqlite3.Connection,
    *,
    decisions: tuple[str, ...],
    search: str | None = None,
    limit: int = 2000,
) -> list[dict[str, Any]]:
    """Kept processed rows joined to their rss_items metadata — the app-owned
    "library" the reading queue ranks when Zotero is absent. One row per
    ``stable_feed_key`` (newest processed row wins; caller dedups). Rows whose
    paper has no rss_items copy (legacy Zotero-sourced) still appear, carrying
    only the processed row's own title/doi (no abstract/url)."""
    if not decisions:
        return []
    placeholders = ",".join("?" for _ in decisions)
    where = [
        f"p.decision IN ({placeholders})",
        "p.stable_feed_key IS NOT NULL",
        "p.stable_feed_key != ''",
    ]
    params: list[Any] = [*decisions]
    if search and search.strip():
        where.append("(lower(COALESCE(ri.title, p.title)) LIKE ? OR lower(COALESCE(ri.abstract, '')) LIKE ?)")
        needle = f"%{search.strip().lower()}%"
        params.extend([needle, needle])
    safe_limit = max(1, min(int(limit), 5000))
    rows = conn.execute(
        f"""
        SELECT
            p.id AS processed_id, p.stable_feed_key, p.decision, p.reading_priority,
            p.title AS p_title, p.doi AS p_doi, p.arxiv_id AS p_arxiv,
            p.materialized_zotero_key, p.created_at AS p_created, p.updated_at AS p_updated,
            ri.title AS ri_title, ri.abstract, ri.url, ri.canonical_url, ri.doi AS ri_doi,
            ri.arxiv_id AS ri_arxiv, ri.publication_date, ri.publication_title,
            ri.authors, ri.item_type, ri.read_at, rf.name AS feed_name
        FROM processed_feed_items p
        LEFT JOIN rss_items ri ON ri.stable_feed_key = p.stable_feed_key
        LEFT JOIN rss_feeds rf ON rf.id = ri.rss_feed_id
        WHERE {' AND '.join(where)}
        ORDER BY p.created_at DESC
        LIMIT ?
        """,
        (*params, safe_limit),
    ).fetchall()
    return [dict(row) for row in rows]


__all__ = [
    "delete_rss_feed",
    "get_rss_item_by_stable_key",
    "import_zotero_feeds",
    "list_library_items",
    "list_rss_feeds",
    "list_rss_items",
    "mark_rss_items_read",
    "record_rss_fetch_result",
    "update_rss_feed",
    "upsert_rss_feed",
    "upsert_rss_item",
]
