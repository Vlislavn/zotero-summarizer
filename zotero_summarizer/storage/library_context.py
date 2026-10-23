"""Read-only current note/verdict snapshots; never acknowledge native mirrors."""
from contextlib import closing
import sqlite3

from zotero_summarizer.storage import label_mirrors, review_notes


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
