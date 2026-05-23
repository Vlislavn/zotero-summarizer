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

    Returns ``None`` only when the row genuinely does not exist (caller's
    contract: distinguish "not in DB" from a hard error). ``feed_item_id``
    must be a positive int — invalid ids are programmer errors and raise.
    """
    safe_id = int(feed_item_id)
    if safe_id <= 0:
        raise ValueError(f"feed_item_id must be positive; got {feed_item_id!r}")
    row = conn.execute(
        """
        SELECT * FROM processed_feed_items
        WHERE feed_item_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (safe_id,),
    ).fetchone()
    return dict(row) if row else None


def get_processed_feed_item_by_pk(
    conn: sqlite3.Connection,
    pk: int,
) -> dict[str, Any] | None:
    """Return one processed_feed_items row by its primary-key ``id``.

    The daily slate exposes ``SlatePaper.item_id`` = this PK, so a Today
    card verdict resolves the source row through it. ``None`` when the row
    doesn't exist; a non-positive id is a programmer error and raises.
    """
    safe_pk = int(pk)
    if safe_pk <= 0:
        raise ValueError(f"pk must be positive; got {pk!r}")
    row = conn.execute(
        "SELECT * FROM processed_feed_items WHERE id = ? LIMIT 1",
        (safe_pk,),
    ).fetchone()
    return dict(row) if row else None
