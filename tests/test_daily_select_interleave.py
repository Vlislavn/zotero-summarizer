"""Slate-level tests for the P3 online interleave (ZS_RANK_INTERLEAVE, ADR-A9):
the team-draft merge of the A0 control and A2 quality-first arms, its blind
`interleave_log` attribution, and the evidence-integrity guards from the
2026-07-08 adversarial review. Split from test_daily_select.py (500-LOC cap);
the kernel + scorer pure math is pinned in tests/test_team_draft.py.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from zotero_summarizer.services.triage.daily_select import assemble_daily_slate
from zotero_summarizer.storage.interleave import fetch_interleave_log
from tests._daily_select_helpers import (
    _DEFAULT_NOW,
    _create_db,
    _insert,
    seed_reviews as _seed_reviews,
)


@pytest.fixture
def triage_db(tmp_path: Path) -> Path:
    db = tmp_path / "triage.db"
    _create_db(db)
    return db


def _insert_disagreement_cohort(triage_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The directive cohort: the arms DISAGREE on the leader (A0 → D-ON on-topic,
    A2 → A-OFF grade-A off-topic), so the merge holds exactly one competitive pair."""
    _seed_reviews(monkeypatch, {"ZK-A": "A", "ZK-D": "D"})
    _insert(triage_db, item_key="A-OFF", decision="awaiting_review",
            composite_score=3.0, corpus_affinity=0.2,
            materialized_zotero_key="ZK-A", goal_sims={"g": 0.05})
    _insert(triage_db, item_key="D-ON", decision="awaiting_review",
            composite_score=3.0, corpus_affinity=0.2,
            materialized_zotero_key="ZK-D", goal_sims={"g": 0.95})


def test_interleave_mode_merges_both_arms_and_logs_attribution(
    triage_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The slate becomes a team-draft merge of both arms; every shown slot gets a
    persisted arm attribution for the SPRT scorer; repeated same-day assembly is
    deterministic and never rewrites recorded teams."""
    monkeypatch.setenv("ZS_RANK_INTERLEAVE", "1")
    _insert_disagreement_cohort(triage_db, monkeypatch)

    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    keys = [p.item_key for p in slate.papers]
    assert sorted(keys) == ["A-OFF", "D-ON"]  # both arms' leaders shown, no dupes

    log = fetch_interleave_log(triage_db)
    assert {r["item_id"] for r in log} == {p.item_id for p in slate.papers}
    assert {r["team"] for r in log} == {"a0", "a2"}          # one competitive pair
    assert len({r["pair_id"] for r in log}) == 1 and log[0]["pair_id"] is not None
    # Blind by design: SlatePaper carries no team field for the UI to leak.
    assert not hasattr(slate.papers[0], "team")

    # Same-day re-assembly: identical slate, no new/changed attribution rows.
    again = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    assert [p.item_key for p in again.papers] == keys
    assert fetch_interleave_log(triage_db) == log


def test_interleave_log_is_day_level_write_once_under_pool_drift(
    triage_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later same-day assembly over a DRIFTED pool (new arrival) must not write:
    a second merge restarts pair_id at 1, and mixing its rows with the first
    merge's would corrupt (day, pair_id) grouping and crash the SPRT scorer
    (adversarial review 2026-07-08, BLOCKER 1)."""
    monkeypatch.setenv("ZS_RANK_INTERLEAVE", "1")
    _insert_disagreement_cohort(triage_db, monkeypatch)
    assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)

    log = fetch_interleave_log(triage_db)
    assert log  # first assembly recorded

    # Pool drifts: a new on-topic arrival reshuffles both arms' rankings.
    _insert(triage_db, item_key="NEW-MID", decision="awaiting_review",
            composite_score=3.5, corpus_affinity=0.2, goal_sims={"g": 0.9})
    assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    assert fetch_interleave_log(triage_db) == log  # complete no-op, nothing mixed

    # And the recorded log stays scoreable (no duplicate-team corruption).
    spec = importlib.util.spec_from_file_location(
        "eval_interleave_dd", Path(__file__).resolve().parents[1] / "tools" / "eval_interleave.py"
    )
    ei = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ei)
    wins, _ = ei.score_pairs(fetch_interleave_log(triage_db), {r["item_id"]: 0 for r in log})
    assert sum(wins.values()) == 1  # exactly the one recorded pair, a tie


def test_daemon_style_explicit_arm_never_touches_interleave_log(
    triage_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Daemon-internal assemblies (_auto_review_slate/_auto_render_slate) pass an
    explicit quality_first, which must bypass the interleave dispatch entirely —
    a K=10 ghost merge writing first would claim the day with attribution the
    user never saw (adversarial review 2026-07-08, BLOCKER 2)."""
    monkeypatch.setenv("ZS_RANK_INTERLEAVE", "1")
    _insert(triage_db, item_key="X", decision="awaiting_review",
            composite_score=3.0, corpus_affinity=0.2, goal_sims={"g": 0.5})
    slate = assemble_daily_slate(
        db_path=triage_db, K=10, now=_DEFAULT_NOW, quality_first=False
    )
    assert [p.item_key for p in slate.papers] == ["X"]
    assert fetch_interleave_log(triage_db) == []
