"""Current composite My notes intent; external delivery remains caller-owned."""

from contextlib import closing, contextmanager

from zotero_summarizer.storage import repositories, review_notes


def _target_key(keys):
    targets = [key for key in keys if not key.startswith(("feed:", "note:"))]
    return targets[0] if len(targets) == 1 else None


def _body(conn, keys):
    from zotero_summarizer.storage import review_questions

    row = review_notes._current(conn, keys)
    manual = row["value"] if row is not None else None
    parts = [manual] if manual else []
    questions = review_questions._list_for_keys(conn, keys)
    if questions:
        parts.append("Saved questions:\n" + "\n".join(
            f"{index}. {row['question']}" for index, row in enumerate(questions, 1)
        ))
    return "\n\n".join(parts)


def snapshot(conn, item_key) -> dict:
    """Read alias-family revision/body/target and signature-bound pending state."""
    keys = review_notes.resolve_item_key_aliases(conn, item_key)
    placeholders = ",".join("?" for _ in keys)
    revision = int(conn.execute(
        "SELECT COALESCE(MAX(revision), 0) FROM sync_changes "
        f"WHERE field IN ('review_note', 'review_question') AND item_key IN ({placeholders})", keys,
    ).fetchone()[0])
    body, target = _body(conn, keys), _target_key(keys)
    intents = conn.execute(
        f"SELECT revision, body, target_key, acknowledged FROM review_note_mirror_intents "
        f"WHERE item_key IN ({placeholders})", keys,
    ).fetchall()
    delivered = any(ack and (rev, text, key) == (revision, body, target)
                    for rev, text, key, ack in intents)
    return {"item_key": item_key, "revision": revision, "body": body,
            "target_key": target, "pending": bool(revision or body or intents) and not delivered}


def stage(conn, item_key) -> None:
    """Stage the current projection in the caller's question/manual transaction."""
    desired = snapshot(conn, item_key)
    if not desired["pending"]:
        return
    conn.execute(
        """INSERT INTO review_note_mirror_intents(item_key, revision, body, target_key)
           VALUES (?, ?, ?, ?) ON CONFLICT(item_key) DO UPDATE SET
             revision=excluded.revision, body=excluded.body,
             target_key=excluded.target_key, acknowledged=0
           WHERE review_note_mirror_intents.revision IS NOT excluded.revision
              OR review_note_mirror_intents.body IS NOT excluded.body
              OR review_note_mirror_intents.target_key IS NOT excluded.target_key
              OR review_note_mirror_intents.acknowledged <> 0""",
        (item_key, desired["revision"], desired["body"], desired["target_key"]),
    )


def acknowledge(conn, desired) -> bool:
    """Acknowledge only an unchanged, target-bearing current intent, explicitly."""
    current = snapshot(conn, desired["item_key"])
    signature = ("revision", "body", "target_key")
    if current["target_key"] is None or any(current[key] != desired[key] for key in signature):
        return False
    if not current["pending"]:
        return bool(current["revision"] or current["body"])
    stage(conn, desired["item_key"])
    return conn.execute(
        """UPDATE review_note_mirror_intents SET acknowledged=1
           WHERE item_key=? AND revision=? AND body=? AND target_key IS ?""",
        (desired["item_key"], *(current[key] for key in signature)),
    ).rowcount == 1


@contextmanager
def current_for_mirror(db_path, item_key):
    """Serialize delivery after save; normal exit alone does not acknowledge it."""
    with closing(repositories._connect_to(db_path)) as conn, conn:
        # ponytail: external backup/write holds the app writer lock, as with
        # current manual/label mirrors; a dedicated worker is a separate concern.
        conn.execute("BEGIN IMMEDIATE")
        stage(conn, item_key)
        yield conn, snapshot(conn, item_key)
