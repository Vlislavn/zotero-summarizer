"""Transactional v10 evolution of the existing revision log and mirror intent."""

from zotero_summarizer.storage._repo_sync import SYNC_CHANGES_TABLE

_LABEL_RECEIPTS = (
    "CREATE TABLE label_mirror_receipts ("
    "revision INTEGER PRIMARY KEY REFERENCES sync_changes(revision))"
)


def _rebuild_sync_changes(conn):
    # Keep foreign keys enabled: remove/recreate the owned dependent table in
    # this same migration transaction, retaining its exact rows and definition.
    receipt_schema = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='label_mirror_receipts'"
    ).fetchone()
    receipts = conn.execute("SELECT revision FROM label_mirror_receipts").fetchall() if receipt_schema else []
    sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='sync_changes'").fetchone()
    dependents = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE "
        "(type='index' AND tbl_name='sync_changes' AND sql IS NOT NULL) OR "
        "(type='trigger' AND (tbl_name='sync_changes' OR name IN "
        "('sync_label_insert','sync_label_update','sync_label_delete',"
        "'sync_note_insert','sync_note_update','sync_note_delete'))) ORDER BY type, name"
    ).fetchall()
    for kind, name, _sql in dependents:
        quoted = name.replace('"', '""')
        conn.execute(f'DROP {kind} "{quoted}"')
    if receipt_schema:
        conn.execute("DROP TABLE label_mirror_receipts")
    conn.execute(SYNC_CHANGES_TABLE.replace("IF NOT EXISTS sync_changes", "sync_changes_next"))
    conn.execute("INSERT INTO sync_changes_next SELECT * FROM sync_changes ORDER BY revision")
    conn.execute("DROP TABLE sync_changes")
    conn.execute("ALTER TABLE sync_changes_next RENAME TO sync_changes")
    if sequence:
        conn.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name='sync_changes'", (sequence[0],))
    for _kind, _name, sql in dependents:
        conn.execute(sql)
    conn.execute(receipt_schema[0] if receipt_schema else _LABEL_RECEIPTS)
    conn.executemany("INSERT INTO label_mirror_receipts(revision) VALUES (?)", receipts)


def apply(conn):
    """Rebuild/backfill without committing; the numbered migration owns commit."""
    from zotero_summarizer.storage import review_note_projection, review_notes

    _rebuild_sync_changes(conn)
    conn.execute(
        """CREATE TABLE review_note_mirror_intents (
            item_key TEXT PRIMARY KEY,
            revision INTEGER NOT NULL,
            body TEXT NOT NULL,
            target_key TEXT,
            acknowledged INTEGER NOT NULL DEFAULT 0 CHECK(acknowledged IN (0, 1))
        )"""
    )
    conn.execute(
        """CREATE TRIGGER sync_question_insert AFTER INSERT ON review_questions BEGIN
             INSERT INTO sync_changes(item_key, field, value)
             VALUES (NEW.item_key, 'review_question', NEW.submission_id);
           END"""
    )
    conn.execute(
        """INSERT INTO sync_changes(item_key, field, value, changed_at)
           SELECT q.item_key, 'review_question', q.submission_id, q.submitted_at
           FROM review_questions q WHERE NOT EXISTS (
             SELECT 1 FROM sync_changes s
             WHERE s.field='review_question' AND s.value=q.submission_id)
           ORDER BY q.submitted_at, q.submission_id"""
    )
    seeds = conn.execute(
        "SELECT item_key FROM review_notes UNION SELECT item_key FROM review_questions "
        "UNION SELECT item_key FROM sync_changes WHERE field='review_note' ORDER BY item_key"
    ).fetchall()
    seen = set()
    for (key,) in seeds:
        if key not in seen:
            review_note_projection.stage(conn, key)
            seen.update(review_notes.resolve_item_key_aliases(conn, key))
