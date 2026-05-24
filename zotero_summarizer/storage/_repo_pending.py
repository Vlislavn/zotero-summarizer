"""repositories: pending queries (split)."""
from __future__ import annotations

import json  # noqa: F401
import sqlite3  # noqa: F401
from datetime import datetime, timezone  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any  # noqa: F401

from zotero_summarizer.domain import (  # noqa: F401
    ChangeStatus,
    EXPLICIT_FEEDBACK_SIGNALS,
    READING_PRIORITY_SORT_RANK,
)
from zotero_summarizer.storage.repositories import (  # noqa: F401
    _connect_to,
    _get_columns,
    _get_conn,
    _json_to_list,
    _normalize_order,
    _normalize_sort,
    _rows_to_dicts,
    _sort_expression,
)
from zotero_summarizer.storage.rows import PendingChangeRow


def _pending_rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    """Validate each row against the typed model (fail-loud on schema drift),
    then emit the legacy dict shape so callers are unchanged."""
    return [PendingChangeRow.from_row(dict(row)).to_dict() for row in rows]


def insert_pending_changes(item_key: str, item_title: str, changes: list[dict[str, Any]]) -> int:
    if not item_key.strip() or not changes:
        return 0

    rows: list[tuple[str, str, str, str]] = []
    for change in changes:
        change_type = str(change.get("change_type", "")).strip()
        payload = change.get("payload", {})
        if not change_type:
            continue
        rows.append(
            (
                item_key.strip(),
                str(item_title or "").strip(),
                change_type,
                json.dumps(payload, ensure_ascii=False),
            )
        )

    if not rows:
        return 0

    conn = _get_conn()
    try:
        conn.executemany(
            """
            INSERT INTO pending_changes (item_key, item_title, change_type, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def get_pending_changes(status: str | None = ChangeStatus.PENDING.value, limit: int = 500) -> list[dict[str, Any]]:
    safe_limit = max(1, min(limit, 5000))
    conn = _get_conn()
    try:
        if status:
            rows = conn.execute(
                """
                SELECT id, item_key, item_title, change_type, payload_json, status,
                       error_message, created_at, applied_at
                FROM pending_changes
                WHERE status = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (status, safe_limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, item_key, item_title, change_type, payload_json, status,
                       error_message, created_at, applied_at
                FROM pending_changes
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return _pending_rows(rows)
    finally:
        conn.close()


def get_pending_change_count(status: str = ChangeStatus.PENDING.value) -> int:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM pending_changes WHERE status = ?",
            (status,),
        ).fetchone()
        return int(row["cnt"] or 0) if row else 0
    finally:
        conn.close()


def get_pending_changes_by_ids(change_ids: list[int], status: str | None = None) -> list[dict[str, Any]]:
    normalized = [int(change_id) for change_id in change_ids if int(change_id) > 0]
    if not normalized:
        return []

    placeholders = ",".join("?" for _ in normalized)
    conn = _get_conn()
    try:
        if status:
            rows = conn.execute(
                f"""
                SELECT id, item_key, item_title, change_type, payload_json, status,
                       error_message, created_at, applied_at
                FROM pending_changes
                WHERE id IN ({placeholders}) AND status = ?
                ORDER BY created_at DESC
                """,
                [*normalized, status],
            ).fetchall()
        else:
            rows = conn.execute(
                f"""
                SELECT id, item_key, item_title, change_type, payload_json, status,
                       error_message, created_at, applied_at
                FROM pending_changes
                WHERE id IN ({placeholders})
                ORDER BY created_at DESC
                """,
                normalized,
            ).fetchall()
        return _pending_rows(rows)
    finally:
        conn.close()


def set_pending_changes_status(
    change_ids: list[int],
    status: str,
    error_message: str | None = None,
) -> int:
    normalized = [int(change_id) for change_id in change_ids if int(change_id) > 0]
    if not normalized:
        return 0

    placeholders = ",".join("?" for _ in normalized)
    conn = _get_conn()
    try:
        if status == ChangeStatus.APPLIED.value:
            cursor = conn.execute(
                f"""
                UPDATE pending_changes
                SET status = ?,
                    error_message = ?,
                    applied_at = datetime('now')
                WHERE id IN ({placeholders})
                  AND status = ?
                """,
                [status, (error_message or "").strip(), *normalized, ChangeStatus.PENDING.value],
            )
        elif status in {ChangeStatus.REJECTED.value, ChangeStatus.FAILED.value}:
            cursor = conn.execute(
                f"""
                UPDATE pending_changes
                SET status = ?,
                    error_message = ?
                WHERE id IN ({placeholders})
                  AND status = ?
                """,
                [status, (error_message or "").strip(), *normalized, ChangeStatus.PENDING.value],
            )
        else:
            cursor = conn.execute(
                f"""
                UPDATE pending_changes
                SET status = ?,
                    error_message = ?
                WHERE id IN ({placeholders})
                """,
                [status, (error_message or "").strip(), *normalized],
            )
        conn.commit()
        return int(cursor.rowcount or 0)
    finally:
        conn.close()


def update_pending_change_payload(change_id: int, payload: dict[str, Any]) -> bool:
    safe_id = int(change_id)
    if safe_id <= 0:
        return False

    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    conn = _get_conn()
    try:
        cursor = conn.execute(
            """
            UPDATE pending_changes
            SET payload_json = ?,
                error_message = ''
            WHERE id = ?
              AND status = ?
            """,
            (payload_json, safe_id, ChangeStatus.PENDING.value),
        )
        conn.commit()
        return int(cursor.rowcount or 0) > 0
    finally:
        conn.close()


__all__ = [
    "insert_pending_changes",
    "get_pending_changes",
    "get_pending_change_count",
    "get_pending_changes_by_ids",
    "set_pending_changes_status",
    "update_pending_change_payload",
]
