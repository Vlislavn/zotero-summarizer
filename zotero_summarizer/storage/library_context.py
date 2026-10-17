"""Read-only related-paper context over the existing alias/revision owners."""
from contextlib import closing
from pathlib import Path
import sqlite3

from zotero_summarizer.storage import label_mirrors, review_notes


def read_context(db_path: Path, item_keys: list[str]) -> dict[str, dict]:
    """One SQLite snapshot; deletes remain authoritative and no mirror is acknowledged."""
    with closing(sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        conn.execute("BEGIN")
        result = {}
        for key in item_keys:
            aliases = review_notes.resolve_item_key_aliases(conn, key)
            current = label_mirrors._current_change(conn, key)
            verdict = None
            if current is not None and current['value'] is not None:
                source = conn.execute("SELECT source FROM label_verdicts WHERE item_key = ?",
                                      (current['item_key'],)).fetchone()
                if source is not None and source['source'] == 'user':
                    verdict = dict(current)
            result[key] = {'aliases': aliases, 'manual': review_notes.current(conn, key), 'verdict': verdict}
        return result
