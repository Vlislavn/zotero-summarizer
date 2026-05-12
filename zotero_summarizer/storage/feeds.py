"""Storage layer for RSS-feed batch processing.

The `processed_feed_items` table is the durable record of every feed item the
agent has decided on, across both Phase 1 (one-shot batch) and Phase 1.5
(continuous daemon + daily selection + outcome feedback).

State machine for `decision` (one row per (feed_library_id, feed_item_id)):

    [first sighting in a tick]
            |
            v
    triaged_pending   ----+----> selected           ----> kept_inbox / moved_collection / deleted_all / trashed
                          |       (materialized)
                          +----> black_swan         ----> (same outcome set)
                          +----> rejected_daily_cutoff (never materialized)
                          +----> rejected_low_score (corpus fast-reject; no LLM)
                          +----> rejected_dedup_library (already in user's library)
                          +----> skipped_error (LLM failure, fatal endpoint error, etc.)

The (feed_library_id, feed_item_id) UNIQUE constraint enforces idempotency:
once the daemon has triaged an item, re-fetching the same item from Zotero
is skipped via `filter_unprocessed()`. State transitions to a terminal
decision happen via `update_to_decision()`.

Outcome detection (Phase 1.5):
  - When the daemon materializes an item, `record_materialization()` sets
    `materialized_zotero_key` + `outcome_eligible_at = now + outcome_window_days`.
  - On each daemon tick, a small number of due rows are picked via
    `due_outcome_checks()` and the daemon queries Zotero for membership;
    `record_outcome()` writes the final outcome + signal weight back.

Persisted in the existing triage_history.db alongside the rest of the
triage state (see storage/repositories.py).
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

LOGGER = logging.getLogger("zotero_summarizer.storage.feeds")


# Decision taxonomy — keep in sync with services/feeds.py.
DECISION_TRIAGED_PENDING = "triaged_pending"  # LLM-scored, awaiting daily selection
DECISION_SELECTED = "selected"
DECISION_BLACK_SWAN = "black_swan"
DECISION_REJECTED_DAILY_CUTOFF = "rejected_daily_cutoff"  # below daily plateau
DECISION_REJECTED_ELBOW = "rejected_elbow"  # legacy Phase 1 (one-shot batch)
DECISION_REJECTED_LOW_SCORE = "rejected_low_score"  # corpus fast-reject
DECISION_REJECTED_DEDUP_LIBRARY = "rejected_dedup_library"
DECISION_REJECTED_DEDUP_PROCESSED = "rejected_dedup_processed"
DECISION_SKIPPED_ERROR = "skipped_error"

# Terminal decisions — these get final outcomes; the "triaged_pending"
# intermediate state never receives an outcome.
TERMINAL_MATERIALIZED_DECISIONS = frozenset({DECISION_SELECTED, DECISION_BLACK_SWAN})

# Outcome taxonomy — what the user did with a materialized item after N days.
OUTCOME_PENDING = "pending"  # outcome window not yet elapsed
OUTCOME_KEPT_INBOX = "kept_inbox"  # still in Inbox only — weak negative
OUTCOME_MOVED_COLLECTION = "moved_collection"  # moved out of Inbox to a real collection — weak positive
OUTCOME_DELETED_ALL = "deleted_all"  # removed from every collection — strong negative
OUTCOME_TRASHED = "trashed"  # moved to Zotero trash — strong negative
OUTCOME_ENGAGED = "engaged"  # has 🧠 or 👀 tag — strong positive
OUTCOME_UNKNOWN = "unknown"  # item key resolved to nothing (hard-delete, merge edge case)

# Signal weights — asymmetric per Schnabel et al. ICML 2016
# (Recommendations as Treatments, arXiv:1602.05352). Industrial-feed convention
# (YouTube/Pinterest/Meta) is delete ≈ 3–10× ignore. We sit at 6× (3.0 vs 0.5).
OUTCOME_WEIGHT = {
    OUTCOME_ENGAGED: 3.0,
    OUTCOME_MOVED_COLLECTION: 1.0,
    OUTCOME_KEPT_INBOX: -0.5,
    OUTCOME_DELETED_ALL: -3.0,
    OUTCOME_TRASHED: -3.0,
    OUTCOME_UNKNOWN: -1.0,
}


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS processed_feed_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_library_id INTEGER NOT NULL,
    feed_item_id INTEGER NOT NULL,
    guid TEXT NOT NULL,
    title TEXT NOT NULL,
    doi TEXT,
    arxiv_id TEXT,
    feed_name TEXT,
    decision TEXT NOT NULL,
    decision_reason TEXT NOT NULL DEFAULT '',
    composite_score REAL,
    surprise_score REAL,
    corpus_affinity REAL,
    reading_priority TEXT,
    is_black_swan INTEGER NOT NULL DEFAULT 0,
    model_version TEXT,
    run_id TEXT NOT NULL,
    planned_zotero_key TEXT,
    matched_collections_json TEXT,
    error TEXT,
    -- Phase 1.5 outcome-feedback columns
    materialized_zotero_key TEXT,
    outcome_eligible_at TEXT,
    outcome_detected_at TEXT,
    final_outcome TEXT,
    outcome_signal_weight REAL,
    read_time_marked_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(feed_library_id, feed_item_id)
)
"""

_INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_processed_feed_run ON processed_feed_items(run_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_processed_feed_guid ON processed_feed_items(guid)",
    "CREATE INDEX IF NOT EXISTS idx_processed_feed_decision ON processed_feed_items(decision, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_processed_feed_zotero_key ON processed_feed_items(materialized_zotero_key)",
    "CREATE INDEX IF NOT EXISTS idx_processed_feed_outcome_due ON processed_feed_items(outcome_eligible_at, outcome_detected_at)",
)

# Phase 1.5 migration: add new columns to pre-existing Phase 1 databases.
# Note: SQLite ALTER TABLE does NOT support non-constant defaults like
# `datetime('now')`, so `updated_at` is added without a default. The CREATE
# TABLE path (fresh DB) does include the default — both code paths converge.
# Existing Phase 1 rows get NULL for `updated_at` until their next update.
_MIGRATION_COLUMNS = (
    ("materialized_zotero_key", "TEXT"),
    ("outcome_eligible_at", "TEXT"),
    ("outcome_detected_at", "TEXT"),
    ("final_outcome", "TEXT"),
    ("outcome_signal_weight", "REAL"),
    ("read_time_marked_at", "TEXT"),
    ("updated_at", "TEXT"),
)


def init_feeds_schema(conn: sqlite3.Connection) -> None:
    """Create the processed_feed_items table + indexes; migrate Phase 1 DBs.

    Idempotent. Safe to call on every app start.
    """
    conn.execute(_CREATE_TABLE)
    # Migrate any pre-Phase-1.5 DBs by adding missing columns.
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(processed_feed_items)").fetchall()}
    for col_name, col_def in _MIGRATION_COLUMNS:
        if col_name not in existing_cols:
            try:
                conn.execute(f"ALTER TABLE processed_feed_items ADD COLUMN {col_name} {col_def}")
            except sqlite3.OperationalError as exc:
                LOGGER.warning("Failed to add column %s: %s", col_name, exc)
    for stmt in _INDEX_STATEMENTS:
        conn.execute(stmt)


def new_run_id(prefix: str = "feeds") -> str:
    """Generate a stable, human-readable run/tick identifier."""
    return datetime.now(timezone.utc).strftime(f"{prefix}_%Y%m%d_%H%M%S_%f")


def is_processed(conn: sqlite3.Connection, feed_library_id: int, feed_item_id: int) -> bool:
    """Return True if this feed item already has a recorded decision."""
    row = conn.execute(
        "SELECT 1 FROM processed_feed_items WHERE feed_library_id=? AND feed_item_id=? LIMIT 1",
        (feed_library_id, feed_item_id),
    ).fetchone()
    return row is not None


def filter_unprocessed(
    conn: sqlite3.Connection,
    feed_items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Return (unprocessed, skipped_count) — items not yet recorded in this DB.

    Daemon resumability: a crash mid-tick may leave readTime unwritten in Zotero
    but `processed_feed_items` rows already committed. Filtering here on
    `processed_feed_items` (not `feedItems.readTime`) is the correct
    idempotency boundary — Zotero's readTime is best-effort write, our DB is
    the source of truth for "have we already decided about this item".
    """
    if not feed_items:
        return [], 0
    keys = [(int(it.get("feed_library_id") or 0), int(it.get("item_id") or 0)) for it in feed_items]
    placeholders = ",".join("(?,?)" for _ in keys)
    flat: list[Any] = []
    for fl, fi in keys:
        flat.extend([fl, fi])

    seen_rows = conn.execute(
        f"""
        SELECT feed_library_id, feed_item_id
        FROM processed_feed_items
        WHERE (feed_library_id, feed_item_id) IN (VALUES {placeholders})
        """,
        flat,
    ).fetchall()
    seen: set[tuple[int, int]] = {(int(r[0]), int(r[1])) for r in seen_rows}

    unprocessed: list[dict[str, Any]] = []
    for item in feed_items:
        key = (int(item.get("feed_library_id") or 0), int(item.get("item_id") or 0))
        if key not in seen:
            unprocessed.append(item)
    return unprocessed, len(feed_items) - len(unprocessed)


def record_decision(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    feed_item: dict[str, Any],
    decision: str,
    decision_reason: str = "",
    composite_score: float | None = None,
    surprise_score: float | None = None,
    corpus_affinity: float | None = None,
    reading_priority: str | None = None,
    is_black_swan: bool = False,
    model_version: str | None = None,
    planned_zotero_key: str | None = None,
    matched_collections: list[str] | None = None,
    error: str | None = None,
) -> int:
    """Insert one decision row. Returns the row id.

    The (feed_library_id, feed_item_id) unique constraint enforces idempotency:
    re-recording silently no-ops (INSERT OR IGNORE). Use `update_to_decision()`
    to transition an existing row to a different decision (e.g., from
    `triaged_pending` to `selected` during daily selection).
    """
    import json as _json

    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO processed_feed_items (
            feed_library_id, feed_item_id, guid, title, doi, arxiv_id, feed_name,
            decision, decision_reason,
            composite_score, surprise_score, corpus_affinity, reading_priority,
            is_black_swan, model_version, run_id, planned_zotero_key,
            matched_collections_json, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(feed_item.get("feed_library_id") or 0),
            int(feed_item.get("item_id") or 0),
            str(feed_item.get("guid") or ""),
            str(feed_item.get("title") or ""),
            str(feed_item.get("doi") or "") or None,
            str(feed_item.get("arxiv_id") or "") or None,
            str(feed_item.get("feed_name") or "") or None,
            decision,
            decision_reason,
            composite_score,
            surprise_score,
            corpus_affinity,
            reading_priority,
            1 if is_black_swan else 0,
            model_version,
            run_id,
            planned_zotero_key,
            _json.dumps(matched_collections or []),
            error,
        ),
    )
    return int(cursor.lastrowid or 0)


def update_to_decision(
    conn: sqlite3.Connection,
    *,
    feed_library_id: int,
    feed_item_id: int,
    decision: str,
    decision_reason: str = "",
    is_black_swan: bool | None = None,
    planned_zotero_key: str | None = None,
) -> bool:
    """Transition an existing row's decision (e.g. triaged_pending -> selected).

    Used by the daily-selection job to flip pending triaged rows to their
    terminal state. Returns True if a row was updated.
    """
    assignments = ["decision = ?", "decision_reason = ?", "updated_at = datetime('now')"]
    params: list[Any] = [decision, decision_reason]
    if is_black_swan is not None:
        assignments.append("is_black_swan = ?")
        params.append(1 if is_black_swan else 0)
    if planned_zotero_key is not None:
        assignments.append("planned_zotero_key = ?")
        params.append(planned_zotero_key)
    params.extend([feed_library_id, feed_item_id])
    cursor = conn.execute(
        f"""
        UPDATE processed_feed_items SET {', '.join(assignments)}
        WHERE feed_library_id = ? AND feed_item_id = ?
        """,
        tuple(params),
    )
    return int(cursor.rowcount or 0) > 0


def record_materialization(
    conn: sqlite3.Connection,
    *,
    feed_library_id: int,
    feed_item_id: int,
    materialized_zotero_key: str,
    outcome_window_days: int,
) -> bool:
    """Mark a selected row as materialized in Zotero; schedules outcome check.

    Sets:
      - materialized_zotero_key (the actual Zotero item.key written)
      - outcome_eligible_at = now + outcome_window_days
    Idempotent: re-calling with the same key is a no-op.
    """
    eligible_at = (datetime.now(timezone.utc) + timedelta(days=max(0, int(outcome_window_days)))).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    cursor = conn.execute(
        """
        UPDATE processed_feed_items
        SET materialized_zotero_key = ?,
            outcome_eligible_at = ?,
            final_outcome = ?,
            updated_at = datetime('now')
        WHERE feed_library_id = ? AND feed_item_id = ?
        """,
        (materialized_zotero_key, eligible_at, OUTCOME_PENDING, feed_library_id, feed_item_id),
    )
    return int(cursor.rowcount or 0) > 0


def record_read_marked(
    conn: sqlite3.Connection,
    *,
    feed_library_id: int,
    feed_item_id: int,
) -> bool:
    """Record that we wrote readTime to Zotero for this feed item."""
    cursor = conn.execute(
        """
        UPDATE processed_feed_items
        SET read_time_marked_at = datetime('now'),
            updated_at = datetime('now')
        WHERE feed_library_id = ? AND feed_item_id = ?
        """,
        (feed_library_id, feed_item_id),
    )
    return int(cursor.rowcount or 0) > 0


def select_pending_triaged(
    conn: sqlite3.Connection,
    *,
    since_hours: int = 24,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Return rows in the 'triaged_pending' state from the last N hours.

    Used by the daily-selection job to gather the candidate pool for plateau
    selection. Ordered by composite_score DESC so kneedle-on-descending-curve
    works directly.
    """
    safe_limit = max(1, min(int(limit), 5000))
    safe_hours = max(1, int(since_hours))
    rows = conn.execute(
        """
        SELECT * FROM processed_feed_items
        WHERE decision = ?
          AND created_at >= datetime('now', ?)
        ORDER BY COALESCE(composite_score, 0) DESC
        LIMIT ?
        """,
        (DECISION_TRIAGED_PENDING, f"-{safe_hours} hours", safe_limit),
    ).fetchall()
    return [dict(r) for r in rows]


def due_outcome_checks(
    conn: sqlite3.Connection,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Return materialized rows whose outcome window has elapsed.

    Daemon picks N due rows per tick to amortize Zotero membership lookups.
    Ordered by outcome_eligible_at ASC so the oldest gets resolved first.
    """
    safe_limit = max(1, min(int(limit), 100))
    rows = conn.execute(
        """
        SELECT * FROM processed_feed_items
        WHERE materialized_zotero_key IS NOT NULL
          AND outcome_eligible_at IS NOT NULL
          AND outcome_eligible_at <= datetime('now')
          AND outcome_detected_at IS NULL
        ORDER BY outcome_eligible_at ASC
        LIMIT ?
        """,
        (safe_limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def record_outcome(
    conn: sqlite3.Connection,
    *,
    feed_library_id: int,
    feed_item_id: int,
    final_outcome: str,
    signal_weight: float,
) -> bool:
    """Write the resolved outcome + signal weight back to the row."""
    cursor = conn.execute(
        """
        UPDATE processed_feed_items
        SET final_outcome = ?,
            outcome_signal_weight = ?,
            outcome_detected_at = datetime('now'),
            updated_at = datetime('now')
        WHERE feed_library_id = ? AND feed_item_id = ?
        """,
        (final_outcome, float(signal_weight), feed_library_id, feed_item_id),
    )
    return int(cursor.rowcount or 0) > 0


def get_run_summary(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    """Return a per-decision count summary for a run."""
    rows = conn.execute(
        """
        SELECT decision, COUNT(*) AS n
        FROM processed_feed_items
        WHERE run_id = ?
        GROUP BY decision
        """,
        (run_id,),
    ).fetchall()
    counts = {str(r["decision"]): int(r["n"]) for r in rows}
    total = sum(counts.values())
    return {"run_id": run_id, "total": total, "by_decision": counts}


def list_recent_decisions(
    conn: sqlite3.Connection,
    limit: int = 100,
    decision: str | None = None,
) -> list[dict[str, Any]]:
    """Return recent decision rows (for the CLI audit view)."""
    safe_limit = max(1, min(int(limit), 1000))
    if decision:
        rows = conn.execute(
            """
            SELECT * FROM processed_feed_items
            WHERE decision = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (decision, safe_limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM processed_feed_items
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return [dict(r) for r in rows]
