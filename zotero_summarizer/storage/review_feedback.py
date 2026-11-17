"""Per-review owner feedback, one row per paper and review version.

``reviewed_at`` is the review's version: reviews are overwritten in place, so each row also
snapshots the generation hash and model it judged. The row holds the whole-review part (chips +
note columns) and ``fields`` (per-part dict, migration 20): ``{part: {chips, note, shown, at}}``,
or a ``{withdrawn, at}`` tombstone for a withdrawn part. Callers own the connection and commit.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

_COLUMNS = ("item_key", "reviewed_at", "generation_sha256", "model", "chips_json", "note", "fields_json",
            "created_at", "updated_at")
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM review_feedback "


def _fields(raw: object) -> dict[str, dict]:
    """The stored dict; NULL, ``[]`` (the first draft's default) and corrupt text read as empty."""
    try:
        value = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _row(values: tuple) -> dict[str, object]:
    row = dict(zip(_COLUMNS, values))
    row["chips"] = json.loads(row.pop("chips_json"))
    row["fields"] = _fields(row.pop("fields_json"))
    return row


def _rows(conn: sqlite3.Connection, where: str, params: tuple) -> list[dict[str, object]]:
    rows = conn.execute(f"{_SELECT}{where} ORDER BY item_key, reviewed_at", params).fetchall()
    return [_row(tuple(row)) for row in rows]


def update(conn: sqlite3.Connection, item_key: str, version: tuple[str, str, str],
           mutate: Callable[[dict[str, object]], None]) -> dict[str, object] | None:
    """Read-modify-write one version's row under ``BEGIN IMMEDIATE`` (concurrent writers queue).

    ``mutate`` edits ``chips``, ``note`` and ``fields`` in place. The row is deleted only when
    chips, note and fields are all empty (a tombstone keeps it); returns the stored row, or
    ``None`` when nothing is left. ``version`` is ``(reviewed_at, generation_sha256, model)``."""
    reviewed_at, generation_sha256, model = version
    conn.execute("BEGIN IMMEDIATE")
    found = _rows(conn, "WHERE item_key = ? AND reviewed_at = ?", (item_key, reviewed_at))
    row = found[0] if found else {"chips": [], "note": "", "fields": {}}
    mutate(row)
    if not (row["chips"] or row["note"] or row["fields"]):
        conn.execute("DELETE FROM review_feedback WHERE item_key = ? AND reviewed_at = ?", (item_key, reviewed_at))
        return None
    conn.execute(
        """INSERT INTO review_feedback (item_key, reviewed_at, generation_sha256, model, chips_json, note, fields_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(item_key, reviewed_at) DO UPDATE SET
             generation_sha256=excluded.generation_sha256, model=excluded.model, chips_json=excluded.chips_json,
             note=excluded.note, fields_json=excluded.fields_json, updated_at=datetime('now')""",
        (item_key, reviewed_at, generation_sha256, model, json.dumps(row["chips"]), row["note"],
         json.dumps(row["fields"], ensure_ascii=False)))
    return _rows(conn, "WHERE item_key = ? AND reviewed_at = ?", (item_key, reviewed_at))[0]


def list_for_paper(conn: sqlite3.Connection, item_key: str) -> list[dict[str, object]]:
    return _rows(conn, "WHERE item_key = ?", (item_key,))


def all_rows(conn: sqlite3.Connection) -> list[dict[str, object]]:
    return _rows(conn, "", ())
