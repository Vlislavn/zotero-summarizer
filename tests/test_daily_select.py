"""Tests for Phase 1.17 Step 1 — :func:`assemble_daily_slate`.

Strategy: seed an in-memory-like sqlite DB (file-backed in ``tmp_path``)
directly with synthetic ``processed_feed_items`` rows. This avoids any
SPECTER2 / OpenAlex / LLM round-trips. The role-allocation logic, the
backlog cap, the surprise floor, and the day-stable RNG can all be exercised
with crafted rows.
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
    assert slate.capped_at == 0


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
        assert paper.role in {"model", "surprise", "audit", "diversity", "model_fallback"}


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


def test_assemble_daily_slate_backlog_cap(triage_db: Path) -> None:
    for i in range(100):
        _insert(
            triage_db,
            item_key=f"P{i:03d}",
            decision="awaiting_review",
            composite_score=float(i) / 20.0,  # 0..5
        )
    slate = assemble_daily_slate(
        db_path=triage_db, K=5, backlog_cap=10, now=_DEFAULT_NOW
    )
    assert slate.pool_size == 100
    assert slate.capped_at == 10
    # Top 10 by composite_score should still be chosen at the head.
    top_paper = max(slate.papers, key=lambda p: p.composite_score)
    assert top_paper.composite_score >= 4.0


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


def test_assemble_daily_slate_audit_pool_from_gate_rejected(triage_db: Path) -> None:
    for i in range(5):
        _insert(
            triage_db,
            item_key=f"R{i}",
            decision="awaiting_review",
            composite_score=3.0 + 0.2 * i,
        )
    for i in range(3):
        _insert(
            triage_db,
            item_key=f"G{i}",
            decision="gate_rejected",
            composite_score=1.0,
            shap_contribs_json=_make_shap_json(affinity=0.0, prestige=4.5),
        )
    # The audit role is no longer in the default slate (it degenerated into an
    # endless one-at-a-time stream when the primary pool was empty); callers can
    # still opt in by passing roles explicitly, which is what we exercise here.
    slate = assemble_daily_slate(
        db_path=triage_db,
        K=5,
        roles={"model": 2, "surprise": 1, "audit": 1, "diversity": 1},
        now=_DEFAULT_NOW,
    )
    audit_papers = [p for p in slate.papers if p.role == "audit"]
    assert len(audit_papers) == 1
    assert audit_papers[0].item_key.startswith("G")
    assert audit_papers[0].decision == "gate_rejected"


def test_assemble_daily_slate_default_has_no_audit(triage_db: Path) -> None:
    """The default slate must never allocate the audit role — that was the
    source of the endless 'spot-check forever' stream when the queue emptied."""
    for i in range(3):
        _insert(
            triage_db,
            item_key=f"G{i}",
            decision="gate_rejected",
            composite_score=1.0,
            shap_contribs_json=_make_shap_json(affinity=0.0, prestige=4.5),
        )
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    assert [p for p in slate.papers if p.role == "audit"] == []
    assert slate.papers == []  # gate_rejected alone no longer fills the slate


def test_assemble_daily_slate_audit_deterministic_within_day(triage_db: Path) -> None:
    # Seed enough primary rows so model/surprise/diversity don't consume audit fallback.
    for i in range(5):
        _insert(
            triage_db,
            item_key=f"R{i}",
            decision="awaiting_review",
            composite_score=3.0 + 0.2 * i,
        )
    # 8 gate_rejected candidates so the audit RNG actually picks one of many.
    for i in range(8):
        _insert(
            triage_db,
            item_key=f"G{i}",
            decision="gate_rejected",
            composite_score=1.0,
            shap_contribs_json=_make_shap_json(affinity=0.0, prestige=4.5),
        )
    roles = {"model": 2, "surprise": 1, "audit": 1, "diversity": 1}
    slate_a = assemble_daily_slate(db_path=triage_db, K=5, roles=roles, now=_DEFAULT_NOW)
    slate_b = assemble_daily_slate(db_path=triage_db, K=5, roles=roles, now=_DEFAULT_NOW)
    audit_a = [p.item_key for p in slate_a.papers if p.role == "audit"]
    audit_b = [p.item_key for p in slate_b.papers if p.role == "audit"]
    assert audit_a == audit_b
    assert audit_a  # non-empty


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
    # itself runs out of candidates. (audit is no longer a default role.)
    assert len(slate.papers) == 2
    # Surprise should be in empty roles (no surprise_score >= 0.30).
    assert "surprise" in slate.empty_role_events
    # Diversity also empty (no negative-affinity rows).
    assert "diversity" in slate.empty_role_events
    # audit is not a default role anymore, so it must not appear.
    assert "audit" not in slate.empty_role_events


def test_assemble_daily_slate_rejects_invalid_K(triage_db: Path) -> None:
    with pytest.raises(ValueError, match="K must be positive"):
        assemble_daily_slate(db_path=triage_db, K=0, now=_DEFAULT_NOW)


def test_assemble_daily_slate_missing_db_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        assemble_daily_slate(
            db_path=tmp_path / "does_not_exist.db", K=5, now=_DEFAULT_NOW
        )


# ---------------------------------------------------------------------------
# Handled papers (rated or labeled) drop out of the slate
# ---------------------------------------------------------------------------


def test_feed_name_flows_to_slate_paper(triage_db: Path) -> None:
    # Provenance: feed_name on the row must reach SlatePaper for the card badge.
    _insert(triage_db, item_key="http://arxiv.org/abs/N", decision="triaged_pending",
            composite_score=4.0, feed_item_id=900, feed_name="bioRxiv")
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    paper = next(p for p in slate.papers if p.item_key == "http://arxiv.org/abs/N")
    assert paper.feed_name == "bioRxiv"
    assert paper.role  # bucket is set


def test_rated_paper_excluded_from_slate(triage_db: Path) -> None:
    # Two fresh model candidates; one gets an after-reading rating.
    _insert(triage_db, item_key="http://arxiv.org/abs/A", decision="triaged_pending",
            composite_score=4.0, feed_item_id=501)
    _insert(triage_db, item_key="http://arxiv.org/abs/B", decision="triaged_pending",
            composite_score=3.0, feed_item_id=502)
    repo.insert_role_value_verdict(
        triage_db, item_key="http://arxiv.org/abs/A", role="model",
        verdict="waste", composite_score=4.0, surprise_score=None, corpus_affinity=None,
    )
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    keys = {p.item_key for p in slate.papers}
    assert "http://arxiv.org/abs/A" not in keys
    assert "http://arxiv.org/abs/B" in keys


def test_labeled_paper_excluded_from_slate(triage_db: Path) -> None:
    _insert(triage_db, item_key="http://arxiv.org/abs/C", decision="triaged_pending",
            composite_score=4.0, feed_item_id=601)
    _insert(triage_db, item_key="http://arxiv.org/abs/D", decision="triaged_pending",
            composite_score=3.0, feed_item_id=602)
    repo.insert_or_update_label_verdict(
        triage_db, item_key="feed:601",
        original_derived_priority="could_read", user_priority="must_read", comment="",
    )
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    keys = {p.item_key for p in slate.papers}
    assert "http://arxiv.org/abs/C" not in keys
    assert "http://arxiv.org/abs/D" in keys


def test_count_awaiting_unhandled_excludes_handled(triage_db: Path) -> None:
    # Two pending papers; one already labeled (handled). The honest counter the
    # Today header now uses must report 1, not 2 (the old raw count returned 2
    # and disagreed with the slate).
    _insert(triage_db, item_key="http://arxiv.org/abs/X", decision="triaged_pending",
            composite_score=4.0, feed_item_id=801)
    _insert(triage_db, item_key="http://arxiv.org/abs/Y", decision="triaged_pending",
            composite_score=3.0, feed_item_id=802)
    repo.insert_or_update_label_verdict(
        triage_db, item_key="feed:801",
        original_derived_priority="could_read", user_priority="dont_read", comment="",
    )
    assert count_awaiting_unhandled(triage_db) == 1
    # Matches the slate's own count exactly (single source of truth).
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    assert count_awaiting_unhandled(triage_db) == len(slate.papers)


def test_count_awaiting_unhandled_zero_when_all_handled(triage_db: Path) -> None:
    _insert(triage_db, item_key="http://arxiv.org/abs/Z", decision="triaged_pending",
            composite_score=4.0, feed_item_id=811)
    repo.insert_or_update_label_verdict(
        triage_db, item_key="feed:811",
        original_derived_priority="could_read", user_priority="must_read", comment="",
    )
    assert count_awaiting_unhandled(triage_db) == 0


def test_handled_paper_excluded_from_recent_fallback(triage_db: Path) -> None:
    # Old rows (outside the 168h window) so the slate must use the recent
    # fallback; the labeled one is still excluded there too.
    old = _DEFAULT_NOW - timedelta(days=30)
    _insert(triage_db, item_key="http://arxiv.org/abs/E", decision="triaged_pending",
            composite_score=4.0, feed_item_id=701, created_at=old)
    _insert(triage_db, item_key="http://arxiv.org/abs/F", decision="triaged_pending",
            composite_score=3.0, feed_item_id=702, created_at=old)
    repo.insert_role_value_verdict(
        triage_db, item_key="http://arxiv.org/abs/E", role="model",
        verdict="worth", composite_score=4.0, surprise_score=None, corpus_affinity=None,
    )
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    keys = {p.item_key for p in slate.papers}
    assert slate.fellback_to_recent is True
    assert "http://arxiv.org/abs/E" not in keys
    assert "http://arxiv.org/abs/F" in keys


# ---------------------------------------------------------------------------
# More cards: the model quota scales with K (was capped at 5 by 3/1/1 default)
# ---------------------------------------------------------------------------


def test_assemble_daily_slate_more_cards_at_high_K(triage_db: Path) -> None:
    # 20 plain awaiting rows (no surprise / negative affinity), so surprise +
    # diversity fall through to model_fallback. K=15 must return 15, not 5.
    for i in range(20):
        _insert(
            triage_db,
            item_key=f"M{i:02d}",
            decision="awaiting_review",
            composite_score=1.0 + 0.2 * i,
            corpus_affinity=0.3,
        )
    slate = assemble_daily_slate(db_path=triage_db, K=15, now=_DEFAULT_NOW)
    assert len(slate.papers) == 15  # > 5 (old fixed 3/1/1 cap)


def test_assemble_daily_slate_K5_still_three_model(triage_db: Path) -> None:
    # Back-compat: K=5 reproduces the legacy 3 model + 1 surprise + 1 diversity.
    for i in range(10):
        _insert(triage_db, item_key=f"B{i:02d}", decision="awaiting_review",
                composite_score=1.0 + 0.3 * i, corpus_affinity=0.3)
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    assert len(slate.papers) == 5


# ---------------------------------------------------------------------------
# "Why it matters" chips flow onto SlatePaper
# ---------------------------------------------------------------------------


def test_why_chips_strong_goal_match(triage_db: Path) -> None:
    _insert(triage_db, item_key="W-goal", decision="awaiting_review",
            composite_score=4.7, corpus_affinity=0.42)
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    paper = next(p for p in slate.papers if p.item_key == "W-goal")
    assert paper.why[0] == "Strong goal match"
    assert len(paper.why) <= 3


def test_why_chips_off_track_for_diversity(triage_db: Path) -> None:
    _insert(triage_db, item_key="W-div", decision="awaiting_review",
            composite_score=2.0, corpus_affinity=-0.4)
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    paper = next(p for p in slate.papers if p.item_key == "W-div")
    assert "Off your usual track" in paper.why


def test_why_chips_surprising(triage_db: Path) -> None:
    _insert(triage_db, item_key="W-surp", decision="awaiting_review",
            composite_score=2.0, surprise_score=0.85, corpus_affinity=0.0)
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    paper = next(p for p in slate.papers if p.item_key == "W-surp")
    assert "Surprising" in paper.why


def test_why_chips_built_without_shap_blob(triage_db: Path) -> None:
    # Older rows predate the SHAP blob — `why` must still build from the
    # dedicated columns (proves gate-independence: no LightGBM SHAP needed).
    _insert(triage_db, item_key="W-noshap", decision="awaiting_review",
            composite_score=4.6, corpus_affinity=0.5, shap_contribs_json="")
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    paper = next(p for p in slate.papers if p.item_key == "W-noshap")
    assert paper.why[0] == "Strong goal match"
    assert "High model relevance" in paper.why
