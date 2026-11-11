"""Serialize current-label mirrors; acknowledge only completed retractions."""

from contextlib import contextmanager
from pathlib import Path

from zotero_summarizer.storage import feeds_lookup, repositories

_CURRENT_CHANGES = """
SELECT s.item_key, s.revision AS revision, s.value, r.revision AS mirrored, s.comment, s.source
FROM sync_changes s LEFT JOIN label_mirror_receipts r ON r.revision = s.revision
WHERE s.field = 'verdict' AND s.revision =
  (SELECT MAX(n.revision) FROM sync_changes n WHERE n.item_key = s.item_key AND n.field = 'verdict')
UNION ALL
SELECT v.item_key, 0, v.user_priority, NULL, v.comment, v.source FROM label_verdicts v
WHERE NOT EXISTS (SELECT 1 FROM sync_changes s WHERE s.item_key = v.item_key AND s.field = 'verdict')
"""


def _target_key(conn, item_key: str) -> str | None:
    if item_key.startswith("note:"):
        return None
    if not item_key.startswith("feed:"):
        return item_key
    suffix = item_key.removeprefix("feed:")
    row = (feeds_lookup.get_processed_feed_item_by_id(conn, int(suffix))
           if suffix.isdigit() and int(suffix) > 0
           else feeds_lookup.get_processed_feed_item_by_stable_key(conn, item_key))
    if row is None:
        return None
    direct = row.get("materialized_zotero_key")
    if direct:
        return str(direct)
    sibling = conn.execute(
        "SELECT materialized_zotero_key FROM processed_feed_items "
        "WHERE stable_feed_key = ? AND materialized_zotero_key IS NOT NULL "
        "AND materialized_zotero_key <> '' ORDER BY id LIMIT 1",
        (row.get("stable_feed_key"),),
    ).fetchone()
    return str(sibling[0]) if sibling else None


def _current_change(conn, item_key: str):
    target = _target_key(conn, item_key)
    return conn.execute(
        "WITH keys(item_key) AS (SELECT ? UNION SELECT ? UNION "
        "SELECT stable_feed_key FROM feed_key_aliases WHERE old_key = ? UNION "
        "SELECT stable_feed_key FROM processed_feed_items WHERE materialized_zotero_key = ?), "
        f"changes AS ({_CURRENT_CHANGES}) SELECT s.* FROM changes s "
        "WHERE (s.item_key IN (SELECT item_key FROM keys) "
        "OR s.item_key IN (SELECT old_key FROM feed_key_aliases "
        "WHERE stable_feed_key IN (SELECT item_key FROM keys))) "
        "ORDER BY s.revision DESC LIMIT 1",
        (item_key, target, item_key, target),
    ).fetchone()


def snapshot(conn, item_key: str, *, redeliver: bool = False) -> dict | None:
    row = _current_change(conn, item_key)
    if row is None:
        return None
    target = _target_key(conn, row["item_key"])
    return {**dict(row), "lookup_item_key": item_key, "target_key": target,
            "label_pending": row["value"] is not None or row["mirrored"] is None or redeliver} if target else None


def acknowledge(conn, desired: dict) -> bool:
    current = snapshot(conn, desired["lookup_item_key"])
    fields = ("item_key", "revision", "value", "comment", "source", "target_key")
    if current is None or any(current[key] != desired[key] for key in fields):
        return False
    if current["value"] is None:
        conn.execute("INSERT OR IGNORE INTO label_mirror_receipts(revision) VALUES (?)", (current["revision"],))
    return True


@contextmanager
def current_label(db_path: Path, item_key: str, *, revision: int | None = None, redeliver: bool = False):
    """Yield the caller-owned transaction and current intent; exit is not a delivery ACK."""
    conn = repositories._connect_to(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        desired = snapshot(conn, item_key, redeliver=redeliver)
        if desired is not None and revision is not None and desired["revision"] != revision:
            desired = None
        yield conn, desired
        conn.commit()
    finally:
        conn.close()


def states(db_path: Path) -> dict[str, dict]:
    """Newest label revision per real Zotero target, across feed/library aliases."""
    conn = repositories._connect_to(db_path)
    try:
        conn.execute("BEGIN")
        rows = conn.execute(_CURRENT_CHANGES + " ORDER BY revision").fetchall()
        out = {}
        for row in rows:
            target = _target_key(conn, row["item_key"])
            if target:
                out[target] = {**dict(row), "target_key": target}
        return out
    finally:
        conn.close()
