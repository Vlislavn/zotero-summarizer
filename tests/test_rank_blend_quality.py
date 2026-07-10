"""Pins the quality-first order key's contracts (the user's directive as asserts).

The directive: *"a high-quality paper a little bit off-topic beats a poor-quality
paper on-topic."* ``test_directive_invariant`` is that sentence made executable.
"""
from __future__ import annotations

import pytest

from zotero_summarizer.services.model.rank_blend_quality import (
    FLAG_QUALITY_CAP,
    GATE_FLOOR,
    quality_first_key,
    unified_quality,
)


def test_directive_invariant_grade_a_offtopic_beats_grade_d_ontopic():
    # A off-topic (goal_sim min, dim 1) vs D on-topic (goal_sim max, dim 5).
    keys = quality_first_key(
        [unified_quality("A", None), unified_quality("D", None)],
        goal_sim=[0.0, 1.0],
        goal_alignment=[1.0, 5.0],
    )
    assert keys[0] > keys[1]
    # D's q=0 zeroes the key regardless of topicality; A off-topic keeps GATE_FLOOR.
    assert keys[1] == pytest.approx(0.0)
    assert keys[0] == pytest.approx(GATE_FLOOR)


def test_grade_anchors_are_absolute():
    assert unified_quality("A", None) == 1.0
    assert unified_quality("B", None) == 0.75
    assert unified_quality("C", None) == 0.4
    assert unified_quality("D", None) == 0.0


def test_flag_band_caps_quality_below_any_letter():
    assert unified_quality("A", "flag") == FLAG_QUALITY_CAP
    assert unified_quality("C", "flag") == FLAG_QUALITY_CAP  # cap < C anchor 0.4


def test_neutral_and_uncertain_never_demote():
    assert unified_quality("B", "neutral") == 0.75
    assert unified_quality("B", "uncertain") == 0.75


def test_unreviewed_prior_stays_inside_band():
    assert unified_quality(None, None) == 0.5                       # no dims
    assert unified_quality(None, None, rigor=5, evidence=5) == 0.75  # top → HI
    assert unified_quality(None, None, rigor=1, evidence=1) == 0.25  # floor → LO
    mid = unified_quality(None, None, rigor=3, evidence=3)
    assert 0.25 < mid < 0.75
    # Never out-ranks a reviewed A, never sinks below the flag cap.
    assert unified_quality(None, None, rigor=5, evidence=5) < unified_quality("A", None)
    assert unified_quality(None, None, rigor=1, evidence=1) >= FLAG_QUALITY_CAP


def test_soft_gate_off_topic_keeps_half_quality():
    # Neither topical signal present → t=0.5 → gate = 0.5 + 0.5*0.5 = 0.75.
    off = quality_first_key([1.0], goal_sim=[None], goal_alignment=[None])
    assert off[0] == pytest.approx(0.75)


def test_topicality_one_signal_not_capped_by_missing_dim():
    # goal_sim at cohort max, goal_alignment absent: per-row renorm gives t=1.0
    # (full gate), NOT t=0.6 (which would silently cap the ~80% dim-less rows).
    keys = quality_first_key([1.0, 1.0], goal_sim=[1.0, 0.0], goal_alignment=[None, None])
    assert keys[0] == pytest.approx(1.0)   # on-topic, no dim → full gate
    assert keys[1] == pytest.approx(GATE_FLOOR)


def test_degenerate_cohort_orders_by_pure_quality():
    # Identical goal_sim → norm 0.5 for all → gate equal → order is q alone.
    keys = quality_first_key([0.75, 0.4], goal_sim=[0.3, 0.3], goal_alignment=[None, None])
    assert keys[0] > keys[1]


def test_parallel_list_length_mismatch_raises():
    with pytest.raises(ValueError):
        quality_first_key([1.0, 0.0], goal_sim=[0.5], goal_alignment=[None, None])
