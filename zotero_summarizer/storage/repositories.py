"""SQLite persistence and batch-history queries for triage results."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

from zotero_summarizer.domain import ChangeStatus, EXPLICIT_FEEDBACK_SIGNALS, READING_PRIORITY_SORT_RANK
from zotero_summarizer.settings import default_project_root

LOGGER = logging.getLogger("zotero_summarizer.db")

# The triage DB path. Resolved cheaply at import (no .env read) and overwritten
# once by ``lifecycle.startup`` / ``migrations`` with the real Settings value;
# tests monkeypatch it. It is set once at startup and only read concurrently
# thereafter, so plain reads from the feed daemon / API threads are safe.
DB_PATH = default_project_root() / "data" / "triage_history.db"

# Per-context path override. Used by ``TriageRepository`` and ``with_db_path`` to
# point the no-arg ``_get_conn`` at a different DB *without* mutating the module
# global. A ``ContextVar`` is isolated per thread and per asyncio task, so the
# feed daemon, API worker threads, and triage tasks never clobber each other's
# override (the old save/restore-on-a-global approach was racy).
_DB_PATH_OVERRIDE: ContextVar[Path | None] = ContextVar("triage_db_path_override", default=None)


def _resolve_db_path() -> Path:
    return _DB_PATH_OVERRIDE.get() or DB_PATH


@contextlib.contextmanager
def with_db_path(db_path: Path) -> Iterator[None]:
    """Scope all no-arg connections in this thread/task to *db_path*."""
    token = _DB_PATH_OVERRIDE.set(db_path)
    try:
        yield
    finally:
        _DB_PATH_OVERRIDE.reset(token)


class TriageRepository:
    """Object-oriented facade over the SQLite triage store.

    The module-level function API remains, but services/tests can depend on this
    facade to bind storage to a specific DB path. The binding is applied through
    a ``ContextVar`` (see :func:`with_db_path`), so it is concurrency-safe.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DB_PATH

    def _scoped(self, fn, *args: Any, **kwargs: Any):
        with with_db_path(self.db_path):
            return fn(*args, **kwargs)

    def init(self) -> None:
        self._scoped(init_db)

    def insert_result(self, *args: Any, **kwargs: Any) -> None:
        self._scoped(insert_result, *args, **kwargs)

    def get_result_by_item_id(self, item_id: str, batch_id: str | None = None) -> dict[str, Any] | None:
        return self._scoped(get_result_by_item_id, item_id, batch_id)

    def insert_pending_changes(self, item_key: str, item_title: str, changes: list[dict[str, Any]]) -> int:
        return self._scoped(insert_pending_changes, item_key, item_title, changes)

    def get_pending_changes(self, status: str | None = ChangeStatus.PENDING.value, limit: int = 500) -> list[dict[str, Any]]:
        return self._scoped(get_pending_changes, status, limit)

    def get_pending_changes_by_ids(self, change_ids: list[int], status: str | None = None) -> list[dict[str, Any]]:
        return self._scoped(get_pending_changes_by_ids, change_ids, status)

    def set_pending_changes_status(
        self,
        change_ids: list[int],
        status: str,
        error_message: str | None = None,
    ) -> int:
        return self._scoped(set_pending_changes_status, change_ids, status, error_message)

    def upsert_triage_job(self, job: dict[str, Any]) -> None:
        self._scoped(upsert_triage_job, job)

    def get_triage_job(self, job_id: str) -> dict[str, Any] | None:
        return self._scoped(get_triage_job, job_id)

    def list_triage_jobs(
        self,
        limit: int = 20,
        statuses: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return self._scoped(list_triage_jobs, limit, statuses)


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

# Phase 1.17 Step 2 — per-paper role-value verdicts for daily-5 validation.
_CREATE_ROLE_VALUE_VERDICTS_TABLE = """
CREATE TABLE IF NOT EXISTS role_value_verdicts (
    id              INTEGER PRIMARY KEY,
    item_key        TEXT NOT NULL,
    role            TEXT NOT NULL,
    verdict         TEXT NOT NULL CHECK (verdict IN ('worth', 'waste', 'unknown')),
    composite_score REAL,
    surprise_score  REAL,
    corpus_affinity REAL,
    created_at      TEXT NOT NULL
);
"""

# Phase 1.17 Step 4 — weekly A/B verdicts (roles vs pure-score).
_CREATE_WEEKLY_AB_VERDICTS_TABLE = """
CREATE TABLE IF NOT EXISTS weekly_ab_verdicts (
    id             INTEGER PRIMARY KEY,
    week_start     TEXT NOT NULL,
    winner         TEXT NOT NULL CHECK (winner IN ('roles', 'pure_score', 'tied')),
    slate_a_keys   TEXT NOT NULL,
    slate_b_keys   TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
"""

# Phase 1.18 Step 1 — user verdicts on derived labels. One row per paper;
# new verdict UPSERTs over the prior one so the table is auditable but small.
_CREATE_LABEL_VERDICTS_TABLE = """
CREATE TABLE IF NOT EXISTS label_verdicts (
    id                         INTEGER PRIMARY KEY,
    item_key                   TEXT NOT NULL,
    original_derived_priority  TEXT NOT NULL,
    user_priority              TEXT NOT NULL CHECK (user_priority IN (
        'must_read', 'should_read', 'could_read', 'dont_read'
    )),
    comment                    TEXT NOT NULL,
    created_at                 TEXT NOT NULL,
    UNIQUE(item_key)
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
    "CREATE INDEX IF NOT EXISTS idx_role_verdicts_role ON role_value_verdicts(role)",
    "CREATE INDEX IF NOT EXISTS idx_weekly_ab_week_start ON weekly_ab_verdicts(week_start)",
    "CREATE INDEX IF NOT EXISTS idx_label_verdicts_priority ON label_verdicts(user_priority)",
    "CREATE INDEX IF NOT EXISTS idx_label_verdicts_created_at ON label_verdicts(created_at)",
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
    db_path = _resolve_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if not db_path.exists():
        db_path.touch(mode=0o600)
    else:
        os.chmod(db_path, 0o600)
    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    return conn


def _get_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def apply_schema(conn: sqlite3.Connection) -> None:
    """Create/upgrade the full triage schema on *conn* (no commit).

    This is the baseline-schema step (migration v1, see ``storage.migrations``).
    It is idempotent — every ``CREATE TABLE IF NOT EXISTS`` / column-presence
    ``ALTER`` converges to the same shape on a fresh or existing DB — so it is
    safe to re-run on every startup via :func:`init_db`. New schema changes go
    in a *new* numbered migration step, not as more inline ALTERs here.
    """
    from zotero_summarizer.storage import feeds as feeds_storage

    conn.execute(_CREATE_BATCH_TABLE)
    conn.execute(_CREATE_RESULTS_TABLE)
    conn.execute(_CREATE_FEEDBACK_TABLE)
    conn.execute(_CREATE_PENDING_CHANGES_TABLE)
    conn.execute(_CREATE_TRIAGE_JOBS_TABLE)
    conn.execute(_CREATE_TRIAGE_DIMENSION_OVERRIDES_TABLE)
    conn.execute(_CREATE_ROLE_VALUE_VERDICTS_TABLE)
    conn.execute(_CREATE_WEEKLY_AB_VERDICTS_TABLE)
    conn.execute(_CREATE_LABEL_VERDICTS_TABLE)
    feeds_storage.init_feeds_schema(conn)

    columns = _get_columns(conn, "triage_results")
    if "batch_id" not in columns:
        conn.execute("ALTER TABLE triage_results ADD COLUMN batch_id TEXT")
    if "pdf_path" not in columns:
        conn.execute("ALTER TABLE triage_results ADD COLUMN pdf_path TEXT")
    if "prestige_score" not in columns:
        conn.execute("ALTER TABLE triage_results ADD COLUMN prestige_score REAL DEFAULT NULL")

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


def init_db() -> None:
    """Ensure the triage schema exists on the active DB (idempotent startup path)."""
    conn = _get_conn()
    try:
        apply_schema(conn)
        conn.commit()
    finally:
        conn.close()
    LOGGER.info("Database initialized at %s", _resolve_db_path())


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


# ---------------------------------------------------------------------------
# Phase 1.17 — role-value verdicts + weekly A/B (Steps 2 and 4)
# ---------------------------------------------------------------------------


_VALID_VERDICTS = ("worth", "waste", "unknown")
_VALID_AB_WINNERS = ("roles", "pure_score", "tied")
_AB_DECISION_THRESHOLD = 8     # weeks needed before decision rule fires
_AB_WINNING_MARGIN = 6         # >=6/8 locks the decision


def _connect_to(db_path: Path) -> sqlite3.Connection:
    """Open a connection to *db_path* with the same hardening as ``_get_conn``."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if not db_path.exists():
        db_path.touch(mode=0o600)
    else:
        os.chmod(db_path, 0o600)
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    # Set busy_timeout BEFORE switching journal mode: the WAL switch needs a
    # brief exclusive lock, and under concurrent writers (API + feed daemon)
    # it would otherwise raise "database is locked" before Python's connect
    # timeout applies. busy_timeout makes every statement, including the WAL
    # switch, wait for the lock at the SQLite C level.
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ---------------------------------------------------------------------------
# Phase 1.18 Step 1 — label verdicts (user audit of derived priorities)
# ---------------------------------------------------------------------------


_VALID_LABEL_PRIORITIES = (
    "must_read", "should_read", "could_read", "dont_read",
)


# Data-access functions live in private _repo_* modules, re-exported here.
from zotero_summarizer.storage._repo_results import *  # noqa: F401,F403,E402
from zotero_summarizer.storage._repo_jobs import *  # noqa: F401,F403,E402
from zotero_summarizer.storage._repo_feedback import *  # noqa: F401,F403,E402
from zotero_summarizer.storage._repo_pending import *  # noqa: F401,F403,E402
from zotero_summarizer.storage._repo_verdicts import *  # noqa: F401,F403,E402
from zotero_summarizer.storage._repo_labels import *  # noqa: F401,F403,E402
