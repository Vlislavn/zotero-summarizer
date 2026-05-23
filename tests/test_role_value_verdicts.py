"""Tests for Phase 1.17 Step 2 — role-value verdict storage helpers.

Covers :func:`insert_role_value_verdict` and
:func:`list_role_verdicts_summary`. We point both functions at a per-test
sqlite file (created with the production CREATE TABLE statement) so no
test ever touches the user's real DB.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from zotero_summarizer.storage import repositories as repo


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS role_value_verdicts (
    id              INTEGER PRIMARY KEY,
    item_key        TEXT NOT NULL,
    role            TEXT NOT NULL,
    verdict         TEXT NOT NULL CHECK (verdict IN ('worth', 'waste', 'unknown')),
    composite_score REAL,
    surprise_score  REAL,
    corpus_affinity REAL,
    created_at      TEXT NOT NULL
)
"""


@pytest.fixture
def verdict_db(tmp_path: Path) -> Path:
    db = tmp_path / "verdicts.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(_CREATE_TABLE_SQL)
        conn.commit()
    finally:
        conn.close()
    return db


def _insert_n(
    db_path: Path,
    *,
    role: str,
    worth: int = 0,
    waste: int = 0,
    unknown: int = 0,
) -> None:
    counter = 0
    for verdict, count in (("worth", worth), ("waste", waste), ("unknown", unknown)):
        for _ in range(count):
            repo.insert_role_value_verdict(
                db_path,
                item_key=f"K{role}_{counter}",
                role=role,
                verdict=verdict,
                composite_score=3.0,
                surprise_score=0.1,
                corpus_affinity=0.2,
            )
            counter += 1


# ---------------------------------------------------------------------------
# insert_role_value_verdict
# ---------------------------------------------------------------------------


def test_insert_role_verdict_returns_row_id(verdict_db: Path) -> None:
    row_id = repo.insert_role_value_verdict(
        verdict_db,
        item_key="K1",
        role="model",
        verdict="worth",
        composite_score=4.2,
        surprise_score=0.05,
        corpus_affinity=0.3,
    )
    assert isinstance(row_id, int)
    assert row_id > 0


def test_insert_role_verdict_rejects_invalid_verdict(verdict_db: Path) -> None:
    with pytest.raises(ValueError, match="verdict must be one of"):
        repo.insert_role_value_verdict(
            verdict_db,
            item_key="K1",
            role="model",
            verdict="bad",
            composite_score=None,
            surprise_score=None,
            corpus_affinity=None,
        )


def test_insert_role_verdict_rejects_empty_item_key(verdict_db: Path) -> None:
    with pytest.raises(ValueError, match="item_key"):
        repo.insert_role_value_verdict(
            verdict_db,
            item_key="",
            role="model",
            verdict="worth",
            composite_score=None,
            surprise_score=None,
            corpus_affinity=None,
        )


def test_insert_role_verdict_rejects_empty_role(verdict_db: Path) -> None:
    with pytest.raises(ValueError, match="role"):
        repo.insert_role_value_verdict(
            verdict_db,
            item_key="K1",
            role="",
            verdict="worth",
            composite_score=None,
            surprise_score=None,
            corpus_affinity=None,
        )


# ---------------------------------------------------------------------------
# dedup on re-rate + read-back (Today persistence fix)
# ---------------------------------------------------------------------------


def _count_rows(db_path: Path, item_key: str, role: str) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM role_value_verdicts WHERE item_key=? AND role=?",
            (item_key, role),
        ).fetchone()[0]
    finally:
        conn.close()


def test_re_rating_same_slot_overwrites(verdict_db: Path) -> None:
    repo.insert_role_value_verdict(
        verdict_db, item_key="K1", role="model", verdict="worth",
        composite_score=1.0, surprise_score=None, corpus_affinity=None,
    )
    repo.insert_role_value_verdict(
        verdict_db, item_key="K1", role="model", verdict="waste",
        composite_score=2.0, surprise_score=None, corpus_affinity=None,
    )
    assert _count_rows(verdict_db, "K1", "model") == 1
    assert repo.get_role_verdicts_by_keys(verdict_db, ["K1"]) == {"K1": "waste"}


def test_dedup_is_scoped_to_item_and_role(verdict_db: Path) -> None:
    # Same item, different role → distinct rows survive.
    repo.insert_role_value_verdict(
        verdict_db, item_key="K1", role="model", verdict="worth",
        composite_score=None, surprise_score=None, corpus_affinity=None,
    )
    repo.insert_role_value_verdict(
        verdict_db, item_key="K1", role="surprise", verdict="waste",
        composite_score=None, surprise_score=None, corpus_affinity=None,
    )
    assert _count_rows(verdict_db, "K1", "model") == 1
    assert _count_rows(verdict_db, "K1", "surprise") == 1


def test_get_role_verdicts_by_keys_filters_and_maps(verdict_db: Path) -> None:
    repo.insert_role_value_verdict(
        verdict_db, item_key="K1", role="model", verdict="worth",
        composite_score=None, surprise_score=None, corpus_affinity=None,
    )
    repo.insert_role_value_verdict(
        verdict_db, item_key="K2", role="surprise", verdict="unknown",
        composite_score=None, surprise_score=None, corpus_affinity=None,
    )
    out = repo.get_role_verdicts_by_keys(verdict_db, ["K1", "K2", "K_absent"])
    assert out == {"K1": "worth", "K2": "unknown"}


def test_get_role_verdicts_by_keys_empty_input(verdict_db: Path) -> None:
    assert repo.get_role_verdicts_by_keys(verdict_db, []) == {}


# ---------------------------------------------------------------------------
# list_role_verdicts_summary
# ---------------------------------------------------------------------------


def test_list_role_summary_empty_db(verdict_db: Path) -> None:
    summary = repo.list_role_verdicts_summary(verdict_db)
    assert summary == {}


def test_list_role_summary_win_rate_math(verdict_db: Path) -> None:
    _insert_n(verdict_db, role="model", worth=7, waste=2, unknown=1)
    summary = repo.list_role_verdicts_summary(verdict_db)
    assert "model" in summary
    bucket = summary["model"]
    assert bucket["worth"] == 7
    assert bucket["waste"] == 2
    assert bucket["unknown"] == 1
    assert bucket["n"] == 9  # decided = worth + waste
    assert bucket["win_rate"] == pytest.approx(7 / 9)


def test_list_role_summary_wilson_ci_appears_at_n_5(verdict_db: Path) -> None:
    _insert_n(verdict_db, role="surprise", worth=5, waste=0)
    summary = repo.list_role_verdicts_summary(verdict_db)
    bucket = summary["surprise"]
    assert bucket["n"] == 5
    assert bucket["win_rate"] == pytest.approx(1.0)
    assert bucket["ci_low"] is not None
    assert bucket["ci_high"] is not None
    assert 0.0 <= bucket["ci_low"] <= bucket["ci_high"] <= 1.0
    # With 5/5 worth, CI low must be strictly below 1.0.
    assert bucket["ci_low"] < 1.0


def test_list_role_summary_no_ci_below_threshold(verdict_db: Path) -> None:
    _insert_n(verdict_db, role="audit", worth=3, waste=1)
    summary = repo.list_role_verdicts_summary(verdict_db)
    bucket = summary["audit"]
    assert bucket["n"] == 4
    assert bucket["win_rate"] == pytest.approx(0.75)
    assert bucket["ci_low"] is None
    assert bucket["ci_high"] is None


def test_list_role_summary_unknown_excluded_from_winrate(verdict_db: Path) -> None:
    _insert_n(verdict_db, role="diversity", worth=4, waste=1, unknown=10)
    summary = repo.list_role_verdicts_summary(verdict_db)
    bucket = summary["diversity"]
    assert bucket["unknown"] == 10
    assert bucket["n"] == 5  # unknown does NOT count toward denominator
    assert bucket["win_rate"] == pytest.approx(4 / 5)


def test_list_role_summary_multi_role(verdict_db: Path) -> None:
    _insert_n(verdict_db, role="model", worth=6, waste=0)
    _insert_n(verdict_db, role="surprise", worth=2, waste=3)
    summary = repo.list_role_verdicts_summary(verdict_db)
    assert set(summary.keys()) == {"model", "surprise"}
    assert summary["model"]["win_rate"] == pytest.approx(1.0)
    assert summary["surprise"]["win_rate"] == pytest.approx(2 / 5)
