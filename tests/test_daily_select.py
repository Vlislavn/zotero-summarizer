"""Tests for Phase 1.17 Step 1 — :func:`assemble_daily_slate`.

Strategy: seed an in-memory-like sqlite DB (file-backed in ``tmp_path``)
directly with synthetic ``processed_feed_items`` rows. This avoids any
SPECTER2 / OpenAlex / LLM round-trips. The role-allocation logic, the
blended ordering (rank_blend), the un-truncated picker pool, the surprise
floor, and the day-stable RNG can all be exercised with crafted rows.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from zotero_summarizer.services.triage.daily_select import (
    DailySlate,
    SlatePaper,
    assemble_daily_slate,
    count_awaiting_unhandled,
)
from zotero_summarizer.storage import repositories as repo
from tests._daily_select_helpers import _DEFAULT_NOW, _create_db, _insert, _make_shap_json


@pytest.fixture
def triage_db(tmp_path: Path) -> Path:
    db = tmp_path / "triage.db"
    _create_db(db)
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_assemble_daily_slate_empty_pool_returns_empty(triage_db: Path) -> None:
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    assert isinstance(slate, DailySlate)
    assert slate.papers == []
    assert slate.pool_size == 0


def test_assemble_daily_slate_basic_K5_with_full_pool(triage_db: Path) -> None:
    # 20 awaiting_review rows with composite_score ramping 1.0..5.0.
    for i in range(20):
        composite = 1.0 + (4.0 * i / 19.0)
        # Put one strongly-surprising paper near the middle.
        surprise = 0.85 if i == 10 else 0.05
        # Provide one strongly-negative affinity for diversity to find.
        affinity = -0.4 if i == 3 else 0.3
        _insert(
            triage_db,
            item_key=f"K{i:02d}",
            decision="awaiting_review",
            composite_score=composite,
            surprise_score=surprise,
            corpus_affinity=affinity,
            shap_contribs_json=_make_shap_json(affinity=affinity, prestige=4.2),
        )
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    assert len(slate.papers) == 5
    assert slate.pool_size == 20
    # At least one paper should be the surprise pick.
    roles = [p.role for p in slate.papers]
    assert "surprise" in roles
    # Model role (top-2 by composite) should appear too.
    assert "model" in roles
    # All papers should be SlatePaper instances with required fields.
    for paper in slate.papers:
        assert isinstance(paper, SlatePaper)
        assert paper.item_key
        assert paper.role in {"model", "surprise", "diversity", "model_fallback"}


def test_assemble_daily_slate_respects_lookback_hours(triage_db: Path) -> None:
    recent_ts = _DEFAULT_NOW - timedelta(hours=24)
    old_ts = _DEFAULT_NOW - timedelta(days=30)
    for i in range(5):
        _insert(
            triage_db,
            item_key=f"NEW{i}",
            decision="awaiting_review",
            composite_score=3.0 + i * 0.1,
            created_at=recent_ts,
        )
    for i in range(5):
        _insert(
            triage_db,
            item_key=f"OLD{i}",
            decision="awaiting_review",
            composite_score=3.0 + i * 0.1,
            created_at=old_ts,
        )
    slate = assemble_daily_slate(
        db_path=triage_db, K=5, lookback_hours=72, now=_DEFAULT_NOW
    )
    assert slate.pool_size == 5
    chosen_keys = {p.item_key for p in slate.papers}
    assert all(k.startswith("NEW") for k in chosen_keys)


def test_assemble_daily_slate_dedupes_by_item_key(triage_db: Path) -> None:
    older = _DEFAULT_NOW - timedelta(hours=10)
    newer = _DEFAULT_NOW - timedelta(hours=1)
    _insert(
        triage_db,
        item_key="DUPE",
        decision="awaiting_review",
        composite_score=2.0,
        created_at=older,
        title="old title",
    )
    _insert(
        triage_db,
        item_key="DUPE",
        decision="awaiting_review",
        composite_score=4.0,
        created_at=newer,
        title="new title",
    )
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    assert slate.pool_size == 1
    assert slate.papers[0].title == "new title"
    assert slate.papers[0].composite_score == pytest.approx(4.0)


def test_assemble_daily_slate_no_pretruncation(triage_db: Path) -> None:
    """The regression the cap removal fixed: a strongly off-library paper
    ranked outside the old top-``backlog_cap`` by composite must STILL be
    reachable by the diversity picker — the role pool is no longer truncated
    before allocation (``backlog_cap`` only bounds the fallback fetch)."""
    for i in range(100):
        _insert(
            triage_db,
            item_key=f"P{i:03d}",
            decision="awaiting_review",
            composite_score=float(i) / 20.0,  # 0..5
            corpus_affinity=0.3,
        )
    # Composite 1.0 — far below the top-10 cutoff — but negative affinity:
    # the only diversity-eligible row in the pool.
    _insert(
        triage_db,
        item_key="OFF-TRACK",
        decision="awaiting_review",
        composite_score=1.0,
        corpus_affinity=-0.5,
        shap_contribs_json=_make_shap_json(affinity=-0.5, prestige=4.2),
    )
    slate = assemble_daily_slate(
        db_path=triage_db, K=5, backlog_cap=10, now=_DEFAULT_NOW
    )
    assert slate.pool_size == 101
    # Model picks still come from the top of the pool.
    top_paper = max(slate.papers, key=lambda p: p.composite_score)
    assert top_paper.composite_score >= 4.0
    # The off-track paper is found by diversity despite its rank (#101).
    diversity = [p for p in slate.papers if p.role == "diversity"]
    assert [p.item_key for p in diversity] == ["OFF-TRACK"]


def test_goal_sim_lifts_on_goal_paper_into_model_picks(triage_db: Path) -> None:
    """The headline routing fix: a paper the gate under-scores but that
    strongly matches the stated research goals must outrank a slightly
    higher-composite paper with no goal evidence (the validated Library
    blend, now applied to the slate)."""
    _insert(
        triage_db,
        item_key="GATE-FAV",
        decision="awaiting_review",
        composite_score=5.0,
        corpus_affinity=0.3,
        goal_sims={"clinical agents": 0.05},
    )
    _insert(
        triage_db,
        item_key="ON-GOAL",
        decision="awaiting_review",
        composite_score=4.5,
        corpus_affinity=0.3,
        goal_sims={"clinical agents": 0.85},
    )
    _insert(
        triage_db,
        item_key="FILLER",
        decision="awaiting_review",
        composite_score=1.0,
        corpus_affinity=0.3,
        goal_sims={"clinical agents": 0.2},
    )
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    model_keys = [p.item_key for p in slate.papers if p.role == "model"]
    assert model_keys[0] == "ON-GOAL"  # goal blend beats raw composite order
    on_goal = next(p for p in slate.papers if p.item_key == "ON-GOAL")
    assert on_goal.goal_sim == pytest.approx(0.85)


def test_no_goal_signal_preserves_composite_order(triage_db: Path) -> None:
    """Fold-back contract: with no goal_sims anywhere in the cohort the slate
    order is exactly the old composite-descending order."""
    for i in range(6):
        _insert(triage_db, item_key=f"C{i}", decision="awaiting_review",
                composite_score=1.0 + 0.5 * i, corpus_affinity=0.3)
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    model_papers = [p for p in slate.papers if p.role in {"model", "model_fallback"}]
    scores = [p.composite_score for p in model_papers]
    assert scores == sorted(scores, reverse=True)
    assert model_papers[0].item_key == "C5"


def test_model_role_floors_out_dont_band(triage_db: Path) -> None:
    """The relevance-floor fix: a dont_read-band paper (composite < 2.0) must
    NEVER appear as a model / model_fallback pick — on a weak feed week the
    top-K-by-blend would otherwise pad Today with below-the-bar papers."""
    # 3 could+ papers and 4 dont-band ones; corpus_affinity >= 0 so the diversity
    # picker (which is deliberately NOT floored) can't pull the weak ones in.
    for i, score in enumerate((2.1, 2.6, 3.1)):
        _insert(triage_db, item_key=f"OK{i}", decision="awaiting_review",
                composite_score=score, corpus_affinity=0.2)
    for i in range(4):
        _insert(triage_db, item_key=f"WEAK{i}", decision="awaiting_review",
                composite_score=1.0 + 0.2 * i, corpus_affinity=0.2)  # all < 2.0
    slate = assemble_daily_slate(db_path=triage_db, K=15, now=_DEFAULT_NOW)
    model_keys = {p.item_key for p in slate.papers if p.role in {"model", "model_fallback"}}
    assert model_keys == {"OK0", "OK1", "OK2"}
    assert not any(k.startswith("WEAK") for k in model_keys)
    # The 4 hidden dont-band papers are reported for the honest banner.
    assert slate.low_relevance_hidden == 4
    # No should_read-or-better candidate anywhere → weak-slate flag set.
    assert slate.weak_slate is True


def test_diversity_still_surfaces_floored_off_track_paper(triage_db: Path) -> None:
    """The floor is model-role only: an off-library (corpus_affinity < 0) paper
    below the band is STILL reachable by the diversity picker (Q2: surprise /
    diversity intentionally left un-floored)."""
    for i in range(3):
        _insert(triage_db, item_key=f"OK{i}", decision="awaiting_review",
                composite_score=2.5 + 0.1 * i, corpus_affinity=0.2)
    _insert(triage_db, item_key="WILD", decision="awaiting_review",
            composite_score=1.2, corpus_affinity=-0.5)  # dont-band AND off-track
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    diversity = [p.item_key for p in slate.papers if p.role == "diversity"]
    assert diversity == ["WILD"]                       # diversity ignores the floor
    model_keys = {p.item_key for p in slate.papers if p.role in {"model", "model_fallback"}}
    assert "WILD" not in model_keys                    # but the model role won't pick it


def test_strong_pool_is_not_flagged_weak(triage_db: Path) -> None:
    """weak_slate is False and nothing is hidden when the pool has a should+ paper."""
    _insert(triage_db, item_key="STRONG", decision="awaiting_review",
            composite_score=4.0, corpus_affinity=0.3)
    _insert(triage_db, item_key="MID", decision="awaiting_review",
            composite_score=2.5, corpus_affinity=0.3)
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    assert slate.weak_slate is False
    assert slate.low_relevance_hidden == 0


def test_assemble_daily_slate_surprise_floor(triage_db: Path) -> None:
    for i in range(10):
        _insert(
            triage_db,
            item_key=f"S{i}",
            decision="awaiting_review",
            composite_score=3.0 + 0.1 * i,
            surprise_score=0.10,  # all below the 0.30 floor
            corpus_affinity=0.3,
        )
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    assert "surprise" in slate.empty_role_events
    # No paper should claim the surprise role.
    assert all(p.role != "surprise" for p in slate.papers)


def test_assemble_daily_slate_diversity_requires_negative_affinity(
    triage_db: Path,
) -> None:
    for i in range(10):
        _insert(
            triage_db,
            item_key=f"A{i}",
            decision="awaiting_review",
            composite_score=3.0 + 0.1 * i,
            corpus_affinity=0.3,  # all positive
            shap_contribs_json=_make_shap_json(affinity=0.3, prestige=4.2),
        )
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    assert "diversity" in slate.empty_role_events
    assert all(p.role != "diversity" for p in slate.papers)


def test_assemble_daily_slate_ignores_gate_rejected(triage_db: Path) -> None:
    """gate_rejected rows never enter the slate — the former audit role is gone
    entirely (it degenerated into an endless 'spot-check forever' stream when
    the queue emptied); spot-check lives in the Review page + Today's SpotCheck
    section (services/library/review.list_by_state)."""
    for i in range(3):
        _insert(
            triage_db,
            item_key=f"G{i}",
            decision="gate_rejected",
            composite_score=1.0,
            shap_contribs_json=_make_shap_json(affinity=0.0, prestige=4.5),
        )
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    assert slate.papers == []  # gate_rejected alone never fills the slate
    assert slate.pool_size == 0


def test_assemble_daily_slate_K_smaller_than_pool(triage_db: Path) -> None:
    for i in range(20):
        _insert(
            triage_db,
            item_key=f"P{i:02d}",
            decision="awaiting_review",
            composite_score=1.0 + 0.2 * i,
        )
    slate = assemble_daily_slate(db_path=triage_db, K=3, now=_DEFAULT_NOW)
    assert len(slate.papers) == 3


def test_assemble_daily_slate_K_larger_than_pool(triage_db: Path) -> None:
    _insert(
        triage_db,
        item_key="P0",
        decision="awaiting_review",
        composite_score=3.0,
        corpus_affinity=0.3,
    )
    _insert(
        triage_db,
        item_key="P1",
        decision="awaiting_review",
        composite_score=4.0,
        corpus_affinity=0.3,
    )
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    # 2 papers from a pool of 2, multiple empty_role_events because the third
    # model slot + surprise + diversity all fall through to model_fallback which
    # itself runs out of candidates.
    assert len(slate.papers) == 2
    # Surprise should be in empty roles (no surprise_score >= 0.30).
    assert "surprise" in slate.empty_role_events
    # Diversity also empty (no negative-affinity rows).
    assert "diversity" in slate.empty_role_events


def test_assemble_daily_slate_rejects_invalid_K(triage_db: Path) -> None:
    with pytest.raises(ValueError, match="K must be positive"):
        assemble_daily_slate(db_path=triage_db, K=0, now=_DEFAULT_NOW)


def test_assemble_daily_slate_missing_db_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        assemble_daily_slate(
            db_path=tmp_path / "does_not_exist.db", K=5, now=_DEFAULT_NOW
        )
