"""Sidecar log for the P3 A0/A2 interleave experiment (ADR-A9 / GAP-G11).

One row per (day, slate item): which arm drafted it (``a0`` control blend /
``a2`` quality-first / ``both``), its competitive ``pair_id`` (NULL for
shared or uncontested picks) and slate position. Written by the daily-slate
assembly when ``ZS_RANK_INTERLEAVE`` is on; read only by the offline scorer
``tools/eval_interleave.py`` (which joins the user's verdicts to decide the
SPRT). The UI never sees the team — the experiment is blind by design.

Purely ADDITIVE sidecar table (no existing schema touched, per the frozen-
contract rule). Writes are DAY-level write-once: the first recorded slate of a
day is the only one persisted — later same-day assemblies are complete no-ops.
Row-level ``INSERT OR IGNORE`` would let intra-day pool drift interleave rows
from two different merges under colliding per-merge ``pair_id``s (adversarial
review 2026-07-08: two same-day GETs after a feed arrival produced a
(day, pair_id) group with duplicate teams, permanently crashing the scorer).
Day-level once also keeps attribution frozen after the user may have seen it.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from zotero_summarizer.storage.repositories import _connect_to

_TEAMS = ("a0", "a2", "both")

_DDL = """
CREATE TABLE IF NOT EXISTS interleave_log (
    day TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    item_key TEXT NOT NULL DEFAULT '',
    stable_feed_key TEXT NOT NULL DEFAULT '',
    team TEXT NOT NULL CHECK (team IN ('a0', 'a2', 'both')),
    pair_id INTEGER,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (day, item_id)
)
"""


def record_interleave_slate(db_path: Path, *, day: str, entries: list[dict]) -> int:
    """Persist one day's drafted slate. Returns the number of rows written —
    0 when the day already has ANY rows (day-level write-once, see module doc).

    Each entry: ``item_id`` (int), ``item_key``, ``stable_feed_key``, ``team``
    (one of ``a0``/``a2``/``both``), ``pair_id`` (int | None), ``position`` (int).
    Invalid teams fail loud — a mis-attributed row would silently corrupt the SPRT.
    """
    for e in entries:
        if e["team"] not in _TEAMS:
            raise ValueError(f"invalid interleave team {e['team']!r} (expected one of {_TEAMS})")
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect_to(db_path)
    try:
        conn.execute(_DDL)
        # BEGIN IMMEDIATE serializes the existence check with the insert —
        # two racing writers can't both see an empty day and mix their merges.
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute(
            "SELECT 1 FROM interleave_log WHERE day = ? LIMIT 1", (day,)
        ).fetchone():
            conn.rollback()
            return 0
        cur = conn.executemany(
            "INSERT OR IGNORE INTO interleave_log "
            "(day, item_id, item_key, stable_feed_key, team, pair_id, position, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    day,
                    int(e["item_id"]),
                    str(e.get("item_key") or ""),
                    str(e.get("stable_feed_key") or ""),
                    e["team"],
                    e["pair_id"],
                    int(e["position"]),
                    now,
                )
                for e in entries
            ],
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def fetch_interleave_log(db_path: Path) -> list[dict]:
    """All recorded attributions, oldest day first (read-only; empty list when
    the experiment has never run — the table may not exist yet)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='interleave_log'"
        ).fetchone()
        if not exists:
            return []
        rows = conn.execute(
            "SELECT day, item_id, item_key, stable_feed_key, team, pair_id, position "
            "FROM interleave_log ORDER BY day, position"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


__all__ = ["record_interleave_slate", "fetch_interleave_log"]
