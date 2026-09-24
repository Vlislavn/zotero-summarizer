"""Selection + outcome/history queries over ``processed_feed_items``.

Split out of ``storage/feeds.py`` to keep each file focused; re-exported there
so callers continue to use ``feeds_storage.<fn>``.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from zotero_summarizer.domain import (
    PRIORITY_COULD_READ_THRESHOLD,
    PRIORITY_MUST_READ_THRESHOLD,
    PRIORITY_SHOULD_READ_THRESHOLD,
)
from zotero_summarizer.storage.feeds_constants import (
    DECISION_TRIAGED_PENDING, DECISION_USER_APPROVED, DECISION_USER_REJECTED,
    OUTCOME_DELETED_ALL, OUTCOME_ENGAGED, OUTCOME_KEPT_INBOX, OUTCOME_KEPT_UNREAD_APP,
    OUTCOME_MOVED_COLLECTION, OUTCOME_TRASHED, OUTCOME_UNKNOWN, OUTCOME_WEIGHT,
    relevance_from_signal_weight,
)
from zotero_summarizer.storage.feed_identity import row_feed_keys
from zotero_summarizer.storage.feeds_lookup import get_processed_feed_item_by_id
from zotero_summarizer.storage.feeds_schema import open_triage_conn


class MaterializationSuperseded(ValueError):
    """The feed row was rejected or its positive verdict changed before write."""


def current_feed_verdict(conn: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any] | None:
    """Newest stable verdict; consult a legacy key only when its alias names this paper."""
    keys = row_feed_keys(row)[:1]
    stable = row.get("stable_feed_key")
    if stable:
        siblings = conn.execute(
            "SELECT DISTINCT feed_item_id FROM processed_feed_items WHERE stable_feed_key = ?",
            (stable,),
        ).fetchall()
        for sibling in siblings:
            feed_id = int(sibling["feed_item_id"])
            resolved = get_processed_feed_item_by_id(conn, feed_id)
            if resolved is not None and resolved.get("stable_feed_key") == stable:
                legacy = f"feed:{feed_id}"
                if legacy not in keys:
                    keys.append(legacy)
    placeholders = ",".join("?" for _ in keys)
    has_revisions = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_changes'"
    ).fetchone() is not None
    order = (
        "COALESCE((SELECT MAX(revision) FROM sync_changes s WHERE s.item_key = v.item_key "
        "AND s.field = 'verdict'), 0) DESC, " if has_revisions else ""
    ) + "v.created_at DESC, v.id DESC"
    label = conn.execute(
        f"SELECT v.user_priority, v.source, v.comment FROM label_verdicts v "
        f"WHERE v.item_key IN ({placeholders}) ORDER BY {order} LIMIT 1", keys,
    ).fetchone()
    return dict(label) if label is not None else None


def current_materialization_intent(
    conn: sqlite3.Connection, row: dict[str, Any], label_priority: str | None = None,
) -> str | None:
    """Check current intent under BEGIN IMMEDIATE; return an existing item key."""
    current = conn.execute(
        "SELECT decision, materialized_zotero_key FROM processed_feed_items WHERE id = ?",
        (int(row["id"]),),
    ).fetchone()
    if current is None:
        raise KeyError(f"Missing processed feed row: {row['id']}")
    if current["materialized_zotero_key"]:
        return str(current["materialized_zotero_key"])
    label = current_feed_verdict(conn, row)
    if label is not None and (label["user_priority"] == "dont_read" or (
        label_priority is not None and label["user_priority"] != label_priority
    )):
        raise MaterializationSuperseded("A newer verdict cancelled this Add")
    if current["decision"] == DECISION_USER_REJECTED and not (
        label and label["source"] == "user" and label["user_priority"] != "dont_read"
    ):
        raise MaterializationSuperseded("A newer rejection cancelled this Add")
    if row.get("stable_feed_key"):
        sibling = conn.execute(
            "SELECT materialized_zotero_key FROM processed_feed_items "
            "WHERE stable_feed_key = ? AND materialized_zotero_key IS NOT NULL "
            "ORDER BY id LIMIT 1", (row["stable_feed_key"],),
        ).fetchone()
        if sibling:
            return str(sibling["materialized_zotero_key"])
    return None


def link_materialized_sibling(conn: sqlite3.Connection, row: dict[str, Any], key: str) -> bool:
    """Resolve an approved duplicate onto its already-written Zotero item."""
    cursor = conn.execute(
        """UPDATE processed_feed_items
           SET materialized_zotero_key = ?, decision = 'selected',
               decision_reason = 'reused_materialized_sibling', zotero_sync_status = 'synced',
               final_outcome = NULL, outcome_signal_weight = NULL,
               outcome_detected_at = NULL, updated_at = datetime('now')
           WHERE id = ? AND materialized_zotero_key IS NULL""",
        (key, int(row["id"])),
    )
    return bool(cursor.rowcount)


def mark_pending_if_current(conn: sqlite3.Connection, row: dict[str, Any], reason: str) -> bool:
    """Atomically park a reviewed Add only while no newer rejection exists."""
    existing = current_materialization_intent(conn, row)
    if existing:
        link_materialized_sibling(conn, row, existing)
        return False
    cursor = conn.execute(
        """UPDATE processed_feed_items
           SET decision = ?, decision_reason = ?, zotero_sync_status = 'pending',
               final_outcome = ?, outcome_signal_weight = ?,
               outcome_detected_at = datetime('now'), updated_at = datetime('now')
           WHERE id = ?""",
        (DECISION_USER_APPROVED, reason, OUTCOME_KEPT_UNREAD_APP,
         OUTCOME_WEIGHT[OUTCOME_KEPT_UNREAD_APP], int(row["id"])),
    )
    return bool(cursor.rowcount)


def cancel_pending_materialization(conn: sqlite3.Connection, stable_feed_key: str) -> int:
    """A newer negative verdict wins over every unmaterialized sibling Add."""
    if not stable_feed_key:
        return 0
    cursor = conn.execute(
        """UPDATE processed_feed_items
           SET decision = ?, decision_reason = 'negative_verdict_after_pending_add',
               zotero_sync_status = 'cancelled', final_outcome = NULL,
               outcome_signal_weight = NULL, outcome_detected_at = NULL,
               updated_at = datetime('now')
           WHERE stable_feed_key = ? AND decision = ?
             AND materialized_zotero_key IS NULL""",
        (DECISION_USER_REJECTED, stable_feed_key, DECISION_USER_APPROVED),
    )
    return int(cursor.rowcount or 0)


_ORDER_BY = {
    "score": "COALESCE(composite_score, 0) DESC",
    "recent": "created_at DESC, id DESC",
    "border": "composite_score IS NULL, "
    "MIN(ABS(composite_score - ?), ABS(composite_score - ?), ABS(composite_score - ?)), "
    "created_at DESC, id DESC",
}
_OUTCOME_FEEDBACK_TYPES = {
    OUTCOME_ENGAGED: "implicit_engagement",
    OUTCOME_MOVED_COLLECTION: "implicit_engagement",
    OUTCOME_KEPT_INBOX: "implicit_weak_negative",
    OUTCOME_DELETED_ALL: "implicit_negative_strong",
    OUTCOME_TRASHED: "implicit_negative_strong",
    OUTCOME_UNKNOWN: "implicit_negative_strong",
}


def reserve_materialization_key(db_path: Path, processed_id: int, proposed_key: str) -> str:
    """Commit one stable operation key before writing to the separate Zotero DB."""
    with open_triage_conn(db_path) as conn:
        row = conn.execute(
            """UPDATE processed_feed_items
               SET planned_zotero_key = COALESCE(NULLIF(materialized_zotero_key, ''),
                                                NULLIF(planned_zotero_key, ''), ?)
               WHERE id = ? RETURNING planned_zotero_key""",
            (proposed_key, processed_id),
        ).fetchone()
        if row is None:
            raise KeyError(f"Missing processed feed row: {processed_id}")
        conn.commit()
        return row["planned_zotero_key"]


def select_by_decisions(
    conn: sqlite3.Connection,
    *,
    decisions: list[str],
    since_hours: int | None = 24,
    limit: int | None = 1000,
    feed_library_ids: list[int] | None = None,
    sort: str = "score",
) -> list[dict[str, Any]]:
    """Return rows whose decision is in ``decisions`` from the last N hours.

    ``since_hours=None`` disables the time window entirely (returns every
    matching row regardless of age) — used for ``user_approved`` rows, which
    are an explicit user instruction and must never silently expire.

    Used by:
      * Daily-selection (decisions=[DECISION_TRIAGED_PENDING]) to gather the
        plateau candidate pool. Order by composite_score DESC works for
        kneedle-on-descending-curve.
      * Review UI (decisions=[DECISION_AWAITING_REVIEW]) to list items
        awaiting user verdict.

    When ``feed_library_ids`` is provided, restrict to those feeds (used by
    ``feeds run --feeds <name>``). ``sort`` selects score-descending (default),
    newest-first, or nearest-priority-boundary order BEFORE the limit. Border
    order puts unscored rows last; review ties use newest timestamp then id.
    ``limit=None`` returns the whole matching set; finite limits stay capped.
    """
    if not decisions:
        raise ValueError("decisions must be non-empty")
    if sort not in _ORDER_BY:
        raise ValueError(f"Unknown feed sort: {sort!r}")
    order_params = (
        PRIORITY_COULD_READ_THRESHOLD,
        PRIORITY_SHOULD_READ_THRESHOLD,
        PRIORITY_MUST_READ_THRESHOLD,
    ) if sort == "border" else ()
    safe_limit = -1 if limit is None else max(1, min(int(limit), 5000))
    decision_placeholders = ",".join("?" * len(decisions))
    time_clause = ""
    time_params: tuple[Any, ...] = ()
    if since_hours is not None:
        safe_hours = max(1, int(since_hours))
        time_clause = "AND created_at >= datetime('now', ?)"
        time_params = (f"-{safe_hours} hours",)
    feed_clause = ""
    if feed_library_ids:
        feed_placeholders = ",".join("?" * len(feed_library_ids))
        feed_clause = f"AND feed_library_id IN ({feed_placeholders})"
    rows = conn.execute(
        f"""
        SELECT * FROM processed_feed_items
        WHERE decision IN ({decision_placeholders})
          {time_clause}
          {feed_clause}
        ORDER BY {_ORDER_BY[sort]}
        LIMIT ?
        """,
        (*decisions, *time_params, *(feed_library_ids or ()), *order_params, safe_limit),
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
) -> bool:
    """Stage the first resolution and its feedback together; caller commits both."""
    from zotero_summarizer.storage._repo_feedback import _insert_feedback_events

    feedback_type = _OUTCOME_FEEDBACK_TYPES[final_outcome]
    signal_weight = OUTCOME_WEIGHT[final_outcome]
    row = conn.execute(
        """
        UPDATE processed_feed_items
        SET final_outcome = ?,
            outcome_signal_weight = ?,
            outcome_detected_at = datetime('now'),
            updated_at = datetime('now')
        WHERE feed_library_id = ? AND feed_item_id = ?
          AND outcome_detected_at IS NULL AND materialized_zotero_key IS NOT NULL
        RETURNING materialized_zotero_key, reading_priority
        """,
        (final_outcome, signal_weight, feed_library_id, feed_item_id),
    ).fetchone()
    if row is None:
        return False
    _insert_feedback_events(conn, [{
        "item_id": row["materialized_zotero_key"],
        "feedback_type": feedback_type,
        "signal": f"feed_outcome:{final_outcome}",
        "original_priority": row["reading_priority"] or "",
        "inferred_relevance": relevance_from_signal_weight(signal_weight),
    }])
    return True
