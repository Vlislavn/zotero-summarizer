"""Read-only title lookup for Zotero item keys mirrored in the corpus cache."""
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
