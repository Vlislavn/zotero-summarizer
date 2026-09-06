"""One current note across known feed/library aliases, without copying note bodies."""
from datetime import datetime, timezone


def _keys(conn, item_key):
    rows = conn.execute(
        """WITH RECURSIVE
        legacy(a, b) AS (
          SELECT old_key, stable_feed_key FROM feed_key_aliases
          UNION ALL
          SELECT 'feed:' || feed_item_id,
                 MIN(COALESCE(NULLIF(stable_feed_key, ''), NULLIF(materialized_zotero_key, '')))
          FROM processed_feed_items
          WHERE feed_item_id > 0
            AND 'feed:' || feed_item_id NOT IN (SELECT old_key FROM feed_key_aliases)
            AND 'feed:' || feed_item_id NOT IN (SELECT old_key FROM feed_key_alias_ambiguities)
          GROUP BY feed_item_id
          HAVING COUNT(DISTINCT COALESCE(NULLIF(stable_feed_key, ''), NULLIF(materialized_zotero_key, ''))) = 1
        ), edges(a, b) AS (
          SELECT a, b FROM legacy
          UNION
          SELECT stable_feed_key, materialized_zotero_key FROM processed_feed_items
          WHERE NULLIF(stable_feed_key, '') IS NOT NULL
            AND NULLIF(materialized_zotero_key, '') IS NOT NULL
        ), keys(k) AS (
          SELECT ? UNION
          SELECT CASE WHEN edges.a = keys.k THEN edges.b ELSE edges.a END
          FROM edges JOIN keys ON edges.a = keys.k OR edges.b = keys.k
        ) SELECT k FROM keys ORDER BY k""", (item_key,),
    ).fetchall()
    return [row[0] for row in rows]


def _current(conn, keys):
    placeholders = ",".join("?" for _ in keys)
    row = conn.execute(
        f"""SELECT item_key, value, revision, changed_at FROM sync_changes
            WHERE field = 'review_note' AND item_key IN ({placeholders})
            UNION ALL
            SELECT item_key, note, 0, updated_at FROM review_notes n
            WHERE item_key IN ({placeholders}) AND NOT EXISTS (
              SELECT 1 FROM sync_changes s WHERE s.item_key = n.item_key AND s.field = 'review_note')
            ORDER BY revision DESC, changed_at DESC, item_key LIMIT 1""", (*keys, *keys),
    ).fetchone()
    return dict(row) if row else None


def current(conn, item_key):
    """Current value/revision; a deletion remains authoritative across aliases."""
    row = _current(conn, _keys(conn, item_key))
    return row if row is not None else {"item_key": item_key, "value": None, "revision": 0}


def write(conn, item_key, note):
    """Set/delete the current note in the caller's writer transaction."""
    key = current(conn, item_key)["item_key"]
    if note is None:
        conn.execute("DELETE FROM review_notes WHERE item_key = ?", (key,))
    else:
        conn.execute(
            """INSERT INTO review_notes(item_key, note, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(item_key) DO UPDATE SET note=excluded.note, updated_at=excluded.updated_at""",
            (key, note, datetime.now(timezone.utc).isoformat()),
        )


def snapshots(conn):
    """Project shared note state onto every known alias for offline paper snapshots."""
    seeds = conn.execute(
        "SELECT item_key FROM review_notes UNION SELECT item_key FROM sync_changes WHERE field = 'review_note'"
    ).fetchall()
    fields = {}
    # ponytail: resolve each note family once; batch identity joins if snapshot latency warrants it.
    for seed in seeds:
        if (seed[0], "review_note") in fields:
            continue
        keys = _keys(conn, seed[0])
        row = _current(conn, keys)
        for key in keys:
            fields[(key, "review_note")] = {
                "item_key": key, "field": "review_note", "value": row["value"],
                "revision": row["revision"], "comment": None, "source": None, "model_priority": None,
            }
    return fields
