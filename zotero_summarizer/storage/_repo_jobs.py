"""repositories: jobs queries (split)."""
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


def _triage_job_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    payload["queue_changes"] = bool(int(payload.get("queue_changes") or 0))
    payload["item_keys"] = _json_to_list(payload.pop("item_keys_json", "[]"))
    payload["results"] = _json_to_list(payload.pop("results_json", "[]"))
    payload["errors"] = _json_to_list(payload.pop("errors_json", "[]"))
    payload["total"] = int(payload.get("total") or 0)
    payload["completed"] = int(payload.get("completed") or 0)
    return payload


def upsert_triage_job(job: dict[str, Any]) -> None:
    job_id = str(job.get("job_id") or "").strip()
    if not job_id:
        raise ValueError("job_id is required")

    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT INTO triage_jobs (
                job_id, status, started_at, updated_at, total, completed,
                current_item_key, current_title, queue_changes,
                item_keys_json, results_json, errors_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                status = excluded.status,
                started_at = excluded.started_at,
                updated_at = excluded.updated_at,
                total = excluded.total,
                completed = excluded.completed,
                current_item_key = excluded.current_item_key,
                current_title = excluded.current_title,
                queue_changes = excluded.queue_changes,
                item_keys_json = excluded.item_keys_json,
                results_json = excluded.results_json,
                errors_json = excluded.errors_json
            """,
            (
                job_id,
                str(job.get("status") or "running"),
                str(job.get("started_at") or ""),
                str(job.get("updated_at") or ""),
                int(job.get("total") or 0),
                int(job.get("completed") or 0),
                str(job.get("current_item_key") or ""),
                str(job.get("current_title") or ""),
                1 if bool(job.get("queue_changes", True)) else 0,
                json.dumps(job.get("item_keys") or [], ensure_ascii=False),
                json.dumps(job.get("results") or [], ensure_ascii=False),
                json.dumps(job.get("errors") or [], ensure_ascii=False),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_triage_job(job_id: str) -> dict[str, Any] | None:
    safe_job_id = str(job_id or "").strip()
    if not safe_job_id:
        return None

    conn = _get_conn()
    try:
        row = conn.execute(
            """
            SELECT job_id, status, started_at, updated_at, total, completed,
                   current_item_key, current_title, queue_changes,
                   item_keys_json, results_json, errors_json
            FROM triage_jobs
            WHERE job_id = ?
            """,
            (safe_job_id,),
        ).fetchone()
        return _triage_job_row_to_dict(row) if row else None
    finally:
        conn.close()


def list_triage_jobs(limit: int = 20, statuses: list[str] | None = None) -> list[dict[str, Any]]:
    safe_limit = max(1, min(limit, 500))
    normalized_statuses = [str(status).strip() for status in (statuses or []) if str(status).strip()]

    conn = _get_conn()
    try:
        if normalized_statuses:
            placeholders = ",".join("?" for _ in normalized_statuses)
            rows = conn.execute(
                f"""
                SELECT job_id, status, started_at, updated_at, total, completed,
                       current_item_key, current_title, queue_changes,
                       item_keys_json, results_json, errors_json
                FROM triage_jobs
                WHERE status IN ({placeholders})
                ORDER BY started_at DESC
                LIMIT ?
                """,
                [*normalized_statuses, safe_limit],
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT job_id, status, started_at, updated_at, total, completed,
                       current_item_key, current_title, queue_changes,
                       item_keys_json, results_json, errors_json
                FROM triage_jobs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [_triage_job_row_to_dict(row) for row in rows]
    finally:
        conn.close()


def mark_running_triage_jobs_interrupted() -> int:
    conn = _get_conn()
    try:
        cursor = conn.execute(
            """
            UPDATE triage_jobs
            SET status = 'interrupted',
                updated_at = datetime('now')
            WHERE status = 'running'
            """
        )
        conn.commit()
        return int(cursor.rowcount or 0)
    finally:
        conn.close()


__all__ = [
    "_triage_job_row_to_dict",
    "upsert_triage_job",
    "get_triage_job",
    "list_triage_jobs",
    "mark_running_triage_jobs_interrupted",
]
