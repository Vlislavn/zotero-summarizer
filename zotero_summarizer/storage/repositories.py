"""SQLite persistence and batch-history queries for triage results."""

from __future__ import annotations

# Preserve existing facade names, including imports no longer used here.
import contextlib  # noqa: F401
import json  # noqa: F401
import logging
import os  # noqa: F401
import sqlite3
import threading  # noqa: F401
from contextvars import ContextVar  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any, Iterator  # noqa: F401

from zotero_summarizer.domain import ChangeStatus, READING_PRIORITY_SORT_RANK  # noqa: F401
from zotero_summarizer.settings import default_project_root  # noqa: F401
from zotero_summarizer.storage.db_core import (  # noqa: F401
    _AB_DECISION_THRESHOLD,
    _AB_WINNING_MARGIN,
    _ALLOWED_SORT,
    _DB_PATH_OVERRIDE,
    _VALID_AB_WINNERS,
    _VALID_LABEL_PRIORITIES,
    _VALID_VERDICTS,
    _WAL_LOCK,
    _WAL_PATHS,
    _connect_to,
    _get_conn,
    _json_to_list,
    _normalize_order,
    _normalize_sort,
    _resolve_db_path,
    _rows_to_dicts,
    _sort_expression,
    resolve_db_path,
    set_db_path,
    table_columns,
    with_db_path,
)

LOGGER = logging.getLogger("zotero_summarizer.db")


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
    source                     TEXT NOT NULL DEFAULT 'user',
    training_sample_json       TEXT,
    reasons_json               TEXT,
    proposal_json              TEXT,
    UNIQUE(item_key)
);
"""

# Review notes — one editable free-text note per paper, jotted during a review.
# Decoupled from label_verdicts (a verdict is a decision; a note is thinking).
# One row per item_key; the app save UPSERTs and best-effort mirrors to Zotero.
_CREATE_REVIEW_NOTES_TABLE = """
CREATE TABLE IF NOT EXISTS review_notes (
    item_key    TEXT PRIMARY KEY,
    note        TEXT NOT NULL,
    updated_at  TEXT NOT NULL
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
    conn.execute(_CREATE_REVIEW_NOTES_TABLE)
    feeds_storage.init_feeds_schema(conn)

    # June 2026 — verdict provenance. Pre-existing DBs get the column via the
    # established column-presence ALTER pattern (same as init_feeds_schema).
    # The backfill runs ONLY when the column is first added: verdicts written
    # by the "Add to library" path carry the frozen historical comment marker
    # below (daily_actions wrote that literal before `source` existed); the
    # comment is UPSERT-overwritten on any later deliberate relabel, so rows
    # still matching it are exactly the never-revisited machine adds.
    verdict_columns = table_columns(conn, "label_verdicts")
    if "source" not in verdict_columns:
        conn.execute(
            "ALTER TABLE label_verdicts ADD COLUMN source TEXT NOT NULL DEFAULT 'user'"
        )
        conn.execute(
            "UPDATE label_verdicts SET source = 'machine_add'"
            " WHERE comment = 'added from Today'"
        )
        feeds_storage.init_feeds_schema(conn)

    columns = table_columns(conn, "triage_results")
    if "batch_id" not in columns:
        conn.execute("ALTER TABLE triage_results ADD COLUMN batch_id TEXT")
    if "pdf_path" not in columns:
        conn.execute("ALTER TABLE triage_results ADD COLUMN pdf_path TEXT")
    if "prestige_score" not in columns:
        conn.execute("ALTER TABLE triage_results ADD COLUMN prestige_score REAL DEFAULT NULL")

    triage_job_columns = table_columns(conn, "triage_jobs")
    if "queue_changes" not in triage_job_columns:
        conn.execute("ALTER TABLE triage_jobs ADD COLUMN queue_changes INTEGER NOT NULL DEFAULT 1")
    if "item_keys_json" not in triage_job_columns:
        conn.execute("ALTER TABLE triage_jobs ADD COLUMN item_keys_json TEXT NOT NULL DEFAULT '[]'")
    if "results_json" not in triage_job_columns:
        conn.execute("ALTER TABLE triage_jobs ADD COLUMN results_json TEXT NOT NULL DEFAULT '[]'")
    if "errors_json" not in triage_job_columns:
        conn.execute("ALTER TABLE triage_jobs ADD COLUMN errors_json TEXT NOT NULL DEFAULT '[]'")

    triage_override_columns = table_columns(conn, "triage_dimension_overrides")
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
    # Import lazily: migrations owns ordered schema evolution and imports this
    # module for its v1 baseline.
    from zotero_summarizer.storage.migrations import run_migrations, TRIAGE_MIGRATIONS

    run_migrations(_resolve_db_path(), "triage", TRIAGE_MIGRATIONS)
    LOGGER.info("Database initialized at %s", _resolve_db_path())


# Data-access functions live in private _repo_* modules, re-exported here.
from zotero_summarizer.storage._repo_results import *  # noqa: F401,F403,E402
from zotero_summarizer.storage._repo_jobs import *  # noqa: F401,F403,E402
from zotero_summarizer.storage._repo_feedback import *  # noqa: F401,F403,E402
from zotero_summarizer.storage._repo_pending import *  # noqa: F401,F403,E402
from zotero_summarizer.storage._repo_pending import pending_change_exists  # noqa: F401,E402
from zotero_summarizer.storage._repo_pending import insert_pending_change_if_absent  # noqa: F401,E402
from zotero_summarizer.storage._repo_verdicts import *  # noqa: F401,F403,E402
from zotero_summarizer.storage._repo_labels import *  # noqa: F401,F403,E402
from zotero_summarizer.storage._repo_sync import *  # noqa: F401,F403,E402
