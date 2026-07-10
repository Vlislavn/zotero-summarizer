"""Unit tests for the P3 team-draft interleave kernel (services/model/team_draft)
and the SPRT / engagement scoring math (tools/eval_interleave.py, pure top half).

These pin the decision instrument for the quality-first flip: a mis-drafted
merge or a wrong SPRT boundary silently corrupts weeks of online evidence, so
every contract the scorer relies on is asserted here. No DB, no app state.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from zotero_summarizer.services.model.team_draft import (
    TEAM_A,
    TEAM_B,
    TEAM_BOTH,
    DraftPick,
    team_draft_merge,
)

_TOOLS = Path(__file__).resolve().parents[1] / "tools"
_SPEC = importlib.util.spec_from_file_location("eval_interleave", _TOOLS / "eval_interleave.py")
ei = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ei)


# --- team_draft_merge ----------------------------------------------------------

def test_merge_is_deterministic_per_seed_and_varies_across_seeds() -> None:
    a, b = [1, 2, 3, 4, 5], [6, 7, 8, 9, 10]
    assert team_draft_merge(a, b, 5, seed="d1") == team_draft_merge(a, b, 5, seed="d1")
    # Disjoint lists: the coin decides who leads each round — across many seeds
    # both arms must lead at least once (a stuck coin would bias the experiment).
    leaders = {team_draft_merge(a, b, 2, seed=f"s{i}")[0].team for i in range(20)}
    assert leaders == {TEAM_A, TEAM_B}


def test_merge_no_duplicates_and_respects_k() -> None:
    a, b = [1, 2, 3], [3, 2, 9]
    out = team_draft_merge(a, b, 4, seed="x")
    items = [p.item for p in out]
    assert len(items) == len(set(items)) <= 4


def test_shared_next_pick_collapses_to_both_with_no_pair() -> None:
    out = team_draft_merge([7, 1], [7, 2], 3, seed="x")
    assert out[0] == DraftPick(7, TEAM_BOTH, None)
    # The remaining round is competitive: one pick per team, shared pair_id.
    competitive = [p for p in out if p.pair_id is not None]
    assert {p.team for p in competitive} == {TEAM_A, TEAM_B}
    assert len({p.pair_id for p in competitive}) == 1


def test_identical_lists_are_all_both() -> None:
    out = team_draft_merge([1, 2, 3], [1, 2, 3], 3, seed="x")
    assert all(p.team == TEAM_BOTH and p.pair_id is None for p in out)


def test_exhausted_arm_yields_uncontested_unpaired_picks() -> None:
    out = team_draft_merge([1], [1, 2, 3], 3, seed="x")
    assert out[0] == DraftPick(1, TEAM_BOTH, None)
    assert [(p.item, p.team, p.pair_id) for p in out[1:]] == [(2, TEAM_B, None), (3, TEAM_B, None)]


def test_k_truncation_never_emits_a_half_pair() -> None:
    # k=1 with disagreeing tops: exactly one pick, and it must be unpaired —
    # a pair needs both sides shown to be a comparison.
    for seed in ("a", "b", "c"):
        out = team_draft_merge([1, 2], [3, 4], 1, seed=seed)
        assert len(out) == 1 and out[0].pair_id is None


def test_second_drafter_skips_the_item_the_first_just_took() -> None:
    # b's top is 1; if a drafts 1 first, b must draft its NEXT item (2), not skip
    # the round — and both carry the same pair_id.
    for seed in range(30):
        out = team_draft_merge([1, 9], [1, 2], 4, seed=str(seed))
        items = [p.item for p in out]
        assert items[0] == 1 and len(items) == len(set(items))
        paired = [p for p in out if p.pair_id is not None]
        assert len(paired) % 2 == 0


def test_duplicate_input_items_fail_fast() -> None:
    with pytest.raises(ValueError):
        team_draft_merge([1, 1], [2], 2, seed="x")
    with pytest.raises(ValueError):
        team_draft_merge([1], [2], 0, seed="x")


# --- eval_interleave pure math ---------------------------------------------------

def test_engagement_ladder() -> None:
    assert ei.engagement_score("must_read", None) == 3
    # explicit verdict wins over any decision
    assert ei.engagement_score("dont_read", "selected", "materialized_via_today_add") == -1
    # kept decisions count ONLY with a user-action reason
    assert ei.engagement_score(None, "selected", "materialized_via_today_add") == 2
    assert ei.engagement_score(None, "user_approved", "today_add_zotero_pending") == 2
    assert ei.engagement_score(None, "selected", "materialized_via_review_apply") == 2
    assert ei.engagement_score(None, "user_approved", "user_relabel:must_read:from_x") == 2
    assert ei.engagement_score(None, "user_rejected") == -1
    assert ei.engagement_score(None, "awaiting_review") == 0
    assert ei.engagement_score(None, None) == 0
    with pytest.raises(ValueError):
        ei.engagement_score("bogus", None)


def test_machine_authored_keeps_are_firewalled() -> None:
    # The daemon's run_daily_selection writes the SAME decisions with plateau/
    # surprise reasons and zero user involvement — they must score 0, never +2.
    assert ei.engagement_score(None, "selected", "elbow") == 0
    assert ei.engagement_score(None, "selected", "cap_overrode_elbow") == 0
    assert ei.engagement_score(None, "selected", "flat_distribution_fallback_to_cap") == 0
    assert ei.engagement_score(None, "black_swan", "surprise_pick") == 0
    # Unknown/missing reason: conservative 0, never a credited trial.
    assert ei.engagement_score(None, "selected", None) == 0
    assert ei.engagement_score(None, "selected", "future_writer_reason") == 0


def test_score_pairs_counts_engagement_only_in_last_pair() -> None:
    # Item 1 (a2's persistent leader) lingers unverdicted across two days and is
    # re-paired each day; its one eventual must_read must count as ONE trial —
    # in the LAST pair only — not replay as N correlated wins.
    log = [
        {"day": "d1", "item_id": 1, "team": "a2", "pair_id": 1},
        {"day": "d1", "item_id": 2, "team": "a0", "pair_id": 1},
        {"day": "d2", "item_id": 1, "team": "a2", "pair_id": 1},
        {"day": "d2", "item_id": 3, "team": "a0", "pair_id": 1},
    ]
    wins, per_day = ei.score_pairs(log, {1: 3, 2: 0, 3: 0})
    assert wins == {"a0": 0, "a2": 1, "tie": 1}
    assert per_day["d1"] == {"pairs": 1, "a2": 0, "a0": 0, "tie": 1}
    assert per_day["d2"]["a2"] == 1


def test_score_pairs_fails_loud_on_corrupt_log() -> None:
    with pytest.raises(ValueError):  # duplicate team inside one pair
        ei.score_pairs(
            [
                {"day": "d1", "item_id": 1, "team": "a2", "pair_id": 1},
                {"day": "d1", "item_id": 2, "team": "a2", "pair_id": 1},
            ],
            {1: 0, 2: 0},
        )
    with pytest.raises(ValueError):  # half pair (only one team present)
        ei.score_pairs([{"day": "d1", "item_id": 1, "team": "a2", "pair_id": 1}], {1: 0})


def test_pair_outcome() -> None:
    assert ei.pair_outcome(0, 2) == "a2"
    assert ei.pair_outcome(2, -1) == "a0"
    assert ei.pair_outcome(0, 0) == "tie"


def test_sprt_boundaries_and_direction() -> None:
    import math
    upper = math.log((1 - ei.SPRT_BETA) / ei.SPRT_ALPHA)  # ln(9) at 0.10/0.10
    # Fresh experiment: no evidence → continue.
    llr, state = ei.sprt_state(0, 0)
    assert llr == 0.0 and state == "continue"
    # Enough consecutive A2 wins crosses the flip boundary (ln(0.7/0.5)≈0.336 → 7 wins).
    llr, state = ei.sprt_state(7, 0)
    assert state == "flip" and llr >= upper
    # A0 losses push toward keep (ln(0.3/0.5)≈-0.511 → 5 losses).
    llr, state = ei.sprt_state(0, 5)
    assert state == "keep" and llr <= -upper
    # Mixed evidence stays inside the boundaries.
    assert ei.sprt_state(3, 2)[1] == "continue"
    with pytest.raises(ValueError):
        ei.sprt_state(1, 1, p1=0.4)
