"""repositories: results queries (split)."""
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


def create_batch_run(batch_id: str, total_items: int, successful_items: int, failed_items: int) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT INTO batch_runs (batch_id, total_items, successful_items, failed_items)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(batch_id) DO UPDATE SET
                total_items = excluded.total_items,
                successful_items = excluded.successful_items,
                failed_items = excluded.failed_items
            """,
            (batch_id, total_items, successful_items, failed_items),
        )
        conn.commit()
    finally:
        conn.close()


def insert_result(
    item_id: str,
    title: str,
    response_dict: dict[str, Any],
    batch_id: str | None = None,
    forced_priority: str | None = None,
    normalized_score: float | None = None,
    percentile: float | None = None,
    rank: int | None = None,
    pdf_path: str | None = None,
    prestige_score: float | None = None,
) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT INTO triage_results (
                batch_id, item_id, title, relevance_score, composite_score,
                reading_priority, forced_priority, normalized_score, percentile,
                rank, confidence, pdf_path, response_json, prestige_score
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                item_id,
                title,
                response_dict.get("relevance_score"),
                response_dict.get("composite_relevance_score"),
                response_dict.get("reading_priority"),
                forced_priority if forced_priority is not None else response_dict.get("reading_priority"),
                normalized_score if normalized_score is not None else 0.0,
                percentile if percentile is not None else 0.0,
                rank if rank is not None else 0,
                response_dict.get("triage_confidence", 0.0),
                pdf_path,
                json.dumps(response_dict, ensure_ascii=False),
                prestige_score if prestige_score is not None else response_dict.get("prestige_score"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def update_result_row_after_override(
    result_row_id: int,
    response_dict: dict[str, Any],
    composite_score: float,
    reading_priority: str,
    forced_priority: str,
) -> bool:
    safe_result_row_id = int(result_row_id)
    if safe_result_row_id <= 0:
        return False

    conn = _get_conn()
    try:
        cursor = conn.execute(
            """
            UPDATE triage_results
            SET response_json = ?,
                composite_score = ?,
                reading_priority = ?,
                forced_priority = ?
            WHERE id = ?
            """,
            (
                json.dumps(response_dict or {}, ensure_ascii=False),
                float(composite_score),
                str(reading_priority or "").strip(),
                str(forced_priority or "").strip(),
                safe_result_row_id,
            ),
        )
        conn.commit()
        return int(cursor.rowcount or 0) > 0
    finally:
        conn.close()


def insert_triage_dimension_override(
    item_id: str,
    original_dimensions: dict[str, Any],
    override_dimensions: dict[str, Any],
    merged_dimensions: dict[str, Any],
    new_composite_score: float,
    new_priority: str,
    *,
    result_row_id: int | None = None,
    corpus_affinity: float = 0.0,
    original_composite_score: float | None = None,
    original_priority: str = "",
) -> int:
    safe_item_id = str(item_id or "").strip()
    if not safe_item_id:
        return 0

    safe_result_row_id = int(result_row_id) if result_row_id else None
    conn = _get_conn()
    try:
        cursor = conn.execute(
            """
            INSERT INTO triage_dimension_overrides (
                item_id,
                result_row_id,
                original_dimensions_json,
                override_dimensions_json,
                merged_dimensions_json,
                corpus_affinity,
                original_composite_score,
                new_composite_score,
                original_priority,
                new_priority
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                safe_item_id,
                safe_result_row_id,
                json.dumps(original_dimensions or {}, ensure_ascii=False),
                json.dumps(override_dimensions or {}, ensure_ascii=False),
                json.dumps(merged_dimensions or {}, ensure_ascii=False),
                float(corpus_affinity),
                None if original_composite_score is None else float(original_composite_score),
                float(new_composite_score),
                str(original_priority or "").strip(),
                str(new_priority or "").strip(),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid or 0)
    finally:
        conn.close()


def _select_results(
    body_sql: str, *, sort_by: str, order: str, limit: int, offset: int
) -> list[dict[str, Any]]:
    """Run a ``SELECT … FROM triage_results`` body with the shared ORDER BY/LIMIT tail.

    ``body_sql`` is the trusted in-source query prefix (optionally a ``WITH … `` CTE);
    the validated sort/order are appended, never interpolated from caller input.
    """
    sort_expression = _sort_expression(_normalize_sort(sort_by))
    safe_order = _normalize_order(order)
    conn = _get_conn()
    try:
        rows = conn.execute(
            f"{body_sql}\nORDER BY {sort_expression} {safe_order}\nLIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return _rows_to_dicts(rows)
    finally:
        conn.close()


def get_latest_results(
    sort_by: str = "composite_score",
    order: str = "desc",
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    return _select_results(
        """
        WITH latest AS (
            SELECT item_id, MAX(id) AS latest_id
            FROM triage_results
            GROUP BY item_id
        )
        SELECT r.*, b.created_at AS batch_created_at
        FROM triage_results r
        JOIN latest l ON r.id = l.latest_id
        LEFT JOIN batch_runs b ON r.batch_id = b.batch_id
        """,
        sort_by=sort_by, order=order, limit=limit, offset=offset,
    )


def get_all_results(
    sort_by: str = "composite_score",
    order: str = "desc",
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    return _select_results(
        """
        SELECT r.*, b.created_at AS batch_created_at
        FROM triage_results r
        LEFT JOIN batch_runs b ON r.batch_id = b.batch_id
        """,
        sort_by=sort_by, order=order, limit=limit, offset=offset,
    )


def get_results_by_batch_ids(
    batch_ids: list[str],
    sort_by: str = "composite_score",
    order: str = "desc",
    limit: int = 500,
    offset: int = 0,
) -> list[dict[str, Any]]:
    if not batch_ids:
        return []
    sort_by = _normalize_sort(sort_by)
    sort_expression = _sort_expression(sort_by)
    order = _normalize_order(order)
    placeholders = ",".join("?" for _ in batch_ids)
    params: list[Any] = [*batch_ids, limit, offset]
    conn = _get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT r.*, b.created_at AS batch_created_at
            FROM triage_results r
            LEFT JOIN batch_runs b ON r.batch_id = b.batch_id
            WHERE r.batch_id IN ({placeholders})
            ORDER BY {sort_expression} {order}
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
        return _rows_to_dicts(rows)
    finally:
        conn.close()


def get_result_by_item_id(item_id: str, batch_id: str | None = None) -> dict[str, Any] | None:
    conn = _get_conn()
    try:
        if batch_id:
            row = conn.execute(
                """
                SELECT r.*, b.created_at AS batch_created_at
                FROM triage_results r
                LEFT JOIN batch_runs b ON r.batch_id = b.batch_id
                WHERE r.item_id = ? AND r.batch_id = ?
                ORDER BY r.id DESC
                LIMIT 1
                """,
                (item_id, batch_id),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT r.*, b.created_at AS batch_created_at
                FROM triage_results r
                LEFT JOIN batch_runs b ON r.batch_id = b.batch_id
                WHERE r.item_id = ?
                ORDER BY r.id DESC
                LIMIT 1
                """,
                (item_id,),
            ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_latest_results_for_items(item_ids: list[str]) -> dict[str, dict[str, Any]]:
    normalized_ids = [str(item_id).strip() for item_id in item_ids if str(item_id).strip()]
    if not normalized_ids:
        return {}

    placeholders = ",".join("?" for _ in normalized_ids)
    conn = _get_conn()
    try:
        rows = conn.execute(
            f"""
            WITH latest AS (
                SELECT item_id, MAX(id) AS latest_id
                FROM triage_results
                WHERE item_id IN ({placeholders})
                GROUP BY item_id
            )
            SELECT r.*
            FROM triage_results r
            JOIN latest l ON r.id = l.latest_id
            """,
            normalized_ids,
        ).fetchall()
        return {str(row["item_id"]): dict(row) for row in rows}
    finally:
        conn.close()


def get_result_count(scope: str = "latest", batch_ids: list[str] | None = None) -> int:
    safe_scope = str(scope or "latest").strip().lower()
    if safe_scope not in {"latest", "all", "batch", "compare"}:
        safe_scope = "latest"

    conn = _get_conn()
    try:
        if safe_scope == "latest":
            row = conn.execute("SELECT COUNT(DISTINCT item_id) AS cnt FROM triage_results").fetchone()
            return row["cnt"] if row else 0
        if safe_scope == "all":
            row = conn.execute("SELECT COUNT(*) AS cnt FROM triage_results").fetchone()
            return row["cnt"] if row else 0
        if safe_scope in {"batch", "compare"} and batch_ids:
            placeholders = ",".join("?" for _ in batch_ids)
            row = conn.execute(
                f"SELECT COUNT(*) AS cnt FROM triage_results WHERE batch_id IN ({placeholders})",
                batch_ids,
            ).fetchone()
            return row["cnt"] if row else 0
        return 0
    finally:
        conn.close()


def get_batch_runs(limit: int = 20) -> list[dict[str, Any]]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT batch_id, total_items, successful_items, failed_items, created_at
            FROM batch_runs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return _rows_to_dicts(rows)
    finally:
        conn.close()


def delete_result(item_id: str) -> bool:
    conn = _get_conn()
    try:
        cursor = conn.execute("DELETE FROM triage_results WHERE item_id = ?", (item_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


__all__ = [
    "create_batch_run",
    "insert_result",
    "update_result_row_after_override",
    "insert_triage_dimension_override",
    "get_latest_results",
    "get_all_results",
    "get_results_by_batch_ids",
    "get_result_by_item_id",
    "get_latest_results_for_items",
    "get_result_count",
    "get_batch_runs",
    "delete_result",
]
