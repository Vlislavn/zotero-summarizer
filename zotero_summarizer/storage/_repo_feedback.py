"""repositories: feedback queries (split)."""
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


def insert_feedback_events(events: list[dict[str, Any]]) -> int:
    if not events:
        return 0

    normalized = []
    for event in events:
        item_id = str(event.get("item_id", "")).strip()
        feedback_type = str(event.get("feedback_type", "")).strip()
        signal = str(event.get("signal", "")).strip()
        if not item_id or not feedback_type or not signal:
            continue
        normalized.append(
            (
                item_id,
                feedback_type,
                signal,
                str(event.get("original_priority", "")).strip(),
                float(event.get("inferred_relevance", 1.0) or 1.0),
            )
        )

    if not normalized:
        return 0

    conn = _get_conn()
    try:
        conn.executemany(
            """
            INSERT INTO user_feedback (item_id, feedback_type, signal, original_priority, inferred_relevance)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(item_id, signal) DO UPDATE SET
                feedback_type = excluded.feedback_type,
                original_priority = excluded.original_priority,
                inferred_relevance = excluded.inferred_relevance,
                created_at = datetime('now')
            """,
            normalized,
        )
        conn.commit()
        return len(normalized)
    finally:
        conn.close()


def get_feedback_events(limit: int = 200) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 2000))
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT id, item_id, feedback_type, signal, original_priority, inferred_relevance, created_at
            FROM user_feedback
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return _rows_to_dicts(rows)
    finally:
        conn.close()


def delete_feedback_signals(item_id: str, signals: list[str]) -> int:
    safe_item_id = str(item_id or "").strip()
    safe_signals = [str(signal).strip() for signal in signals if str(signal).strip()]
    if not safe_item_id or not safe_signals:
        return 0

    placeholders = ",".join("?" for _ in safe_signals)
    conn = _get_conn()
    try:
        cursor = conn.execute(
            f"""
            DELETE FROM user_feedback
            WHERE item_id = ? AND signal IN ({placeholders})
            """,
            [safe_item_id, *safe_signals],
        )
        conn.commit()
        return int(cursor.rowcount or 0)
    finally:
        conn.close()


def get_latest_feedback_for_items(item_ids: list[str], signals: list[str] | None = None) -> dict[str, dict[str, Any]]:
    normalized_ids = [str(item_id).strip() for item_id in item_ids if str(item_id).strip()]
    normalized_signals = [str(signal).strip() for signal in (signals or []) if str(signal).strip()]
    if not normalized_ids:
        return {}

    id_placeholders = ",".join("?" for _ in normalized_ids)
    where_clauses = [f"item_id IN ({id_placeholders})"]
    query_params: list[Any] = [*normalized_ids]
    if normalized_signals:
        signal_placeholders = ",".join("?" for _ in normalized_signals)
        where_clauses.append(f"signal IN ({signal_placeholders})")
        query_params.extend(normalized_signals)

    where_sql = " AND ".join(where_clauses)

    conn = _get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT uf.id, uf.item_id, uf.feedback_type, uf.signal,
                   uf.original_priority, uf.inferred_relevance, uf.created_at
            FROM user_feedback uf
            JOIN (
                SELECT item_id, MAX(id) AS latest_id
                FROM user_feedback
                WHERE {where_sql}
                GROUP BY item_id
            ) latest ON uf.id = latest.latest_id
            """,
            query_params,
        ).fetchall()
        return {str(row["item_id"]): dict(row) for row in rows}
    finally:
        conn.close()


def get_latest_explicit_feedback_with_results(days: int | None = None) -> list[dict[str, Any]]:
    safe_days = None
    if days is not None:
        safe_days = max(1, min(int(days), 3650))

    signal_placeholders = ",".join("?" for _ in EXPLICIT_FEEDBACK_SIGNALS)
    day_filter_sql = ""
    query_params: list[Any] = [*EXPLICIT_FEEDBACK_SIGNALS]
    if safe_days is not None:
        day_filter_sql = " AND created_at >= datetime('now', ?)"
        query_params.append(f"-{safe_days} days")

    conn = _get_conn()
    try:
        rows = conn.execute(
            f"""
            WITH latest_feedback AS (
                SELECT uf.id, uf.item_id, uf.feedback_type, uf.signal,
                       uf.original_priority, uf.inferred_relevance, uf.created_at
                FROM user_feedback uf
                JOIN (
                    SELECT item_id, MAX(id) AS latest_id
                    FROM user_feedback
                    WHERE signal IN ({signal_placeholders}){day_filter_sql}
                    GROUP BY item_id
                ) lf ON uf.id = lf.latest_id
            ),
            latest_results AS (
                SELECT tr.item_id, tr.reading_priority, tr.composite_score, tr.relevance_score,
                       tr.confidence, tr.created_at AS triage_created_at
                FROM triage_results tr
                JOIN (
                    SELECT item_id, MAX(id) AS latest_id
                    FROM triage_results
                    GROUP BY item_id
                ) lr ON tr.id = lr.latest_id
            )
            SELECT
                latest_feedback.item_id,
                latest_feedback.feedback_type,
                latest_feedback.signal,
                latest_feedback.original_priority,
                latest_feedback.inferred_relevance,
                latest_feedback.created_at,
                latest_results.reading_priority,
                latest_results.composite_score,
                latest_results.relevance_score,
                latest_results.confidence,
                latest_results.triage_created_at
            FROM latest_feedback
            LEFT JOIN latest_results ON latest_results.item_id = latest_feedback.item_id
            ORDER BY latest_feedback.created_at DESC
            """,
            query_params,
        ).fetchall()
        return _rows_to_dicts(rows)
    finally:
        conn.close()


__all__ = [
    "insert_feedback_events",
    "get_feedback_events",
    "delete_feedback_signals",
    "get_latest_feedback_for_items",
    "get_latest_explicit_feedback_with_results",
]
