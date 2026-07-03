"""Tests for the quality → must_read band promotion (Part 2).

Catches the structural bug: the gate's compressed regressor never reaches the
must_read band (≥4.5) on its own (0/69 must recall), so must_read was empty even
for genuinely great papers. ``promote_band`` lifts a high-quality + on-goal +
gate-confirmed paper to must_read — the band the gate can't reach.
"""
from __future__ import annotations

import pytest

from zotero_summarizer.domain import ReadingPriority
from zotero_summarizer.services.model.rank_blend import promote_band

MUST = ReadingPriority.MUST_READ.value
SHOULD = ReadingPriority.SHOULD_READ.value
COULD = ReadingPriority.COULD_READ.value
DONT = ReadingPriority.DONT_READ.value


def test_default_relevance_floor_lowered_to_3_0() -> None:
    # Regression: the default floor was lowered 3.5→3.0 (tools/eval_quality_promote.py
    # measured 3.0 promotes 3 must + 9 should at 1.00 precision / 0 flooding, vs just 1
    # at 3.5 under gate compression). A grade-A, on-goal paper the gate scored 3.2 now
    # promotes to must_read; at the old 3.5 floor it did not.
    assert promote_band(COULD, grade="A", relevance_score=3.2, goal_sim=0.6) == MUST
    # still no promotion below the new floor.
    assert promote_band(COULD, grade="A", relevance_score=2.9, goal_sim=0.6) == COULD


def test_quality_plus_on_goal_plus_gate_confirmed_promotes_to_must() -> None:
    """The headline case: grade A, strong goal_sim, gate ≥ should-band → must_read."""
    assert promote_band(SHOULD, grade="A", relevance_score=4.0, goal_sim=0.7) == MUST
    assert promote_band(COULD, grade="B", relevance_score=3.6, goal_sim=0.55) == MUST


def test_quality_but_off_goal_lifts_to_should_not_must() -> None:
    """Grade A + gate-confirmed but weak goal_sim → should_read, not must_read."""
    assert promote_band(COULD, grade="A", relevance_score=4.0, goal_sim=0.3) == SHOULD
    assert promote_band(SHOULD, grade="B", relevance_score=3.5, goal_sim=0.54) == SHOULD


def test_low_grade_never_promotes() -> None:
    """Grade C/D/None → the raw band unchanged (quality not strong enough to lift)."""
    assert promote_band(COULD, grade="C", relevance_score=4.0, goal_sim=0.9) == COULD
    assert promote_band(SHOULD, grade="D", relevance_score=4.0, goal_sim=0.9) == SHOULD
    assert promote_band(COULD, grade=None, relevance_score=4.0, goal_sim=0.9) == COULD


def test_gate_below_should_band_never_promotes() -> None:
    """A dont_read / low-could paper is never lifted — promotion only crosses ONE
    band up from a gate-confirmed should-band floor, never invents must from dont."""
    assert promote_band(DONT, grade="A", relevance_score=2.5, goal_sim=0.9) == DONT
    # Below the (now 3.0) gate floor → no promotion, however strong the grade/goal.
    assert promote_band(COULD, grade="A", relevance_score=2.99, goal_sim=0.9) == COULD


def test_absent_signal_is_no_promotion() -> None:
    """None grade / relevance / goal_sim = "not assessed" → no promotion (mirrors
    the no-hide-on-absent-evidence contract, ADR-B2)."""
    assert promote_band(SHOULD, grade=None, relevance_score=4.0, goal_sim=0.7) == SHOULD
    assert promote_band(SHOULD, grade="A", relevance_score=None, goal_sim=0.7) == SHOULD
    assert promote_band(SHOULD, grade="A", relevance_score=4.0, goal_sim=None) == SHOULD


def test_promote_only_lifts_one_band() -> None:
    """A should_read paper with grade A + on-goal → must_read (one band up), NOT
    inflated beyond. A could_read → must_read is allowed (one band up to should,
    then must on goal) — but a DONT stays dont (the floor gate)."""
    assert promote_band(SHOULD, grade="A", relevance_score=4.0, goal_sim=0.7) == MUST
    # could → must is the one-band-up-then-goal case (relevance ≥ should-band)
    assert promote_band(COULD, grade="A", relevance_score=3.5, goal_sim=0.7) == MUST


@pytest.mark.parametrize("raw", [MUST, SHOULD, COULD, DONT, ""])
def test_low_grade_is_identity(raw: str) -> None:
    """Grade C with any signals → raw band unchanged (promote-off parity for the
    non-promoting case)."""
    assert promote_band(raw, grade="C", relevance_score=4.5, goal_sim=0.99) == raw
