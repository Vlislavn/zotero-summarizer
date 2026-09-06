"""repositories: labels queries (split)."""
from __future__ import annotations

import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zotero_summarizer.domain import VERDICT_SOURCE_USER
from zotero_summarizer.storage import review_notes
from zotero_summarizer.storage.repositories import (
    _VALID_LABEL_PRIORITIES,
    _connect_to,
)
from zotero_summarizer.storage.feed_identity import is_legacy_feed_key


def _row_to_label_verdict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "item_key": str(row["item_key"]),
        "original_derived_priority": str(row["original_derived_priority"]),
        "user_priority": str(row["user_priority"]),
        "comment": str(row["comment"]),
        "created_at": str(row["created_at"]),
        "source": str(row["source"]),
    }


def insert_or_update_label_verdict(
    db_path: Path,
    *,
    item_key: str,
    original_derived_priority: str,
    user_priority: str,
    comment: str,
    source: str = VERDICT_SOURCE_USER,
    expected_revision: int | None = None,
) -> int | None:
    """Commit a label verdict using the shared transactional writer."""
    conn = _connect_to(db_path)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            if expected_revision is not None:
                from zotero_summarizer.storage.label_mirrors import _current_change

                change = _current_change(conn, item_key)
                revision = change["revision"] if change is not None else 0
                if revision != expected_revision:
                    return None
            row_id, _ = upsert_label_verdict(
                conn, item_key=item_key, original_derived_priority=original_derived_priority,
                user_priority=user_priority, comment=comment, source=source,
            )
        return row_id
    finally:
        conn.close()


def upsert_label_verdict(
    conn: sqlite3.Connection,
    *,
    item_key: str,
    original_derived_priority: str,
    user_priority: str,
    comment: str,
    source: str = VERDICT_SOURCE_USER,
    training_sample: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any] | None]:
    """Write within the caller transaction; return id and the previous snapshot."""
    safe_item_key = str(item_key or "").strip()
    safe_original = str(original_derived_priority or "").strip()
    safe_user = str(user_priority or "").strip()
    safe_source = str(source or "").strip()
    if not safe_item_key:
        raise ValueError("item_key is required")
    if not safe_original:
        raise ValueError("original_derived_priority is required")
    if safe_user not in _VALID_LABEL_PRIORITIES:
        raise ValueError(
            f"user_priority must be one of {_VALID_LABEL_PRIORITIES}; got {user_priority!r}"
        )
    if not isinstance(comment, str):
        raise ValueError(f"comment must be a string; got {type(comment).__name__}")
    if not safe_source:
        raise ValueError("source is required")

    now_iso = datetime.now(timezone.utc).isoformat()
    previous_row = conn.execute("SELECT * FROM label_verdicts WHERE item_key = ?", (safe_item_key,)).fetchone()
    row = conn.execute(
        """
        INSERT INTO label_verdicts (
            item_key, original_derived_priority, user_priority, comment, created_at, source
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(item_key) DO UPDATE SET
            original_derived_priority = excluded.original_derived_priority,
            user_priority = excluded.user_priority,
            comment = excluded.comment,
            created_at = excluded.created_at,
            source = excluded.source
        RETURNING id
        """,
        (safe_item_key, safe_original, safe_user, comment, now_iso, safe_source),
    ).fetchone()
    if training_sample is not None:
        conn.execute(
            "UPDATE label_verdicts SET training_sample_json = ? WHERE item_key = ?",
            (json.dumps(training_sample), safe_item_key),
        )
    previous = _row_to_label_verdict(previous_row) if previous_row is not None else None
    return int(row[0]), previous


def get_label_verdict(db_path: Path, item_key: str) -> dict[str, Any] | None:
    """Return one verdict for ``item_key`` or None if not present.

    None signals absence (boundary contract), not an error.
    """
    safe_item_key = str(item_key or "").strip()
    if not safe_item_key:
        raise ValueError("item_key is required")
    conn = _connect_to(db_path)
    try:
        lookup_keys = [safe_item_key]
        if is_legacy_feed_key(safe_item_key):
            stable = ""
            try:
                alias = conn.execute(
                    "SELECT stable_feed_key FROM feed_key_aliases WHERE old_key = ?",
                    (safe_item_key,),
                ).fetchone()
                stable = str(alias["stable_feed_key"] or "").strip() if alias is not None else ""
                if not stable:
                    feed_item_id = int(safe_item_key.removeprefix("feed:"))
                    rows = conn.execute(
                        """
                        SELECT DISTINCT stable_feed_key
                        FROM processed_feed_items
                        WHERE feed_item_id = ?
                          AND stable_feed_key IS NOT NULL
                          AND stable_feed_key != ''
                        """,
                        (feed_item_id,),
                    ).fetchall()
                    stable_keys = {str(row["stable_feed_key"] or "").strip() for row in rows}
                    if len(stable_keys) == 1:
                        stable = next(iter(stable_keys))
            except sqlite3.OperationalError as exc:
                if "no such table:" not in str(exc).lower():
                    raise
            if stable and stable not in lookup_keys:
                lookup_keys.insert(0, stable)
        row = None
        for key in lookup_keys:
            row = conn.execute(
                """
                SELECT id, item_key, original_derived_priority, user_priority,
                       comment, created_at, source
                FROM label_verdicts
                WHERE item_key = ?
                """,
                (key,),
            ).fetchone()
            if row is not None:
                break
    finally:
        conn.close()
    if row is None:
        return None
    return _row_to_label_verdict(row)


def list_all_label_verdicts(db_path: Path, *, include_training: bool = False) -> list[dict[str, Any]]:
    """All verdicts, newest first, for complete UI/training/transfer snapshots."""
    conn = _connect_to(db_path)
    try:
        training_column = ", training_sample_json" if include_training else ""
        rows = conn.execute(
            f"""
            SELECT id, item_key, original_derived_priority, user_priority,
                   comment, created_at, source{training_column}
            FROM label_verdicts
            ORDER BY datetime(created_at) DESC, id DESC
            """
        ).fetchall()
    finally:
        conn.close()
    verdicts = []
    for row in rows:
        verdict = _row_to_label_verdict(row)
        if include_training:
            raw = row["training_sample_json"]
            verdict["training_sample"] = json.loads(raw) if raw is not None else None
        verdicts.append(verdict)
    return verdicts


def list_label_verdict_keys(db_path: Path) -> set[str]:
    """ALL distinct ``item_key`` values that have a manual verdict — uncapped.

    The golden-CSV re-export uses this to PRESERVE every manually-labelled item
    across the rebuild (a capped/paginated fetch would silently drop verdicts and
    lose them on re-export). Keys-only + DISTINCT, so it stays cheap even
    uncapped.
    """
    conn = _connect_to(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT item_key FROM label_verdicts WHERE item_key IS NOT NULL AND item_key != ''"
        ).fetchall()
    finally:
        conn.close()
    return {str(r["item_key"]) for r in rows}


def list_label_verdict_priorities(db_path: Path) -> dict[str, str]:
    """``{item_key: user_priority}`` for every manual verdict — uncapped.

    The reading-queue handled-filter needs the PRIORITY, not just the key
    (:func:`list_label_verdict_keys`): a ``dont_read`` verdict hides the paper
    (handled), but ``must_read``/``should_read``/``could_read`` are positive
    reading intents that must stay visible and pin to the top of Read next — a
    label should make a paper easy to find, not vanish. One row per ``item_key``
    via the UPSERT; uncapped for the same reason as
    :func:`list_label_verdict_keys` (a paged fetch silently drops rows once the
    table outgrows the cap).
    """
    conn = _connect_to(db_path)
    try:
        rows = conn.execute(
            "SELECT item_key, user_priority FROM label_verdicts "
            "WHERE item_key IS NOT NULL AND item_key != ''"
        ).fetchall()
    finally:
        conn.close()
    return {str(r["item_key"]): str(r["user_priority"]) for r in rows}


def delete_label_verdict(db_path: Path, item_key: str, *, expected_revision: int | None = None) -> bool:
    """Delete one verdict; return True iff a row was removed."""
    safe_item_key = str(item_key or "").strip()
    if not safe_item_key:
        raise ValueError("item_key is required")
    conn = _connect_to(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if expected_revision is not None:
            from zotero_summarizer.storage.label_mirrors import _current_change

            change = _current_change(conn, safe_item_key)
            revision = change["revision"] if change is not None else 0
            if revision != expected_revision:
                return False
        cursor = conn.execute(
            "DELETE FROM label_verdicts WHERE item_key = ?",
            (safe_item_key,),
        )
        conn.commit()
        return int(cursor.rowcount or 0) > 0
    finally:
        conn.close()


def upsert_review_note(db_path: Path, item_key: str, note: str) -> None:
    """Insert or REPLACE the single free-text review note for ``item_key``.

    One editable note per paper (mirrors the ``label_verdicts`` UPSERT shape).
    ``note`` must be a string; empty is allowed (clears the note's body).
    """
    safe_item_key = str(item_key or "").strip()
    if not safe_item_key:
        raise ValueError("item_key is required")
    if not isinstance(note, str):
        raise ValueError(f"note must be a string; got {type(note).__name__}")
    conn = _connect_to(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        review_notes.write(conn, safe_item_key, note)
        conn.commit()
    finally:
        conn.close()


def get_review_note(db_path: Path, item_key: str) -> str | None:
    """Return the saved review note for ``item_key``, or None if none exists.

    None signals absence (boundary contract), not an error.
    """
    safe_item_key = str(item_key or "").strip()
    if not safe_item_key:
        raise ValueError("item_key is required")
    conn = _connect_to(db_path)
    try:
        conn.execute("BEGIN")
        row = review_notes.current(conn, safe_item_key)
    finally:
        conn.close()
    return row["value"]


__all__ = [
    "_row_to_label_verdict",
    "insert_or_update_label_verdict",
    "upsert_label_verdict",
    "get_label_verdict",
    "list_all_label_verdicts",
    "list_label_verdict_keys",
    "list_label_verdict_priorities",
    "delete_label_verdict",
    "upsert_review_note",
    "get_review_note",
]
