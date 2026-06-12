"""Unit tests for the heuristic, no-LLM reason-chip builders.

Pure-function tests (no DB / I/O). They pin the threshold boundaries to the
reused canonical constants so a chip and the rest of the pipeline can never
silently drift apart, and lock the ordering + 3-chip cap. Goal chips key on the
REAL ``goal_sim`` signal with pool-relative tercile bands (``goal_bands``);
``corpus_affinity`` chips are the honestly-labeled engagement signal.
"""
from __future__ import annotations

import pytest

from zotero_summarizer.domain import PRIORITY_SHOULD_READ_THRESHOLD
from zotero_summarizer.services.model.surprise import DEFAULT_BLACK_SWAN_MIN_SCORE
from zotero_summarizer.services.triage.daily_select._relevance import (
    attach_why,
    build_why,
    goal_bands,
)


def _why(**overrides):
    base = dict(composite_score=1.0, corpus_affinity=0.0, surprise_score=0.0)
    base.update(overrides)
    return build_why(**base)


def _cand(goal_sim=None, **overrides):
    base = dict(
        composite_score=1.0,
        corpus_affinity=0.0,
        surprise_score=0.0,
        goal_sim=goal_sim,
    )
    base.update(overrides)
    return base


# --- goal chips: keyed to goal_sim with cohort bands ------------------------


def test_goal_chip_requires_goal_sim_not_affinity() -> None:
    # The old conflation: high engagement affinity must NOT produce a goal chip.
    why = _why(corpus_affinity=0.9, goal_sim=None, goal_strong=0.5, goal_mild=0.3)
    assert "Strong goal match" not in why
    assert "On-topic for you" not in why
    assert why[0] == "Like papers you've saved"


def test_goal_chip_requires_bands() -> None:
    # No cohort bands (no positive goal signal in the pool) → no goal chip,
    # even when the card itself carries a goal_sim.
    assert "Strong goal match" not in _why(goal_sim=0.9)


def test_strong_and_mild_goal_chips() -> None:
    assert _why(goal_sim=0.6, goal_strong=0.5, goal_mild=0.3)[0] == "Strong goal match"
    why = _why(goal_sim=0.4, goal_strong=0.5, goal_mild=0.3)
    assert why[0] == "On-topic for you"
    assert "Strong goal match" not in why


def test_nonpositive_goal_sim_never_chips() -> None:
    assert "Strong goal match" not in _why(goal_sim=-0.2, goal_strong=-0.5, goal_mild=-0.6)


def test_goal_bands_are_pool_terciles() -> None:
    cands = [_cand(goal_sim=v) for v in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6)]
    strong, mild = goal_bands(cands)
    assert strong == 0.5  # lower edge of the top tercile
    assert mild == 0.3    # lower edge of the middle tercile


def test_goal_bands_ignore_absent_and_nonpositive() -> None:
    cands = [_cand(goal_sim=None), _cand(goal_sim=-0.4), _cand(goal_sim=0.0)]
    assert goal_bands(cands) == (None, None)


def test_goal_bands_need_three_positive_points() -> None:
    # A tercile needs 3 points. With 1–2 positives the bands degenerate
    # (strong == the lone weak value) and a goal_sim of 0.2 would be crowned
    # "Strong goal match" right under the weak-week banner. No bands instead.
    assert goal_bands([_cand(goal_sim=0.2)]) == (None, None)
    assert goal_bands([_cand(goal_sim=0.2), _cand(goal_sim=0.4)]) == (None, None)
    # Exactly 3 positives is the minimum that yields real terciles.
    strong, mild = goal_bands([_cand(goal_sim=v) for v in (0.2, 0.4, 0.6)])
    assert (strong, mild) == (0.6, 0.4)


def test_attach_why_lone_weak_goal_sim_gets_no_goal_chip() -> None:
    # The observed live bug: a single positive goal_sim=0.2 in the cohort
    # showed "Strong goal match" while the banner said "Light week".
    cands = [_cand(goal_sim=0.2), _cand(goal_sim=None), _cand(goal_sim=0.0)]
    attach_why(cands)
    assert cands[0]["why"] == []


def test_attach_why_labels_cohort_relative() -> None:
    cands = [_cand(goal_sim=v) for v in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6)]
    attach_why(cands)
    assert cands[-1]["why"][0] == "Strong goal match"   # top of the pool
    assert cands[2]["why"][0] == "On-topic for you"     # middle tercile
    assert cands[0]["why"] == []                        # bottom tercile: no chip


# --- library-engagement chips (honest labels) -------------------------------


def test_strong_affinity_is_library_chip() -> None:
    assert _why(corpus_affinity=0.30)[0] == "Like papers you've saved"


def test_neutral_affinity_emits_no_chip() -> None:
    assert _why(corpus_affinity=0.0) == []
    assert _why(corpus_affinity=0.05) == []


def test_negative_affinity_is_off_track() -> None:
    assert _why(corpus_affinity=-0.01) == ["Off your usual track"]


# --- model relevance / prestige / citations / surprise ---------------------


@pytest.mark.parametrize(
    "composite, present",
    [(PRIORITY_SHOULD_READ_THRESHOLD, True), (PRIORITY_SHOULD_READ_THRESHOLD - 0.01, False)],
)
def test_high_model_relevance_boundary(composite: float, present: bool) -> None:
    assert ("High model relevance" in _why(composite_score=composite)) is present


@pytest.mark.parametrize("h, present", [(30, True), (29, False)])
def test_senior_authors_boundary(h: int, present: bool) -> None:
    why = _why(h_index=h)
    assert (f"Senior authors (h={h})" in why) is present


@pytest.mark.parametrize("pct, present", [(0.80, True), (0.79, False)])
def test_highly_cited_boundary(pct: float, present: bool) -> None:
    assert ("Highly cited" in _why(citation_percentile=pct)) is present


@pytest.mark.parametrize(
    "surprise, present",
    [(DEFAULT_BLACK_SWAN_MIN_SCORE, True), (DEFAULT_BLACK_SWAN_MIN_SCORE - 0.01, False)],
)
def test_surprising_boundary(surprise: float, present: bool) -> None:
    assert ("Surprising" in _why(surprise_score=surprise)) is present


# --- shape invariants ------------------------------------------------------


def test_missing_optional_signals_omit_chips_no_crash() -> None:
    # h_index / citation_percentile / goal_sim default to None → simply omitted.
    assert _why(composite_score=4.0) == ["High model relevance"]


def test_empty_when_nothing_clears_threshold() -> None:
    assert _why(composite_score=2.0, corpus_affinity=0.05, surprise_score=0.1) == []


def test_capped_at_three_strongest_first() -> None:
    why = build_why(
        composite_score=5.0,
        corpus_affinity=0.9,
        surprise_score=0.9,
        h_index=99,
        citation_percentile=0.99,
        goal_sim=0.9,
        goal_strong=0.5,
        goal_mild=0.3,
    )
    assert len(why) == 3
    # Strongest signals win, in order: goal → library → model relevance.
    assert why == ["Strong goal match", "Like papers you've saved", "High model relevance"]
