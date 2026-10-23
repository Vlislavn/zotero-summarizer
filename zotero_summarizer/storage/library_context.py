"""Read-only current note/verdict snapshots; never acknowledge native mirrors."""
from contextlib import closing
import sqlite3

from zotero_summarizer.storage import label_mirrors, review_notes


def read_discovery_membership(db_path):
    """Uncapped latest decision per durable key, before any retention filtering."""
    with closing(sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute("""
            WITH current AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY stable_feed_key
                    ORDER BY COALESCE(updated_at, created_at) DESC, id DESC
                ) AS position
                FROM processed_feed_items
                WHERE stable_feed_key IS NOT NULL AND stable_feed_key != ''
            )
            SELECT p.stable_feed_key, p.decision, p.materialized_zotero_key,
                   p.doi, p.arxiv_id, ri.doi AS rss_doi, ri.arxiv_id AS rss_arxiv_id,
                   ri.url, ri.canonical_url
            FROM current p LEFT JOIN rss_items ri ON ri.stable_feed_key = p.stable_feed_key
            WHERE p.position = 1 ORDER BY p.stable_feed_key
        """).fetchall()
        return [dict(row) for row in rows]


def read_context(db_path, item_keys):
    with closing(sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        conn.execute("BEGIN")
        result = {}
        for key in item_keys:
            aliases = review_notes._keys(conn, key)
            placeholders = ",".join("?" for _ in aliases)
            row = conn.execute(
                f"WITH changes AS ({label_mirrors._CURRENT_CHANGES}) SELECT c.*, v.source "
                "FROM changes c LEFT JOIN label_verdicts v ON v.item_key = c.item_key "
                f"WHERE c.item_key IN ({placeholders}) "
                "ORDER BY c.revision DESC, v.created_at DESC, c.item_key LIMIT 1", aliases,
            ).fetchone()
            result[key] = {"aliases": aliases, "manual": review_notes.current(conn, key),
                           "verdict": dict(row) if row is not None else None}
        return result
