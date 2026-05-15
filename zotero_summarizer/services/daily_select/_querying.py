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


def open_ro(db_path: Path) -> sqlite3.Connection:
    """Open the triage DB read-only.

    URI mode raises ``sqlite3.OperationalError`` if the file doesn't exist;
    we pre-check with a clearer FileNotFoundError so the API route can
    return 503 with a helpful message.
    """
    if not db_path.exists():
        raise FileNotFoundError(f"triage db not found at {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


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


__all__ = ["open_ro", "fetch_rows_by_decisions"]
