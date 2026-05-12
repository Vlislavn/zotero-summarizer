"""Tests for services/select.py — kneed-based plateau selection."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from zotero_summarizer.services.select import plateau_select


@dataclass
class _Candidate:
    name: str
    composite_score: float


def test_empty_batch_returns_zero_cutoff():
    r = plateau_select([], hard_max=15)
    assert r.cutoff == 0
    assert r.selected == []
    assert r.rejected == []
    assert r.reason == "empty_batch"


def test_tiny_batch_capped_by_total():
    items = [_Candidate(f"t{i}", 4.0 - i) for i in range(3)]
    r = plateau_select(items, hard_min=10, hard_max=15)
    # When total < hard_min, safety_cap clips to total
    assert r.cutoff == 3
    assert len(r.selected) == 3
    assert len(r.rejected) == 0


def test_sharp_elbow_picks_plateau_within_safety_cap():
    """A sharp drop after 8 items should yield an elbow-based cutoff <= 15."""
    random.seed(42)
    high = [_Candidate(f"h{i}", 4.5 - i * 0.05) for i in range(8)]
    low = [_Candidate(f"l{i}", 1.5 + random.random() * 0.3) for i in range(50)]
    r = plateau_select(high + low, hard_max=15)
    assert r.reason == "elbow"
    assert r.knee_index is not None
    assert 5 <= r.cutoff <= 15
    # The selected list must be the highest scorers
    assert r.selected[0].composite_score >= r.selected[-1].composite_score
    assert r.selected[-1].composite_score >= r.rejected[0].composite_score


def test_flat_distribution_falls_back_to_safety_cap():
    items = [_Candidate(f"f{i}", 3.0) for i in range(40)]
    r = plateau_select(items, target_fraction=0.05, hard_min=10, hard_max=15)
    # Flat curve has no elbow; cap at min(15, max(10, 0.05*40))=10
    assert r.cutoff <= 15
    assert r.reason in ("flat_distribution_fallback_to_cap", "cap_overrode_elbow", "elbow")


def test_safety_cap_never_exceeds_hard_max():
    """N=1000 with mild decay; hard_max=15 must clip regardless of elbow."""
    items = [_Candidate(f"i{i}", 5.0 * math.exp(-i * 0.003)) for i in range(1000)]
    r = plateau_select(items, target_fraction=0.05, hard_min=10, hard_max=15)
    assert r.cutoff <= 15
    assert r.safety_cap <= 15


def test_descending_input_unsorted_still_works():
    """Input order should not matter — selection sorts internally."""
    items = [
        _Candidate("b", 2.0),
        _Candidate("a", 5.0),
        _Candidate("e", 1.0),
        _Candidate("c", 4.0),
        _Candidate("d", 3.0),
    ]
    r = plateau_select(items, hard_min=2, hard_max=3)
    assert r.cutoff <= 3
    assert r.selected[0].name == "a"  # Highest score first


def test_score_curve_attribute_populated():
    items = [_Candidate(f"x{i}", float(10 - i)) for i in range(5)]
    r = plateau_select(items, hard_max=10)
    assert r.score_curve == [10.0, 9.0, 8.0, 7.0, 6.0]
