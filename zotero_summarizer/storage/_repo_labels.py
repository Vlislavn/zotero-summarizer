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
    VERDICT_SOURCE_USER,
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
) -> int:
    """Insert one label verdict, or REPLACE the existing row for ``item_key``.

    ``source`` records provenance (``domain.VERDICT_SOURCE_*``): a deliberate
    user verdict (default) vs. the machine-written provisional "Add to
    library" verdict. The UPSERT propagates it, so a later explicit relabel
    of a machine add flips the row back to a user verdict.

    Returns the row id. Fails fast on bad inputs:
    - empty item_key / original_derived_priority / source
    - user_priority not in the 4-class enum
    - comment must be a string (may be empty)
    """
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
    conn = _connect_to(db_path)
    try:
        conn.execute(
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
            """,
            (safe_item_key, safe_original, safe_user, comment, now_iso, safe_source),
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
                   comment, created_at, source
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
                       comment, created_at, source
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
                       comment, created_at, source
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


def list_all_label_verdicts(db_path: Path) -> list[dict[str, Any]]:
    """ALL verdict rows — uncapped, for the training/ground-truth merge.

    The paged :func:`list_label_verdicts` keeps its UI cap; the hybrid
    ground-truth loader must see EVERY verdict (a capped fetch silently
    dropped the oldest verdicts from training once the table outgrew the
    cap — same failure class :func:`list_label_verdict_keys` exists for).
    """
    conn = _connect_to(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, item_key, original_derived_priority, user_priority,
                   comment, created_at, source
            FROM label_verdicts
            ORDER BY datetime(created_at) DESC, id DESC
            """
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_label_verdict(r) for r in rows]


def list_label_verdict_keys(db_path: Path) -> set[str]:
    """ALL distinct ``item_key`` values that have a manual verdict — uncapped.

    The golden-CSV re-export uses this to PRESERVE every manually-labelled item
    across the rebuild (a capped/paginated fetch would silently drop verdicts and
    lose them on re-export). Keys-only + DISTINCT, so it stays cheap even
    uncapped; the row-listing :func:`list_label_verdicts` keeps its UI page cap.
    """
    conn = _connect_to(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT item_key FROM label_verdicts WHERE item_key IS NOT NULL AND item_key != ''"
        ).fetchall()
    finally:
        conn.close()
    return {str(r["item_key"]) for r in rows}


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
    "list_all_label_verdicts",
    "list_label_verdict_keys",
    "delete_label_verdict",
]
