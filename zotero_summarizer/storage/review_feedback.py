"""Per-review owner feedback (chips + note), one row per paper and review version.

``reviewed_at`` is the review's version: reviews are overwritten in place, so each row also
snapshots the generation hash and model it judged. Callers own the connection and commit.
"""
from __future__ import annotations

import json
import sqlite3

_FIELDS = ("item_key", "reviewed_at", "generation_sha256", "model", "chips", "note", "created_at", "updated_at")


def _row(values: tuple) -> dict[str, object]:
    row = dict(zip(_FIELDS, values))
    row["chips"] = json.loads(row["chips"])
    return row


def upsert(conn: sqlite3.Connection, item_key: str, version: tuple[str, str, str],
           chips: list[str], note: str) -> dict[str, object]:
    """Insert or replace the feedback for one review version; ``created_at`` survives a replace.

    ``version`` is ``(reviewed_at, generation_sha256, model)``."""
    reviewed_at, generation_sha256, model = version
    conn.execute(
        """INSERT INTO review_feedback (item_key, reviewed_at, generation_sha256, model, chips_json, note)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(item_key, reviewed_at) DO UPDATE SET
             generation_sha256=excluded.generation_sha256, model=excluded.model,
             chips_json=excluded.chips_json, note=excluded.note, updated_at=datetime('now')""",
        (item_key, reviewed_at, generation_sha256, model, json.dumps(chips), note))
    return _rows(conn, "WHERE item_key = ? AND reviewed_at = ?", (item_key, reviewed_at))[0]


def delete(conn: sqlite3.Connection, item_key: str, reviewed_at: str) -> None:
    conn.execute("DELETE FROM review_feedback WHERE item_key = ? AND reviewed_at = ?", (item_key, reviewed_at))


def _rows(conn: sqlite3.Connection, where: str, params: tuple) -> list[dict[str, object]]:
    rows = conn.execute(
        "SELECT item_key, reviewed_at, generation_sha256, model, chips_json, note, created_at, updated_at "
        f"FROM review_feedback {where} ORDER BY item_key, reviewed_at", params).fetchall()
    return [_row(tuple(row)) for row in rows]


def list_for_paper(conn: sqlite3.Connection, item_key: str) -> list[dict[str, object]]:
    return _rows(conn, "WHERE item_key = ?", (item_key,))


def all_rows(conn: sqlite3.Connection) -> list[dict[str, object]]:
    return _rows(conn, "", ())
