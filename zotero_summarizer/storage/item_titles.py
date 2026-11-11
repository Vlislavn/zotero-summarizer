"""Read-only title lookup for item keys: Zotero keys in the corpus cache, feed keys in the triage DB."""
from __future__ import annotations

import sqlite3
from pathlib import Path


def titles_for_keys(db_path: Path, keys: list[str]) -> dict[str, str]:
    """``{item_key: title}`` for the keys the corpus knows; empty when the cache is absent."""
    unique = sorted({key for key in keys if key})
    if not unique or not Path(db_path).exists():
        return {}
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            marks = ",".join("?" * len(unique))
            rows = conn.execute(
                f"SELECT item_id, title FROM corpus_embeddings WHERE item_id IN ({marks})", unique
            ).fetchall()
    except sqlite3.Error:
        return {}
    return {str(key): str(title) for key, title in rows if title}


def feed_titles_for_keys(db_path: Path, keys: list[str]) -> dict[str, str]:
    """``{key: title}`` for feed-item keys (stable or legacy ``feed:<id>`` aliases) in the triage DB."""
    unique = sorted({key for key in keys if key})
    if not unique or not Path(db_path).exists():
        return {}
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            marks = ",".join("?" * len(unique))
            rows = conn.execute(
                f"""SELECT k.key, i.title FROM (
                        SELECT stable_feed_key AS key, stable_feed_key AS stable FROM rss_items
                        UNION ALL SELECT old_key, stable_feed_key FROM feed_key_aliases
                        UNION ALL SELECT 'feed:' || id, stable_feed_key FROM rss_items) k
                    JOIN rss_items i ON i.stable_feed_key = k.stable
                    WHERE k.key IN ({marks}) AND i.title != ''""", unique
            ).fetchall()
    except sqlite3.Error:
        return {}
    return {str(key): str(title) for key, title in rows if title}
