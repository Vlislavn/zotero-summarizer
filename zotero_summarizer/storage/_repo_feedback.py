"""repositories: feedback queries (split)."""
from __future__ import annotations

import sqlite3
from typing import Any

from zotero_summarizer.domain import EXPLICIT_FEEDBACK_SIGNALS
from zotero_summarizer.storage.repositories import _get_conn, _rows_to_dicts


def insert_feedback_events(events: list[dict[str, Any]]) -> int:
    """Apply a batch atomically; each explicit verdict replaces its opposite."""
    if not events:
        return 0

    conn = _get_conn()
    try:
        _insert_feedback_events(conn, events)
        conn.commit()
        return len(events)
    finally:
        conn.close()


def _insert_feedback_events(conn: sqlite3.Connection, events: list[dict[str, Any]]) -> None:
    """Stage feedback in the caller's transaction; never commit here."""
    for event in events:
        item_id = event.get("item_id", "").strip()
        feedback_type = event.get("feedback_type", "").strip()
        signal = event.get("signal", "").strip()
        if not item_id or not feedback_type or not signal:
            raise ValueError("Feedback events require item_id, feedback_type and signal")
        if signal in EXPLICIT_FEEDBACK_SIGNALS:
            conn.execute(
                "DELETE FROM user_feedback WHERE item_id = ? AND signal IN (?, ?) AND signal != ?",
                (item_id, *EXPLICIT_FEEDBACK_SIGNALS, signal),
            )
        conn.execute(
            """
            INSERT INTO user_feedback (item_id, feedback_type, signal, original_priority, inferred_relevance)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(item_id, signal) DO UPDATE SET
                feedback_type = excluded.feedback_type,
                original_priority = excluded.original_priority,
                inferred_relevance = excluded.inferred_relevance,
                created_at = datetime('now')
            """,
            (
                item_id, feedback_type, signal,
                str(event.get("original_priority", "")).strip(),
                float(event.get("inferred_relevance", 1.0)),
            ),
        )


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


def get_latest_explicit_feedback() -> list[dict[str, Any]]:
    """One uncapped snapshot of decisions, their saved predictions and UTC ages."""
    signal_placeholders = ",".join("?" for _ in EXPLICIT_FEEDBACK_SIGNALS)
    conn = _get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT uf.item_id, uf.signal, uf.original_priority,
                   julianday(datetime('now')) - julianday(uf.created_at) AS age_days
            FROM user_feedback uf
            JOIN (
                SELECT item_id, MAX(id) AS latest_id
                FROM user_feedback
                WHERE signal IN ({signal_placeholders})
                GROUP BY item_id
            ) latest ON uf.id = latest.latest_id
            ORDER BY uf.created_at DESC
            """,
            EXPLICIT_FEEDBACK_SIGNALS,
        ).fetchall()
        return _rows_to_dicts(rows)
    finally:
        conn.close()


__all__ = [
    "insert_feedback_events",
    "get_feedback_events",
    "get_latest_feedback_for_items",
    "get_latest_explicit_feedback",
]
