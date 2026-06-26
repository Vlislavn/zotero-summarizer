"""Tests for Phase 1.17 Step 4 — weekly A/B verdict storage helpers.

Covers :func:`insert_weekly_ab_verdict` and :func:`list_ab_decision_status`.
The decision rule (locked after 8 verdicts with >=6 wins for one side) is
exercised at the boundary cases.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from zotero_summarizer.storage import repositories as repo


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS weekly_ab_verdicts (
    id             INTEGER PRIMARY KEY,
    week_start     TEXT NOT NULL,
    winner         TEXT NOT NULL CHECK (winner IN ('roles', 'pure_score', 'tied')),
    slate_a_keys   TEXT NOT NULL,
    slate_b_keys   TEXT NOT NULL,
    created_at     TEXT NOT NULL
)
"""


@pytest.fixture
def ab_db(tmp_path: Path) -> Path:
    db = tmp_path / "ab.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(_CREATE_TABLE_SQL)
        conn.commit()
    finally:
        conn.close()
    return db


def _insert_winners(db_path: Path, *, winner: str, count: int) -> None:
    for i in range(count):
        # Vary week_start so we exercise multiple distinct rows.
        week = f"2026-05-{(i % 28) + 1:02d}"
        repo.insert_weekly_ab_verdict(
            db_path,
            week_start=week,
            winner=winner,
            slate_a_keys=[f"A{i}_1", f"A{i}_2"],
            slate_b_keys=[f"B{i}_1", f"B{i}_2"],
        )


# ---------------------------------------------------------------------------
# insert_weekly_ab_verdict
# ---------------------------------------------------------------------------


def test_insert_weekly_ab_returns_row_id(ab_db: Path) -> None:
    row_id = repo.insert_weekly_ab_verdict(
        ab_db,
        week_start="2026-05-11",
        winner="roles",
        slate_a_keys=["K1", "K2"],
        slate_b_keys=["K3", "K4"],
    )
    assert isinstance(row_id, int)
    assert row_id > 0


def test_insert_weekly_ab_rejects_invalid_winner(ab_db: Path) -> None:
    with pytest.raises(ValueError, match="winner must be one of"):
        repo.insert_weekly_ab_verdict(
            ab_db,
            week_start="2026-05-11",
            winner="other",
            slate_a_keys=["K1"],
            slate_b_keys=["K2"],
        )


def test_insert_weekly_ab_rejects_empty_slates(ab_db: Path) -> None:
    with pytest.raises(ValueError, match="slate_a_keys"):
        repo.insert_weekly_ab_verdict(
            ab_db,
            week_start="2026-05-11",
            winner="tied",
            slate_a_keys=[],
            slate_b_keys=["K1"],
        )


def test_insert_weekly_ab_rejects_bad_week_format(ab_db: Path) -> None:
    with pytest.raises(ValueError):
        repo.insert_weekly_ab_verdict(
            ab_db,
            week_start="not-a-date",
            winner="roles",
            slate_a_keys=["K1"],
            slate_b_keys=["K2"],
        )


def test_insert_weekly_ab_stores_slate_keys_as_json(ab_db: Path) -> None:
    repo.insert_weekly_ab_verdict(
        ab_db,
        week_start="2026-05-11",
        winner="roles",
        slate_a_keys=["K1", "K2"],
        slate_b_keys=["K3"],
    )
    conn = sqlite3.connect(str(ab_db))
    try:
        row = conn.execute(
            "SELECT slate_a_keys, slate_b_keys FROM weekly_ab_verdicts"
        ).fetchone()
    finally:
        conn.close()
    assert json.loads(row[0]) == ["K1", "K2"]
    assert json.loads(row[1]) == ["K3"]


# ---------------------------------------------------------------------------
# list_ab_decision_status
# ---------------------------------------------------------------------------


def test_ab_status_empty_db(ab_db: Path) -> None:
    status = repo.list_ab_decision_status(ab_db)
    assert status["total"] == 0
    assert status["roles_wins"] == 0
    assert status["pure_score_wins"] == 0
    assert status["tied"] == 0
    assert status["decision_locked"] is False
    assert status["decision"] is None
    assert status["remaining_until_decision"] == 8


def test_ab_status_unlocked_below_threshold(ab_db: Path) -> None:
    _insert_winners(ab_db, winner="roles", count=3)
    _insert_winners(ab_db, winner="pure_score", count=2)
    status = repo.list_ab_decision_status(ab_db)
    assert status["total"] == 5
    assert status["roles_wins"] == 3
    assert status["pure_score_wins"] == 2
    assert status["decision_locked"] is False
    assert status["decision"] is None
    assert status["remaining_until_decision"] == 3


def test_ab_status_locked_roles_at_6_of_8(ab_db: Path) -> None:
    _insert_winners(ab_db, winner="roles", count=6)
    _insert_winners(ab_db, winner="pure_score", count=2)
    status = repo.list_ab_decision_status(ab_db)
    assert status["total"] == 8
    assert status["roles_wins"] == 6
    assert status["pure_score_wins"] == 2
    assert status["decision_locked"] is True
    assert status["decision"] == "roles"
    assert status["remaining_until_decision"] == 0


def test_ab_status_locked_pure_score_at_6_of_8(ab_db: Path) -> None:
    _insert_winners(ab_db, winner="pure_score", count=6)
    _insert_winners(ab_db, winner="roles", count=2)
    status = repo.list_ab_decision_status(ab_db)
    assert status["decision_locked"] is True
    assert status["decision"] == "pure_score"


def test_ab_status_tied_no_lock(ab_db: Path) -> None:
    _insert_winners(ab_db, winner="roles", count=4)
    _insert_winners(ab_db, winner="pure_score", count=4)
    status = repo.list_ab_decision_status(ab_db)
    assert status["total"] == 8
    assert status["decision_locked"] is False
    assert status["decision"] is None


def test_ab_status_counts_tied_winner(ab_db: Path) -> None:
    _insert_winners(ab_db, winner="tied", count=3)
    status = repo.list_ab_decision_status(ab_db)
    assert status["tied"] == 3
    assert status["roles_wins"] == 0
    assert status["pure_score_wins"] == 0
    assert status["decision_locked"] is False
