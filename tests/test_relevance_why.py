"""Unit tests for the heuristic, no-LLM ``build_why`` reason-chip builder.

Pure-function tests (no DB / I/O). They pin the threshold boundaries to the
reused canonical constants so a chip and the rest of the pipeline can never
silently drift apart, and lock the ordering + 3-chip cap.
"""
from __future__ import annotations

import pytest

from zotero_summarizer.domain import PRIORITY_SHOULD_READ_THRESHOLD
from zotero_summarizer.services.model.surprise import DEFAULT_BLACK_SWAN_MIN_SCORE
from zotero_summarizer.services.triage.daily_select._relevance import build_why


def _why(**overrides):
    base = dict(composite_score=1.0, corpus_affinity=0.0, surprise_score=0.0)
    base.update(overrides)
    return build_why(**base)


# --- goal match (primary lever, exactly one chip) --------------------------


def test_strong_goal_match_at_threshold() -> None:
    assert _why(corpus_affinity=0.30)[0] == "Strong goal match"


def test_on_topic_band() -> None:
    why = _why(corpus_affinity=0.10)
    assert "On-topic for you" in why
    assert "Strong goal match" not in why


def test_neutral_affinity_emits_no_goal_chip() -> None:
    # 0.0 is neither >= 0.10 nor < 0.0 → no goal chip.
    assert _why(corpus_affinity=0.0) == []
    assert _why(corpus_affinity=0.05) == []


def test_negative_affinity_is_off_track() -> None:
    assert _why(corpus_affinity=-0.01) == ["Off your usual track"]


def test_only_one_goal_chip() -> None:
    why = _why(corpus_affinity=0.9)
    assert why.count("Strong goal match") == 1
    assert "On-topic for you" not in why


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
    # h_index / citation_percentile default to None → simply omitted.
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
    )
    assert len(why) == 3
    # Strongest signals win, in order: goal → model relevance → author standing.
    assert why == ["Strong goal match", "High model relevance", "Senior authors (h=99)"]
