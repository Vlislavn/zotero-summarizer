"""One current note across known feed/library aliases, without copying note bodies."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone

from zotero_summarizer.storage import repositories


def alias_resolver(conn):
    """Load the feed/library identity graph once; return ``key -> sorted alias family``.

    Edges: ``feed_key_aliases``; a legacy ``feed:<id>`` key to the single stable/
    materialized key its rows agree on (unless alias or ambiguity tables claim it);
    and every row's ``stable_feed_key`` <-> ``materialized_zotero_key``. Callers that
    resolve many keys (sync snapshots) reuse one resolver instead of rescanning the
    28k-row feed history per key.
    """
    edges = [tuple(row) for row in conn.execute("SELECT old_key, stable_feed_key FROM feed_key_aliases")]
    claimed = {a for a, _ in edges}
    claimed.update(row[0] for row in conn.execute("SELECT old_key FROM feed_key_alias_ambiguities"))
    legacy = {}
    for feed_item_id, stable, materialized in conn.execute(
        "SELECT feed_item_id, stable_feed_key, materialized_zotero_key FROM processed_feed_items"
    ):
        if stable and materialized:
            edges.append((stable, materialized))
        if feed_item_id > 0:
            legacy.setdefault(feed_item_id, set()).add(stable or materialized or None)
    for feed_item_id, keys in legacy.items():
        keys.discard(None)
        if len(keys) == 1 and f"feed:{feed_item_id}" not in claimed:
            edges.append((f"feed:{feed_item_id}", keys.pop()))
    graph = {}
    for a, b in edges:
        graph.setdefault(a, set()).add(b)
        graph.setdefault(b, set()).add(a)

    def resolve(item_key):
        family, todo = {item_key}, [item_key]
        while todo:
            for other in graph.get(todo.pop(), ()):
                if other not in family:
                    family.add(other)
                    todo.append(other)
        return sorted(family)

    return resolve


def resolve_item_key_aliases(conn, item_key):
    """Return the unambiguous feed/library identity family for a paper."""
    return alias_resolver(conn)(item_key)


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
    return dict(zip(("item_key", "value", "revision", "changed_at"), row)) if row else None


def current(conn, item_key, aliases=None):
    """Current value/revision; a deletion remains authoritative across aliases.

    Bulk callers pass the already-resolved ``aliases`` so the identity graph is not rebuilt per key.
    """
    row = _current(conn, aliases or resolve_item_key_aliases(conn, item_key))
    return row if row is not None else {"item_key": item_key, "value": None, "revision": 0}


@contextmanager
def current_for_mirror(db_path, item_key):
    """Hold current note intent stable until the caller finishes external delivery."""
    with closing(repositories._connect_to(db_path)) as conn:
        # ponytail: serialize app writers during delivery; use a dedicated delivery
        # worker if backup latency starts blocking local edits.
        conn.execute("BEGIN IMMEDIATE")
        yield current(conn, item_key)["value"]


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
    from zotero_summarizer.storage import review_note_projection

    review_note_projection.stage(conn, item_key)


def snapshots(conn, resolve):
    """Project shared note state onto every known alias for offline paper snapshots."""
    seeds = conn.execute(
        "SELECT item_key FROM review_notes UNION SELECT item_key FROM sync_changes WHERE field = 'review_note'"
    ).fetchall()
    fields = {}
    for seed in seeds:
        if (seed[0], "review_note") in fields:
            continue
        keys = resolve(seed[0])
        row = _current(conn, keys)
        for key in keys:
            fields[(key, "review_note")] = {
                "item_key": key, "field": "review_note", "value": row["value"],
                "revision": row["revision"], "comment": None, "source": None, "model_priority": None,
            }
    return fields
