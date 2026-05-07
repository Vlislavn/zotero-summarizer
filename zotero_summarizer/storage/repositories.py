"""SQLite persistence and batch-history queries for triage results."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from zotero_summarizer.domain import ChangeStatus, EXPLICIT_FEEDBACK_SIGNALS, READING_PRIORITY_SORT_RANK
from zotero_summarizer.settings import Settings

LOGGER = logging.getLogger("zotero_summarizer.db")
DB_PATH = Settings.load().triage_db_path


class TriageRepository:
    """Small object-oriented facade over the SQLite triage store.

    The module-level function API remains for transition support, but services should
    depend on this facade so storage can be injected in tests.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DB_PATH

    def _with_db_path(self, fn, *args: Any, **kwargs: Any):
        global DB_PATH
        previous = DB_PATH
        DB_PATH = self.db_path
        try:
            return fn(*args, **kwargs)
        finally:
            DB_PATH = previous

    def init(self) -> None:
        self._with_db_path(init_db)

    def insert_result(self, *args: Any, **kwargs: Any) -> None:
        self._with_db_path(insert_result, *args, **kwargs)

    def get_result_by_item_id(self, item_id: str, batch_id: str | None = None) -> dict[str, Any] | None:
        return self._with_db_path(get_result_by_item_id, item_id, batch_id)

    def insert_pending_changes(self, item_key: str, item_title: str, changes: list[dict[str, Any]]) -> int:
        return self._with_db_path(insert_pending_changes, item_key, item_title, changes)

    def get_pending_changes(self, status: str | None = ChangeStatus.PENDING.value, limit: int = 500) -> list[dict[str, Any]]:
        return self._with_db_path(get_pending_changes, status, limit)

    def get_pending_changes_by_ids(self, change_ids: list[int], status: str | None = None) -> list[dict[str, Any]]:
        return self._with_db_path(get_pending_changes_by_ids, change_ids, status)

    def set_pending_changes_status(
        self,
        change_ids: list[int],
        status: str,
        error_message: str | None = None,
    ) -> int:
        return self._with_db_path(set_pending_changes_status, change_ids, status, error_message)

    def upsert_triage_job(self, job: dict[str, Any]) -> None:
        self._with_db_path(upsert_triage_job, job)

    def get_triage_job(self, job_id: str) -> dict[str, Any] | None:
        return self._with_db_path(get_triage_job, job_id)

    def list_triage_jobs(
        self,
        limit: int = 20,
        statuses: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return self._with_db_path(list_triage_jobs, limit, statuses)

_CREATE_BATCH_TABLE = """
CREATE TABLE IF NOT EXISTS batch_runs (
    batch_id         TEXT PRIMARY KEY,
    total_items      INTEGER NOT NULL,
    successful_items INTEGER NOT NULL,
    failed_items     INTEGER NOT NULL,
    created_at       TEXT DEFAULT (datetime('now'))
);
"""

_CREATE_RESULTS_TABLE = """
CREATE TABLE IF NOT EXISTS triage_results (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id          TEXT,
    item_id           TEXT NOT NULL,
    title             TEXT NOT NULL,
    relevance_score   INTEGER,
    composite_score   REAL,
    reading_priority  TEXT,
    forced_priority   TEXT,
    normalized_score  REAL,
    percentile        REAL,
    rank              INTEGER,
    confidence        REAL,
    pdf_path          TEXT,
    response_json     TEXT NOT NULL,
    created_at        TEXT DEFAULT (datetime('now')),
    FOREIGN KEY(batch_id) REFERENCES batch_runs(batch_id)
);
"""

_CREATE_FEEDBACK_TABLE = """
CREATE TABLE IF NOT EXISTS user_feedback (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id           TEXT NOT NULL,
    feedback_type     TEXT NOT NULL,
    signal            TEXT NOT NULL,
    original_priority TEXT,
    inferred_relevance REAL,
    created_at        TEXT DEFAULT (datetime('now'))
);
"""

_CREATE_PENDING_CHANGES_TABLE = f"""
CREATE TABLE IF NOT EXISTS pending_changes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    item_key          TEXT NOT NULL,
    item_title        TEXT,
    change_type       TEXT NOT NULL,
    payload_json      TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT '{ChangeStatus.PENDING.value}',
    error_message     TEXT,
    created_at        TEXT DEFAULT (datetime('now')),
    applied_at        TEXT
);
"""

_CREATE_TRIAGE_JOBS_TABLE = """
CREATE TABLE IF NOT EXISTS triage_jobs (
    job_id            TEXT PRIMARY KEY,
    status            TEXT NOT NULL,
    started_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    total             INTEGER NOT NULL DEFAULT 0,
    completed         INTEGER NOT NULL DEFAULT 0,
    current_item_key  TEXT NOT NULL DEFAULT '',
    current_title     TEXT NOT NULL DEFAULT '',
    queue_changes     INTEGER NOT NULL DEFAULT 1,
    item_keys_json    TEXT NOT NULL DEFAULT '[]',
    results_json      TEXT NOT NULL DEFAULT '[]',
    errors_json       TEXT NOT NULL DEFAULT '[]'
);
"""

_CREATE_TRIAGE_DIMENSION_OVERRIDES_TABLE = """
CREATE TABLE IF NOT EXISTS triage_dimension_overrides (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id                  TEXT NOT NULL,
    result_row_id            INTEGER,
    original_dimensions_json TEXT NOT NULL,
    override_dimensions_json TEXT NOT NULL,
    merged_dimensions_json   TEXT NOT NULL,
    corpus_affinity          REAL NOT NULL,
    original_composite_score REAL,
    new_composite_score      REAL NOT NULL,
    original_priority        TEXT,
    new_priority             TEXT NOT NULL,
    created_at               TEXT DEFAULT (datetime('now')),
    FOREIGN KEY(result_row_id) REFERENCES triage_results(id)
);
"""

_INDEX_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS idx_results_item_id ON triage_results(item_id)",
    "CREATE INDEX IF NOT EXISTS idx_results_batch_id ON triage_results(batch_id)",
    "CREATE INDEX IF NOT EXISTS idx_results_created_at ON triage_results(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_feedback_item_id ON user_feedback(item_id)",
    "CREATE INDEX IF NOT EXISTS idx_feedback_created_at ON user_feedback(created_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_item_signal ON user_feedback(item_id, signal)",
    "CREATE INDEX IF NOT EXISTS idx_pending_changes_status ON pending_changes(status)",
    "CREATE INDEX IF NOT EXISTS idx_pending_changes_item_key ON pending_changes(item_key)",
    "CREATE INDEX IF NOT EXISTS idx_pending_changes_created_at ON pending_changes(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_triage_jobs_status ON triage_jobs(status)",
    "CREATE INDEX IF NOT EXISTS idx_triage_jobs_started_at ON triage_jobs(started_at)",
    "CREATE INDEX IF NOT EXISTS idx_triage_dim_overrides_item_id ON triage_dimension_overrides(item_id)",
    "CREATE INDEX IF NOT EXISTS idx_triage_dim_overrides_created_at ON triage_dimension_overrides(created_at)",
]

_ALLOWED_SORT = {
    "composite_score",
    "relevance_score",
    "normalized_score",
    "percentile",
    "rank",
    "confidence",
    "created_at",
    "title",
    "batch_created_at",
    "forced_priority",
}


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        DB_PATH.touch(mode=0o600)
    else:
        os.chmod(DB_PATH, 0o600)
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    return conn


def _get_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def init_db() -> None:
    """Create or migrate storage schema to support batch history."""
    conn = _get_conn()
    try:
        conn.execute(_CREATE_BATCH_TABLE)
        conn.execute(_CREATE_RESULTS_TABLE)
        conn.execute(_CREATE_FEEDBACK_TABLE)
        conn.execute(_CREATE_PENDING_CHANGES_TABLE)
        conn.execute(_CREATE_TRIAGE_JOBS_TABLE)
        conn.execute(_CREATE_TRIAGE_DIMENSION_OVERRIDES_TABLE)

        columns = _get_columns(conn, "triage_results")
        if "batch_id" not in columns:
            conn.execute("ALTER TABLE triage_results ADD COLUMN batch_id TEXT")
        if "pdf_path" not in columns:
            conn.execute("ALTER TABLE triage_results ADD COLUMN pdf_path TEXT")

        triage_job_columns = _get_columns(conn, "triage_jobs")
        if "queue_changes" not in triage_job_columns:
            conn.execute("ALTER TABLE triage_jobs ADD COLUMN queue_changes INTEGER NOT NULL DEFAULT 1")
        if "item_keys_json" not in triage_job_columns:
            conn.execute("ALTER TABLE triage_jobs ADD COLUMN item_keys_json TEXT NOT NULL DEFAULT '[]'")
        if "results_json" not in triage_job_columns:
            conn.execute("ALTER TABLE triage_jobs ADD COLUMN results_json TEXT NOT NULL DEFAULT '[]'")
        if "errors_json" not in triage_job_columns:
            conn.execute("ALTER TABLE triage_jobs ADD COLUMN errors_json TEXT NOT NULL DEFAULT '[]'")

        triage_override_columns = _get_columns(conn, "triage_dimension_overrides")
        override_column_defs = {
            "item_id": "TEXT NOT NULL DEFAULT ''",
            "result_row_id": "INTEGER",
            "original_dimensions_json": "TEXT NOT NULL DEFAULT '{}'",
            "override_dimensions_json": "TEXT NOT NULL DEFAULT '{}'",
            "merged_dimensions_json": "TEXT NOT NULL DEFAULT '{}'",
            "corpus_affinity": "REAL NOT NULL DEFAULT 0",
            "original_composite_score": "REAL",
            "new_composite_score": "REAL NOT NULL DEFAULT 0",
            "original_priority": "TEXT",
            "new_priority": "TEXT NOT NULL DEFAULT ''",
            "created_at": "TEXT DEFAULT (datetime('now'))",
        }
        for column_name, column_sql in override_column_defs.items():
            if column_name not in triage_override_columns:
                conn.execute(
                    f"ALTER TABLE triage_dimension_overrides ADD COLUMN {column_name} {column_sql}"
                )

        # Drop legacy unique index so repeated processing across batches is preserved.
        conn.execute("DROP INDEX IF EXISTS idx_item_id")
        for statement in _INDEX_STATEMENTS:
            conn.execute(statement)
        conn.commit()
    finally:
        conn.close()
    LOGGER.info("Database initialized at %s", DB_PATH)


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
) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT INTO triage_results (
                batch_id, item_id, title, relevance_score, composite_score,
                reading_priority, forced_priority, normalized_score, percentile,
                rank, confidence, pdf_path, response_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    result_row_id: int | None,
    original_dimensions: dict[str, Any],
    override_dimensions: dict[str, Any],
    merged_dimensions: dict[str, Any],
    corpus_affinity: float,
    original_composite_score: float | None,
    new_composite_score: float,
    original_priority: str,
    new_priority: str,
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


def _normalize_order(order: str) -> str:
    normalized = order.lower()
    return normalized if normalized in {"asc", "desc"} else "desc"


def _normalize_sort(sort_by: str) -> str:
    return sort_by if sort_by in _ALLOWED_SORT else "composite_score"


def _sort_expression(sort_by: str) -> str:
    if sort_by == "forced_priority":
        priority_case = " ".join(
            f"WHEN '{priority}' THEN {rank}"
            for priority, rank in READING_PRIORITY_SORT_RANK.items()
        )
        return f"CASE forced_priority {priority_case} ELSE 0 END"
    return sort_by


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _json_to_list(raw_value: Any) -> list[Any]:
    if isinstance(raw_value, list):
        return raw_value
    if not isinstance(raw_value, str):
        return []
    try:
        decoded = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    if isinstance(decoded, list):
        return decoded
    return []


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


def get_latest_results(
    sort_by: str = "composite_score",
    order: str = "desc",
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    sort_by = _normalize_sort(sort_by)
    sort_expression = _sort_expression(sort_by)
    order = _normalize_order(order)
    conn = _get_conn()
    try:
        rows = conn.execute(
            f"""
            WITH latest AS (
                SELECT item_id, MAX(id) AS latest_id
                FROM triage_results
                GROUP BY item_id
            )
            SELECT r.*, b.created_at AS batch_created_at
            FROM triage_results r
            JOIN latest l ON r.id = l.latest_id
            LEFT JOIN batch_runs b ON r.batch_id = b.batch_id
            ORDER BY {sort_expression} {order}
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        return _rows_to_dicts(rows)
    finally:
        conn.close()


def get_all_results(
    sort_by: str = "composite_score",
    order: str = "desc",
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    sort_by = _normalize_sort(sort_by)
    sort_expression = _sort_expression(sort_by)
    order = _normalize_order(order)
    conn = _get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT r.*, b.created_at AS batch_created_at
            FROM triage_results r
            LEFT JOIN batch_runs b ON r.batch_id = b.batch_id
            ORDER BY {sort_expression} {order}
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        return _rows_to_dicts(rows)
    finally:
        conn.close()


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
        return _rows_to_dicts(rows)
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
        return _rows_to_dicts(rows)
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
