"""Unit tests for the shared order-time blend (``services/model/rank_blend``).

Pure-math tests pinning the contract BOTH consumers (Library queue + Today
slate) rely on: min-max per cohort, weight fold-back when a signal is absent,
median-of-known for unknown prestige, per-row missing goal_sim → 0.0. Includes
the generalization case the fix shipped for: slate-shaped data (composite
scores on a 0-5 scale, [0,1] citation percentiles) — a different surface and
scale than the library benchmark the weights came from.
"""
from __future__ import annotations

import pytest

from zotero_summarizer.services.model.rank_blend import (
    DEFAULT_QUALITY_BAND_BONUS,
    DEFAULT_QUALITY_BONUS,
    GOAL_BLEND_WEIGHT,
    PRESTIGE_BLEND_WEIGHT,
    blend_scores,
    quality_bonus,
)


def test_no_signals_is_pure_relevance_order() -> None:
    rel = [1.0, 3.0, 2.0]
    keys = blend_scores(rel, [None] * 3, [None] * 3)
    assert sorted(range(3), key=lambda i: keys[i]) == [0, 2, 1]
    # Full weight folds back into relevance: best == 1.0, worst == 0.0.
    assert keys[1] == 1.0 and keys[0] == 0.0


def test_goal_lifts_on_goal_paper_over_higher_relevance() -> None:
    # The shipped behaviour: a lower-relevance paper that is the cohort's SOLE
    # goal evidence outranks the relevance leader with no goal evidence — driven
    # by the lone goal_sim getting FULL credit (1.0), not the uninformative 0.5.
    # Needs a 3rd (FILLER) row so the on-goal paper isn't the cohort relevance
    # floor: min-max over a 2-row cohort pins the lower paper to 0.0, where no
    # goal weight (0.4 < 0.6 relevance) can lift it — a property of the cohort
    # shape, not the blend. The slate/library always has such filler rows
    # (cf. test_daily_select.test_goal_sim_lifts_on_goal_paper_into_model_picks).
    rel = [5.0, 3.0, 1.0]
    goal = [None, 0.8, None]
    keys = blend_scores(rel, goal, [None, None, None])
    assert keys[1] > keys[0]  # on-goal (rel 3.0) beats off-goal leader (rel 5.0)
    assert keys[1] > keys[2]  # ... and the no-goal filler
    # With the lone goal at 0.5 (the old uninformative fill) the on-goal paper
    # would score 0.6·0.5 + 0.4·0.5 = 0.5 < 0.6 and lose — full credit is what
    # lifts it (0.6·0.5 + 0.4·1.0 = 0.7).
    assert keys[1] == pytest.approx(0.7)


def test_many_identical_goals_stay_uninformative_not_full_credit() -> None:
    # The sole-evidence exception is for ONE present value only. Several rows
    # sharing an identical goal_sim can't be separated → neutral 0.5 each
    # (uninformative), so relevance order stands — NOT a goal-driven flip.
    rel = [4.0, 2.0]
    keys = blend_scores(rel, [0.7, 0.7], [None, None])
    assert keys[0] > keys[1]                       # pure relevance order
    # Both goal terms are the SAME 0.5 (degenerate, not 1.0), so goal adds an
    # equal constant and never reorders the pair.
    assert keys[0] - keys[1] == pytest.approx(0.6)  # = w_rel · (1.0 − 0.0)


def test_goal_weight_folds_when_cohort_has_none() -> None:
    rel = [2.0, 4.0]
    with_goal = blend_scores(rel, [None, None], [None, None])
    assert with_goal == blend_scores(rel, [None, None], [None, None])
    assert with_goal[1] == 1.0  # 100% relevance weight


def test_unknown_prestige_scores_median_never_demoted_below_peers() -> None:
    # Four rows, equal relevance and goal; prestige known for three (low, mid,
    # high), unknown for the fourth → the unknown row keys at the known median
    # ("typical quality"), strictly between the extremes.
    rel = [3.0, 3.0, 3.0, 3.0]
    goal = [0.5, 0.5, 0.5, 0.5]
    prestige = [0.1, 0.9, None, 0.5]
    keys = blend_scores(rel, goal, prestige)
    assert keys[0] < keys[2] < keys[1]
    assert keys[2] == pytest.approx(keys[3])  # unknown == the median row


def test_per_row_missing_goal_scores_zero_on_that_term() -> None:
    # Cohort HAS goal evidence; a row without it ranks below an identical row
    # with evidence (no free ride for unmeasured papers).
    rel = [3.0, 3.0]
    keys = blend_scores(rel, [0.6, None], [None, None])
    assert keys[0] > keys[1]


def test_degenerate_cohort_is_uninformative_not_crash() -> None:
    # Single row / identical values → 0.5 normalization, no division by zero.
    assert blend_scores([3.0], [0.5], [0.7]) == [
        pytest.approx((1 - GOAL_BLEND_WEIGHT - PRESTIGE_BLEND_WEIGHT) * 0.5
                      + GOAL_BLEND_WEIGHT * 0.5 + PRESTIGE_BLEND_WEIGHT * 0.5)
    ]


def test_parallel_list_contract_is_enforced() -> None:
    with pytest.raises(ValueError):
        blend_scores([1.0, 2.0], [None], [None, None])


def test_empty_cohort() -> None:
    assert blend_scores([], [], []) == []


def test_generalizes_to_slate_shaped_scales() -> None:
    # Slate adapter shape: composite 0-5, goal_sim raw cosine, prestige [0,1]
    # citation percentile. Scale must be irrelevant (cohort min-max): the same
    # relative pattern in library shape (relevance 0-1, prestige 1-5) must
    # produce the same ORDER.
    slate_keys = blend_scores(
        [4.8, 4.0, 2.5], [0.1, 0.7, 0.4], [0.9, None, 0.2],
    )
    library_keys = blend_scores(
        [0.96, 0.80, 0.50], [0.1, 0.7, 0.4], [4.6, None, 1.8],
    )
    slate_order = sorted(range(3), key=lambda i: -slate_keys[i])
    library_order = sorted(range(3), key=lambda i: -library_keys[i])
    assert slate_order == library_order


# --- quality_bonus (shared pure helper, both consumers) ---------------------

def test_quality_bonus_grade_only_is_the_shipped_default() -> None:
    # use_band=False (the default) = the measured grade-only behaviour, band ignored.
    assert quality_bonus("highlight", "A") == DEFAULT_QUALITY_BONUS["A"]
    assert quality_bonus(None, "B") == DEFAULT_QUALITY_BONUS["B"]
    assert quality_bonus("flag", "C") == 0.0
    assert quality_bonus(None, "D") == DEFAULT_QUALITY_BONUS["D"]
    assert quality_bonus(None, None) == 0.0   # unreviewed → no lift
    assert quality_bonus(None, "Z") == 0.0    # unknown grade → no lift


def test_quality_bonus_band_primary_floats_highlight_sinks_flag() -> None:
    hi = quality_bonus("highlight", None, use_band=True)
    flag = quality_bonus("flag", None, use_band=True)
    assert hi > 0.0 > flag
    assert hi == pytest.approx(DEFAULT_QUALITY_BAND_BONUS["highlight"])
    assert flag == pytest.approx(DEFAULT_QUALITY_BAND_BONUS["flag"])


def test_quality_bonus_neutral_and_uncertain_are_exactly_zero() -> None:
    # The pinned safety invariant: uncertain is a self-consistency / human-look
    # state, NOT a demotion — it (and neutral) must resolve to EXACTLY 0.0 even
    # with a grade that would otherwise nudge negative, so borderline (clinical)
    # papers are never buried.
    for grade in ("A", "B", "C", "D", None):
        assert quality_bonus("uncertain", grade, use_band=True) == 0.0
        assert quality_bonus("neutral", grade, use_band=True) == 0.0
    assert quality_bonus("", "D", use_band=True) == 0.0       # unreviewed band
    assert quality_bonus("mystery", "A", use_band=True) == 0.0  # unknown band


def test_quality_bonus_band_primary_precedence_over_grade() -> None:
    # A flagged paper that happens to carry a high grade still nets negative —
    # the band leads, the grade only nudges.
    assert quality_bonus("flag", "A", use_band=True) < 0.0
    # A highlight with a poor grade still nets positive.
    assert quality_bonus("highlight", "D", use_band=True) > 0.0


def test_quality_bonus_grade_is_a_secondary_nudge_within_a_band() -> None:
    # Same band, better grade → strictly larger lift (the secondary nudge), but
    # the nudge never flips the band's sign.
    assert quality_bonus("highlight", "A", use_band=True) > quality_bonus(
        "highlight", "D", use_band=True
    )


def test_quality_bonus_is_bounded_across_every_combo() -> None:
    # The cap property the panel asked to pin: the lift is small/bounded for ALL
    # band×grade combinations (so it can only reorder within a relevance band).
    cap = max(abs(v) for v in DEFAULT_QUALITY_BAND_BONUS.values()) + 0.5 * max(
        abs(v) for v in DEFAULT_QUALITY_BONUS.values()
    )
    bands = [None, "", "highlight", "flag", "neutral", "uncertain", "mystery"]
    grades = [None, "A", "B", "C", "D", "Z"]
    for use_band in (False, True):
        for b in bands:
            for g in grades:
                assert abs(quality_bonus(b, g, use_band=use_band)) <= cap + 1e-9
