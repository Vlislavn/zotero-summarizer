"""Selection + outcome/history queries over ``processed_feed_items``.

Split out of ``storage/feeds.py`` to keep each file focused; re-exported there
so callers continue to use ``feeds_storage.<fn>``.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from zotero_summarizer.storage.feeds_constants import (
    DECISION_AWAITING_REVIEW,
    DECISION_TRIAGED_PENDING,
)


def select_by_decisions(
    conn: sqlite3.Connection,
    *,
    decisions: list[str],
    since_hours: int = 24,
    limit: int = 1000,
    feed_library_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Return rows whose decision is in ``decisions`` from the last N hours.

    Used by:
      * Daily-selection (decisions=[DECISION_TRIAGED_PENDING]) to gather the
        plateau candidate pool. Order by composite_score DESC works for
        kneedle-on-descending-curve.
      * Review UI (decisions=[DECISION_AWAITING_REVIEW]) to list items
        awaiting user verdict.

    When ``feed_library_ids`` is provided, restrict to those feeds (used by
    ``feeds run --feeds <name>``).
    """
    if not decisions:
        raise ValueError("decisions must be non-empty")
    safe_limit = max(1, min(int(limit), 5000))
    safe_hours = max(1, int(since_hours))
    decision_placeholders = ",".join("?" * len(decisions))
    if feed_library_ids:
        feed_placeholders = ",".join("?" * len(feed_library_ids))
        rows = conn.execute(
            f"""
            SELECT * FROM processed_feed_items
            WHERE decision IN ({decision_placeholders})
              AND created_at >= datetime('now', ?)
              AND feed_library_id IN ({feed_placeholders})
            ORDER BY COALESCE(composite_score, 0) DESC
            LIMIT ?
            """,
            (*decisions, f"-{safe_hours} hours", *feed_library_ids, safe_limit),
        ).fetchall()
    else:
        rows = conn.execute(
            f"""
            SELECT * FROM processed_feed_items
            WHERE decision IN ({decision_placeholders})
              AND created_at >= datetime('now', ?)
            ORDER BY COALESCE(composite_score, 0) DESC
            LIMIT ?
            """,
            (*decisions, f"-{safe_hours} hours", safe_limit),
        ).fetchall()
    return [dict(r) for r in rows]


def select_pending_triaged(
    conn: sqlite3.Connection,
    *,
    since_hours: int = 24,
    limit: int = 1000,
    feed_library_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Compatibility wrapper: returns triaged_pending rows for daily selection."""
    return select_by_decisions(
        conn,
        decisions=[DECISION_TRIAGED_PENDING],
        since_hours=since_hours,
        limit=limit,
        feed_library_ids=feed_library_ids,
    )


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
