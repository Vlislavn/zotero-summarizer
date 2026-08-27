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
    BEHAVIORAL_OUTCOMES,
    OUTCOME_DELETED_ALL,
    OUTCOME_DEEP_REVIEW_RUN,
    OUTCOME_ENGAGED,
    OUTCOME_KEPT_INBOX,
    OUTCOME_KEPT_UNREAD_APP,
    OUTCOME_MOVED_COLLECTION,
    OUTCOME_OPENED_DETAIL,
    OUTCOME_PENDING,
    OUTCOME_TRASHED,
    OUTCOME_UNKNOWN,
    OUTCOME_USER_LABELED,
    OUTCOME_WEIGHT,
    relevance_from_signal_weight,
)
from zotero_summarizer.storage.feed_identity import stable_feed_key_from_item
from zotero_summarizer.storage.feeds_lookup import (  # noqa: F401  (re-exported)
    _col,
    fetch_processed_content_pairs,
    fetch_resolved_outcomes_by_key,
    fetch_trashed_guids,
    get_processed_feed_item_by_id,
    get_processed_feed_item_by_pk,
    get_processed_feed_item_by_stable_key,
)
from zotero_summarizer.storage.feeds_schema import init_feeds_schema, open_triage_conn  # noqa: F401


def _parse_pub_year(date_str: Any) -> int | None:
    s = str(date_str or "").strip()
    return int(s[:4]) if len(s) >= 4 and s[:4].isdigit() else None


def new_run_id(prefix: str = "feeds") -> str:
    """Generate a stable, human-readable run/tick identifier."""
    return datetime.now(timezone.utc).strftime(f"{prefix}_%Y%m%d_%H%M%S_%f")


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
    stable_keys = [stable_feed_key_from_item(it) for it in feed_items]

    # `skipped_error` rows are transient LLM/endpoint failures the daemon
    # explicitly keeps for retry (the item was never scored). Treat them as
    # NOT processed so the next tick re-picks them; otherwise an errored item
    # stays unread forever AND blocks the round-robin picker from advancing to
    # newer unprocessed items. `clear_error_rows` removes the stale row before
    # the retry records a fresh decision.
    seen_numeric: set[tuple[int, int]] = set()
    if keys:
        placeholders = ",".join("(?,?)" for _ in keys)
        flat: list[Any] = []
        for fl, fi in keys:
            flat.extend([fl, fi])
        seen_rows = conn.execute(
            f"""
            SELECT feed_library_id, feed_item_id
            FROM processed_feed_items
            WHERE (feed_library_id, feed_item_id) IN (VALUES {placeholders})
              AND decision != ?
            """,
            [*flat, DECISION_SKIPPED_ERROR],
        ).fetchall()
        seen_numeric = {(int(r[0]), int(r[1])) for r in seen_rows}

    seen_stable: set[str] = set()
    non_empty_stable = [k for k in stable_keys if k]
    if non_empty_stable:
        placeholders = ",".join("?" for _ in non_empty_stable)
        rows = conn.execute(
            f"""
            SELECT DISTINCT stable_feed_key
            FROM processed_feed_items
            WHERE stable_feed_key IN ({placeholders})
              AND decision != ?
            """,
            [*non_empty_stable, DECISION_SKIPPED_ERROR],
        ).fetchall()
        seen_stable = {str(r[0]) for r in rows}

    unprocessed: list[dict[str, Any]] = []
    for idx, item in enumerate(feed_items):
        key = (int(item.get("feed_library_id") or 0), int(item.get("item_id") or 0))
        stable = stable_keys[idx]
        if (stable and stable not in seen_stable) or (not stable and key not in seen_numeric):
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
    out: list[tuple[int, int]] = []
    for item in feed_items:
        fl = int(item.get("feed_library_id") or 0)
        fi = int(item.get("item_id") or 0)
        stable = stable_feed_key_from_item(item)
        if stable:
            row = conn.execute(
                """
                SELECT 1 FROM processed_feed_items
                WHERE stable_feed_key = ?
                  AND decision NOT IN (?, ?)
                LIMIT 1
                """,
                (stable, DECISION_SKIPPED_ERROR, DECISION_AWAITING_REVIEW),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT 1 FROM processed_feed_items
                WHERE feed_library_id = ? AND feed_item_id = ?
                  AND decision NOT IN (?, ?)
                LIMIT 1
                """,
                (fl, fi, DECISION_SKIPPED_ERROR, DECISION_AWAITING_REVIEW),
            ).fetchone()
        if row is not None:
            out.append((fl, fi))
    return out


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
    deleted = 0
    for item in feed_items:
        fl = int(item.get("feed_library_id") or 0)
        fi = int(item.get("item_id") or 0)
        stable = stable_feed_key_from_item(item)
        if stable:
            cursor = conn.execute(
                """
                DELETE FROM processed_feed_items
                WHERE decision = ? AND stable_feed_key = ?
                """,
                (DECISION_SKIPPED_ERROR, stable),
            )
        else:
            cursor = conn.execute(
                """
                DELETE FROM processed_feed_items
                WHERE decision = ?
                  AND feed_library_id = ? AND feed_item_id = ?
                """,
                (DECISION_SKIPPED_ERROR, fl, fi),
            )
        deleted += int(cursor.rowcount or 0)
    return deleted


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
            feed_library_id, feed_item_id, source_type, stable_feed_key,
            guid, title, doi, arxiv_id, feed_name,
            decision, decision_reason,
            composite_score, surprise_score, corpus_affinity, reading_priority,
            is_black_swan, model_version, run_id, planned_zotero_key,
            matched_collections_json, error, shap_contribs_json,
            abstract, pub_year
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(feed_item.get("feed_library_id") or 0),
            int(feed_item.get("item_id") or 0),
            str(feed_item.get("source_type") or feed_item.get("source") or "zotero"),
            str(feed_item.get("stable_feed_key") or stable_feed_key_from_item(feed_item) or "") or None,
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
            str(feed_item.get("abstract") or "") or None,
            _parse_pub_year(feed_item.get("publication_date")),
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


def update_scores(
    conn: sqlite3.Connection,
    *,
    row_id: int,
    composite_score: float,
    reading_priority: str,
    shap_contribs_json: str | None,
) -> bool:
    """Refresh a row's gate scores IN PLACE (by PK ``id``) after a model upgrade.

    Updates ONLY the gate-derived fields (composite_score, reading_priority,
    shap_contribs_json) — never the ``decision``, read status, or outcome — so a
    re-score re-ranks the Today slate without re-surfacing items the user already
    handled. Returns True if a row was updated. Used by ``rescore_slate``.
    """
    cursor = conn.execute(
        """
        UPDATE processed_feed_items
        SET composite_score = ?, reading_priority = ?, shap_contribs_json = ?,
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (float(composite_score), reading_priority, shap_contribs_json, int(row_id)),
    )
    return int(cursor.rowcount or 0) > 0


def count_all_by_decision(conn: sqlite3.Connection) -> dict[str, int]:
    """Full per-decision histogram across every processed feed item.

    Backs the Today pipeline-funnel overview (came in / filtered / awaiting /
    added / trashed). Spans all runs, grouped by decision.
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
            zotero_sync_status = 'synced',
            outcome_eligible_at = ?,
            final_outcome = ?,
            updated_at = datetime('now')
        WHERE feed_library_id = ? AND feed_item_id = ?
        """,
        (materialized_zotero_key, eligible_at, OUTCOME_PENDING, feed_library_id, feed_item_id),
    )
    return int(cursor.rowcount or 0) > 0


def record_zotero_sync_status(
    conn: sqlite3.Connection,
    *,
    feed_library_id: int,
    feed_item_id: int,
    status: str,
) -> bool:
    cursor = conn.execute(
        """
        UPDATE processed_feed_items
        SET zotero_sync_status = ?,
            updated_at = datetime('now')
        WHERE feed_library_id = ? AND feed_item_id = ?
        """,
        (str(status or "").strip(), int(feed_library_id), int(feed_item_id)),
    )
    return int(cursor.rowcount or 0) > 0


def record_app_outcome(
    conn: sqlite3.Connection,
    *,
    feed_library_id: int,
    feed_item_id: int,
    final_outcome: str,
    signal_weight: float,
) -> bool:
    cursor = conn.execute(
        """
        UPDATE processed_feed_items
        SET final_outcome = ?,
            outcome_signal_weight = ?,
            outcome_detected_at = datetime('now'),
            updated_at = datetime('now')
        WHERE feed_library_id = ? AND feed_item_id = ?
          AND materialized_zotero_key IS NULL
        """,
        (
            str(final_outcome or "").strip(),
            float(signal_weight),
            int(feed_library_id),
            int(feed_item_id),
        ),
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
    record_outcome,
    select_by_decisions,
    select_pending_triaged,
)
