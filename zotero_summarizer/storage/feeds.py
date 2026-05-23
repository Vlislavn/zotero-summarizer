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

# Re-export the decision/outcome taxonomy and table DDL so existing
# `from zotero_summarizer.storage import feeds as fs; fs.DECISION_*` callers
# keep working. The split exists only for file-size compliance.
from zotero_summarizer.storage.feeds_constants import (  # noqa: F401  (re-exported)
    DECISION_AWAITING_REVIEW,
    DECISION_BLACK_SWAN,
    DECISION_GATE_REJECTED,
    DECISION_REJECTED_DAILY_CUTOFF,
    DECISION_REJECTED_DEDUP_LIBRARY,
    DECISION_REJECTED_DEDUP_PROCESSED,
    DECISION_REJECTED_ELBOW,
    DECISION_REJECTED_LOW_SCORE,
    DECISION_SELECTED,
    DECISION_SKIPPED_ERROR,
    DECISION_TRIAGED_PENDING,
    DECISION_USER_APPROVED,
    DECISION_USER_REJECTED,
    OUTCOME_DELETED_ALL,
    OUTCOME_ENGAGED,
    OUTCOME_KEPT_INBOX,
    OUTCOME_MOVED_COLLECTION,
    OUTCOME_PENDING,
    OUTCOME_TRASHED,
    OUTCOME_UNKNOWN,
    OUTCOME_WEIGHT,
    TERMINAL_MATERIALIZED_DECISIONS,
)
from zotero_summarizer.storage.feeds_schema import (
    CREATE_TABLE as _CREATE_TABLE,
    INDEX_STATEMENTS as _INDEX_STATEMENTS,
    MIGRATION_COLUMNS as _MIGRATION_COLUMNS,
)
from zotero_summarizer.storage.feeds_lookup import (  # noqa: F401  (re-exported)
    get_processed_feed_item_by_id,
    get_processed_feed_item_by_pk,
)

LOGGER = logging.getLogger("zotero_summarizer.storage.feeds")


def init_feeds_schema(conn: sqlite3.Connection) -> None:
    """Create the processed_feed_items table + indexes; migrate Phase 1 DBs.

    Idempotent. Safe to call on every app start. The narrow
    ``except sqlite3.OperationalError`` branch is a deliberate carry-over
    from Phase 1.5: concurrent daemon starts race PRAGMA + ALTER and the
    second loser must NOT abort startup. Tightening this to a specific
    error message is queued for the fail-fast pass; until then, the
    warning log preserves visibility.
    """
    conn.execute(_CREATE_TABLE)
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

    # `skipped_error` rows are transient LLM/endpoint failures the daemon
    # explicitly keeps for retry (the item was never scored). Treat them as
    # NOT processed so the next tick re-picks them; otherwise an errored item
    # stays unread forever AND blocks the round-robin picker from advancing to
    # newer unprocessed items. `clear_error_rows` removes the stale row before
    # the retry records a fresh decision.
    seen_rows = conn.execute(
        f"""
        SELECT feed_library_id, feed_item_id
        FROM processed_feed_items
        WHERE (feed_library_id, feed_item_id) IN (VALUES {placeholders})
          AND decision != ?
        """,
        [*flat, DECISION_SKIPPED_ERROR],
    ).fetchall()
    seen: set[tuple[int, int]] = {(int(r[0]), int(r[1])) for r in seen_rows}

    unprocessed: list[dict[str, Any]] = []
    for item in feed_items:
        key = (int(item.get("feed_library_id") or 0), int(item.get("item_id") or 0))
        if key not in seen:
            unprocessed.append(item)
    return unprocessed, len(feed_items) - len(unprocessed)


def select_stale_unread_to_mark(
    conn: sqlite3.Connection,
    feed_items: list[dict[str, Any]],
) -> list[tuple[int, int]]:
    """Keys ``(feed_library_id, feed_item_id)`` of items already decided with a
    TERMINAL decision — the ones the daemon should mark read in Zotero.

    These are items the dedup in :func:`filter_unprocessed` skips (already
    recorded) but that linger as *unread* in Zotero, so the bounded round-robin
    picker keeps re-grabbing the same batch every tick and never advances to new
    items. Marking them read evicts them from the unread pool.

    Excluded:
      * ``skipped_error`` — retryable (the item was never scored), must stay pickable.
      * ``awaiting_review`` — the review flow keeps these unread on purpose.
    """
    if not feed_items:
        return []
    keys = [(int(it.get("feed_library_id") or 0), int(it.get("item_id") or 0)) for it in feed_items]
    placeholders = ",".join("(?,?)" for _ in keys)
    flat: list[Any] = []
    for fl, fi in keys:
        flat.extend([fl, fi])

    rows = conn.execute(
        f"""
        SELECT DISTINCT feed_library_id, feed_item_id
        FROM processed_feed_items
        WHERE (feed_library_id, feed_item_id) IN (VALUES {placeholders})
          AND decision NOT IN (?, ?)
        """,
        [*flat, DECISION_SKIPPED_ERROR, DECISION_AWAITING_REVIEW],
    ).fetchall()
    return [(int(r[0]), int(r[1])) for r in rows]


def clear_error_rows(
    conn: sqlite3.Connection, feed_items: list[dict[str, Any]]
) -> int:
    """Delete any ``skipped_error`` rows for these items; returns the count.

    Counterpart to :func:`filter_unprocessed` treating ``skipped_error`` as
    retryable: ``record_decision`` is ``INSERT OR IGNORE``, so the stale error
    row must be removed first or the retry's fresh decision silently no-ops.
    The caller commits.
    """
    if not feed_items:
        return 0
    keys = [(int(it.get("feed_library_id") or 0), int(it.get("item_id") or 0)) for it in feed_items]
    placeholders = ",".join("(?,?)" for _ in keys)
    flat: list[Any] = []
    for fl, fi in keys:
        flat.extend([fl, fi])
    cursor = conn.execute(
        f"""
        DELETE FROM processed_feed_items
        WHERE decision = ?
          AND (feed_library_id, feed_item_id) IN (VALUES {placeholders})
        """,
        [DECISION_SKIPPED_ERROR, *flat],
    )
    return cursor.rowcount


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
    shap_contribs_json: str | None = None,
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
            matched_collections_json, error, shap_contribs_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            shap_contribs_json,
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


def update_quality_review(
    conn: sqlite3.Connection,
    *,
    row_id: int,
    quality_review_json: str,
) -> bool:
    """Store the full-text :class:`QualityReview` JSON on a row (by PK ``id``)."""
    cursor = conn.execute(
        """
        UPDATE processed_feed_items
        SET quality_review_json = ?, updated_at = datetime('now')
        WHERE id = ?
        """,
        (quality_review_json, int(row_id)),
    )
    return int(cursor.rowcount or 0) > 0


def count_by_decisions(conn: sqlite3.Connection, decisions: list[str]) -> int:
    """Uncapped count of rows whose ``decision`` is in ``decisions``.

    Backs the Today backlog header (e.g. how many triaged items await the
    user's add/trash call). Empty ``decisions`` -> 0.
    """
    if not decisions:
        return 0
    placeholders = ",".join("?" for _ in decisions)
    row = conn.execute(
        f"SELECT COUNT(*) FROM processed_feed_items WHERE decision IN ({placeholders})",
        tuple(decisions),
    ).fetchone()
    return int(row[0]) if row else 0


def count_all_by_decision(conn: sqlite3.Connection) -> dict[str, int]:
    """Full per-decision histogram across every processed feed item.

    Backs the Today pipeline-funnel overview (came in / filtered / awaiting /
    added / trashed). Mirrors :func:`get_run_summary` but spans all runs.
    """
    rows = conn.execute(
        "SELECT decision, COUNT(*) AS n FROM processed_feed_items GROUP BY decision"
    ).fetchall()
    return {str(r["decision"]): int(r["n"]) for r in rows}


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


# Selection + outcome/history queries live in feeds_history (re-exported).
from zotero_summarizer.storage.feeds_history import (  # noqa: F401,E402
    due_outcome_checks,
    get_run_summary,
    list_recent_decisions,
    record_outcome,
    select_by_decisions,
    select_pending_triaged,
)
