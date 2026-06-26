"""Tests for services/surprise.py — black-swan surprise scoring."""
from __future__ import annotations

from zotero_summarizer.services.model.surprise import compute_surprise_score


def test_surprise_low_when_affinity_high():
    # mean(5,5)/5 = 1.0; 1.0 - 0.9 = 0.1 (low surprise — paper already familiar)
    score = compute_surprise_score(5.0, 5.0, 0.9)
    assert 0.05 < score < 0.15


def test_surprise_max_when_quality_high_affinity_zero():
    # mean(5,5)/5 = 1.0; 1.0 - 0.0 = 1.0 (max surprise)
    assert compute_surprise_score(5.0, 5.0, 0.0) == 1.0


def test_surprise_clipped_to_one_with_negative_affinity():
    # mean(4,4)/5 = 0.8; 0.8 - (-0.5) = 1.3 -> clip to 1.0
    assert compute_surprise_score(4.0, 4.0, -0.5) == 1.0


def test_surprise_low_quality_low_affinity_still_yields_some_surprise():
    # Even mediocre papers from outside the user's library produce small surprise.
    # mean(3,3)/5 = 0.6; 0.6 - 0.1 = 0.5
    assert compute_surprise_score(3.0, 3.0, 0.1) == 0.5


def test_surprise_zero_when_dim_quality_below_affinity():
    # mean(1,1)/5 = 0.2; 0.2 - 0.5 = -0.3 -> clip to 0
    assert compute_surprise_score(1.0, 1.0, 0.5) == 0.0


def test_surprise_score_bounded_zero_one():
    for r in (0, 2, 4, 5):
        for n in (0, 2, 4, 5):
            for a in (-1.0, -0.5, 0.0, 0.5, 1.0):
                s = compute_surprise_score(r, n, a)
                assert 0.0 <= s <= 1.0


def test_surprise_nan_returns_zero():
    assert compute_surprise_score(float("nan"), 5.0, 0.0) == 0.0
