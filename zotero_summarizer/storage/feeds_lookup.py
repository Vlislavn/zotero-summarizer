"""Single-row lookups against ``processed_feed_items``.

Split out of ``storage/feeds.py`` for file-size compliance (<500 LOC each)
and single-responsibility: pure read helpers that resolve one row by a key.
``feeds.py`` re-exports these so existing
``from zotero_summarizer.storage import feeds; feeds.get_processed_feed_item_by_id``
callers keep working.
"""
from __future__ import annotations

import sqlite3
from typing import Any


def _fetch_one_processed(
    conn: sqlite3.Connection, value: int, label: str, where_sql: str
) -> dict[str, Any] | None:
    """Fetch one ``processed_feed_items`` row by a positive-int key.

    ``None`` when the row is absent (caller's contract: distinguish "not in DB"
    from a hard error). A non-positive ``value`` is a programmer error and raises.
    ``where_sql`` is the trusted, in-source ``WHERE …``/``ORDER BY …`` fragment.
    """
    safe = int(value)
    if safe <= 0:
        raise ValueError(f"{label} must be positive; got {value!r}")
    row = conn.execute(
        f"SELECT * FROM processed_feed_items WHERE {where_sql} LIMIT 1",
        (safe,),
    ).fetchone()
    return dict(row) if row else None


def get_processed_feed_item_by_id(
    conn: sqlite3.Connection,
    feed_item_id: int,
) -> dict[str, Any] | None:
    """Return the most recent processed_feed_items row for a given feed_item_id.

    The golden CSV uses ``feed:<feed_item_id>`` as the row key, dropping the
    library id. Resolving back from CSV to a DB row therefore goes through
    feed_item_id alone. If the same item id appears across multiple feed
    libraries (rare; Zotero reuses ids per library), the newest row wins
    — the older one is from a previous library that has since gone away.
    """
    return _fetch_one_processed(
        conn, feed_item_id, "feed_item_id", "feed_item_id = ? ORDER BY created_at DESC"
    )


def get_processed_feed_item_by_pk(
    conn: sqlite3.Connection,
    pk: int,
) -> dict[str, Any] | None:
    """Return one processed_feed_items row by its primary-key ``id``.

    The daily slate exposes ``SlatePaper.item_id`` = this PK, so a Today
    card verdict resolves the source row through it.
    """
    return _fetch_one_processed(conn, pk, "pk", "id = ?")
