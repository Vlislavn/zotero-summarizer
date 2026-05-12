"""Tests for services/surprise.py — black-swan scoring and slot allocation."""
from __future__ import annotations

from dataclasses import dataclass

from zotero_summarizer.services.surprise import (
    allocate_black_swan_slots,
    compute_surprise_score,
)


@dataclass
class _C:
    key: str
    surprise_score: float


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


def test_allocate_zero_slots_when_inbox_empty():
    r = allocate_black_swan_slots(0, [], set())
    assert r.slot_count == 0
    assert r.black_swan_selected == []


def test_allocate_floor_of_fraction():
    # inbox=15, fraction=0.10 -> floor(1.5) = 1 slot
    pool = [_C(f"k{i}", 0.9 - i * 0.05) for i in range(10)]
    r = allocate_black_swan_slots(15, pool, set(), fraction=0.10)
    assert r.slot_count == 1
    assert len(r.black_swan_selected) == 1
    assert r.black_swan_selected[0].key == "k0"


def test_allocate_excludes_already_selected_keys():
    pool = [_C("a", 0.9), _C("b", 0.8), _C("c", 0.7)]
    r = allocate_black_swan_slots(20, pool, {"a"}, fraction=0.10)  # 2 slots
    keys = [c.key for c in r.black_swan_selected]
    assert "a" not in keys
    assert keys == ["b", "c"]


def test_allocate_respects_min_score_threshold():
    pool = [_C("a", 0.20), _C("b", 0.25), _C("c", 0.50)]
    r = allocate_black_swan_slots(20, pool, set(), fraction=0.10, min_score=0.30)
    keys = [c.key for c in r.black_swan_selected]
    assert keys == ["c"]


def test_allocate_returns_top_n_sorted_descending():
    pool = [_C(f"k{i}", i * 0.1) for i in range(10)]  # 0.0 .. 0.9
    r = allocate_black_swan_slots(30, pool, set(), fraction=0.10, min_score=0.0)
    keys = [c.key for c in r.black_swan_selected]
    # 30 * 0.10 = 3 slots; top 3 by score: k9 (0.9), k8 (0.8), k7 (0.7)
    assert keys == ["k9", "k8", "k7"]
