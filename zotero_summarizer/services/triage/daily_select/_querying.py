"""Read-only sqlite access for the daily slate.

Single responsibility: open the triage DB read-only, fetch rows by decision
within a lookback window. No allocation, no JSON parsing — those live in
sibling modules.

Fail-fast posture:

  * Missing DB file -> ``FileNotFoundError`` at :func:`_open_ro`.
  * sqlite errors propagate (no swallowing).
  * Empty ``decisions`` list -> ``ValueError`` (caller bug).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from zotero_summarizer.services._common import connect_sqlite_ro


def open_ro(db_path: Path) -> sqlite3.Connection:
    """Open the triage DB read-only (see ``services._common.connect_sqlite_ro``)."""
    return connect_sqlite_ro(db_path)


def fetch_rows_by_decisions(
    conn: sqlite3.Connection,
    *,
    decisions: list[str],
    lookback_hours: int,
    now: datetime,
) -> list[dict[str, Any]]:
    """Fetch rows whose decision is in ``decisions`` within the lookback window.

    The ``created_at`` column stores text in either ISO-8601 or sqlite's
    ``YYYY-MM-DD HH:MM:SS`` format. Both are lexicographically comparable
    after we normalise the cutoff to the same shape, so a string ``>=``
    works without parsing every row.
    """
    if not decisions:
        raise ValueError("decisions must be non-empty")
    cutoff_dt = now.astimezone(timezone.utc) - timedelta(hours=int(lookback_hours))
    cutoff_iso = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")
    placeholders = ",".join("?" * len(decisions))
    rows = conn.execute(
        f"""
        SELECT *
        FROM processed_feed_items
        WHERE decision IN ({placeholders})
          AND created_at >= ?
        """,
        (*decisions, cutoff_iso),
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_handled_keys(conn: sqlite3.Connection) -> tuple[set[str], set[str]]:
    """Return ``(role_handled_guids, label_handled_keys)``.

    A paper the user has already acted on must drop out of the slate so the
    next-best pick takes its place (inbox semantics). Two kinds of action
    count as "handled":

      * an after-reading rating in ``role_value_verdicts`` (keyed by the feed
        GUID/URL, == ``processed_feed_items.guid``), and
      * a priority label in ``label_verdicts`` (keyed ``feed:<feed_item_id>``).

    Both tables are created by ``repositories.init_db`` at startup, so their
    absence is a real schema bug and the sqlite error is allowed to
    propagate rather than being masked.
    """
    role_rows = conn.execute(
        "SELECT DISTINCT item_key FROM role_value_verdicts"
    ).fetchall()
    label_rows = conn.execute(
        "SELECT DISTINCT item_key FROM label_verdicts"
    ).fetchall()
    return (
        {str(r["item_key"]) for r in role_rows},
        {str(r["item_key"]) for r in label_rows},
    )


def fetch_recent_rows_by_decisions(
    conn: sqlite3.Connection,
    *,
    decisions: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    """Fetch the most recent ``limit`` rows in ``decisions``, ignoring the
    time window.

    Used as the never-empty fallback: when the windowed query returns
    nothing (the last triage was > lookback_hours ago and no daemon is
    running), Today still shows the freshest scored items that exist so the
    tab is never blank. The caller flags this as ``fellback_to_recent``.
    """
    if not decisions:
        raise ValueError("decisions must be non-empty")
    safe_limit = max(1, int(limit))
    placeholders = ",".join("?" * len(decisions))
    rows = conn.execute(
        f"""
        SELECT *
        FROM processed_feed_items
        WHERE decision IN ({placeholders})
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (*decisions, safe_limit),
    ).fetchall()
    return [dict(r) for r in rows]


__all__ = [
    "open_ro",
    "fetch_rows_by_decisions",
    "fetch_recent_rows_by_decisions",
    "fetch_handled_keys",
]
