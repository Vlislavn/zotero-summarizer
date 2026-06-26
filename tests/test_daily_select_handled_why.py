"""Slate behaviour around handled papers, K-scaling, and "why" chips.

Split out of ``test_daily_select.py`` (file-size limit): same seeded-sqlite
strategy, no model/LLM round-trips. Core assembly/ordering tests stay in
``test_daily_select.py``.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from zotero_summarizer.services.triage.daily_select import (
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


def test_why_chips_strong_goal_match_keys_on_goal_sim(triage_db: Path) -> None:
    # The goal chip requires REAL goal evidence (goal_sims), not engagement
    # affinity; bands are cohort terciles, so seed a spread and check the top.
    for i, sim in enumerate((0.1, 0.3, 0.8)):
        _insert(triage_db, item_key=f"W-goal{i}", decision="awaiting_review",
                composite_score=2.0 + i, corpus_affinity=0.0,
                goal_sims={"clinical agents": sim})
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    top = next(p for p in slate.papers if p.item_key == "W-goal2")
    assert top.why[0] == "Strong goal match"
    assert len(top.why) <= 3


def test_why_chips_library_affinity_is_not_goal_match(triage_db: Path) -> None:
    # High engagement affinity alone must yield the honest library chip,
    # never a goal chip (the label-drift fix).
    _insert(triage_db, item_key="W-lib", decision="awaiting_review",
            composite_score=4.7, corpus_affinity=0.42)
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    paper = next(p for p in slate.papers if p.item_key == "W-lib")
    assert paper.why[0] == "Like papers you've saved"
    assert "Strong goal match" not in paper.why


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
    # No payload ⇒ no goal_sims ⇒ goal_sim is honestly None: library chip only.
    _insert(triage_db, item_key="W-noshap", decision="awaiting_review",
            composite_score=4.6, corpus_affinity=0.5, shap_contribs_json="")
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    paper = next(p for p in slate.papers if p.item_key == "W-noshap")
    assert paper.goal_sim is None
    assert paper.why[0] == "Like papers you've saved"
    assert "High model relevance" in paper.why
