"""repositories: labels queries (split)."""
from __future__ import annotations

import json  # noqa: F401
import sqlite3  # noqa: F401
from datetime import datetime, timezone
from pathlib import Path  # noqa: F401
from typing import Any  # noqa: F401

from zotero_summarizer.domain import (  # noqa: F401
    ChangeStatus,
    EXPLICIT_FEEDBACK_SIGNALS,
    READING_PRIORITY_SORT_RANK,
)
from zotero_summarizer.storage.repositories import (  # noqa: F401
    _VALID_LABEL_PRIORITIES,
    _connect_to,
    _get_columns,
    _get_conn,
    _json_to_list,
    _normalize_order,
    _normalize_sort,
    _rows_to_dicts,
    _sort_expression,
)


def _row_to_label_verdict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "item_key": str(row["item_key"]),
        "original_derived_priority": str(row["original_derived_priority"]),
        "user_priority": str(row["user_priority"]),
        "comment": str(row["comment"]),
        "created_at": str(row["created_at"]),
    }


def insert_or_update_label_verdict(
    db_path: Path,
    *,
    item_key: str,
    original_derived_priority: str,
    user_priority: str,
    comment: str,
) -> int:
    """Insert one label verdict, or REPLACE the existing row for ``item_key``.

    Returns the row id. Fails fast on bad inputs:
    - empty item_key / original_derived_priority
    - user_priority not in the 4-class enum
    - comment must be a string (may be empty)
    """
    safe_item_key = str(item_key or "").strip()
    safe_original = str(original_derived_priority or "").strip()
    safe_user = str(user_priority or "").strip()
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

    now_iso = datetime.now(timezone.utc).isoformat()
    conn = _connect_to(db_path)
    try:
        conn.execute(
            """
            INSERT INTO label_verdicts (
                item_key, original_derived_priority, user_priority, comment, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(item_key) DO UPDATE SET
                original_derived_priority = excluded.original_derived_priority,
                user_priority = excluded.user_priority,
                comment = excluded.comment,
                created_at = excluded.created_at
            """,
            (safe_item_key, safe_original, safe_user, comment, now_iso),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM label_verdicts WHERE item_key = ?",
            (safe_item_key,),
        ).fetchone()
        if row is None:
            raise RuntimeError(
                f"UPSERT completed but no row found for item_key {safe_item_key!r}"
            )
        return int(row["id"])
    finally:
        conn.close()


def get_label_verdict(db_path: Path, item_key: str) -> dict[str, Any] | None:
    """Return one verdict for ``item_key`` or None if not present.

    None signals absence (boundary contract), not an error.
    """
    safe_item_key = str(item_key or "").strip()
    if not safe_item_key:
        raise ValueError("item_key is required")
    conn = _connect_to(db_path)
    try:
        row = conn.execute(
            """
            SELECT id, item_key, original_derived_priority, user_priority,
                   comment, created_at
            FROM label_verdicts
            WHERE item_key = ?
            """,
            (safe_item_key,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return _row_to_label_verdict(row)


def list_label_verdicts(
    db_path: Path,
    *,
    user_priority: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """List verdicts most-recent-first; optionally filter by ``user_priority``."""
    safe_limit = int(limit)
    if not (1 <= safe_limit <= 5000):
        raise ValueError(f"limit must be between 1 and 5000; got {limit}")
    safe_filter: str | None = None
    if user_priority is not None:
        candidate = str(user_priority).strip()
        if candidate not in _VALID_LABEL_PRIORITIES:
            raise ValueError(
                f"user_priority must be one of {_VALID_LABEL_PRIORITIES}; got {user_priority!r}"
            )
        safe_filter = candidate

    conn = _connect_to(db_path)
    try:
        if safe_filter is None:
            rows = conn.execute(
                """
                SELECT id, item_key, original_derived_priority, user_priority,
                       comment, created_at
                FROM label_verdicts
                ORDER BY datetime(created_at) DESC, id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, item_key, original_derived_priority, user_priority,
                       comment, created_at
                FROM label_verdicts
                WHERE user_priority = ?
                ORDER BY datetime(created_at) DESC, id DESC
                LIMIT ?
                """,
                (safe_filter, safe_limit),
            ).fetchall()
    finally:
        conn.close()
    return [_row_to_label_verdict(r) for r in rows]


def delete_label_verdict(db_path: Path, item_key: str) -> bool:
    """Delete one verdict; return True iff a row was removed."""
    safe_item_key = str(item_key or "").strip()
    if not safe_item_key:
        raise ValueError("item_key is required")
    conn = _connect_to(db_path)
    try:
        cursor = conn.execute(
            "DELETE FROM label_verdicts WHERE item_key = ?",
            (safe_item_key,),
        )
        conn.commit()
        return int(cursor.rowcount or 0) > 0
    finally:
        conn.close()


__all__ = [
    "_row_to_label_verdict",
    "insert_or_update_label_verdict",
    "get_label_verdict",
    "list_label_verdicts",
    "delete_label_verdict",
]
