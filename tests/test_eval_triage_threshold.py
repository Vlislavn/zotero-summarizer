"""Unit tests for the PURE sweep helpers in tools/eval_triage_threshold.py.

These pin the measurement core (precision/recall/F1 of the gate's keep-decision
at each candidate threshold, and the F1-best selection) on hand-built synthetic
arrays where the optimal threshold is KNOWN. They need no DB and no corpus model:
``main()`` defers every heavy import, so importing the module is cheap.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "eval_triage_threshold",
    Path(__file__).resolve().parents[1] / "tools" / "eval_triage_threshold.py",
)
ev = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ev)


# A cleanly separable cohort: low scores are trashed, high scores are kept.
SCORES = [0.1, 0.4, 0.6, 0.9]
LABELS = [0, 0, 1, 1]


def test_prf_at_perfect_separating_threshold() -> None:
    # threshold 0.5: keep {0.6, 0.9} (both label 1) → perfect precision & recall.
    precision, recall, f1, n_kept = ev._prf(SCORES, LABELS, 0.5)
    assert precision == pytest.approx(1.0)
    assert recall == pytest.approx(1.0)
    assert f1 == pytest.approx(1.0)
    assert n_kept == 2


def test_prf_at_too_low_threshold_keeps_everything() -> None:
    # threshold -1.0: keep all 4 → 2 of 4 kept are user-kept → precision 0.5,
    # recall 1.0 (no user-kept row was dropped), F1 = 2*.5*1/(1.5) = 0.6667.
    precision, recall, f1, n_kept = ev._prf(SCORES, LABELS, -1.0)
    assert precision == pytest.approx(0.5)
    assert recall == pytest.approx(1.0)
    assert f1 == pytest.approx(2 / 3)
    assert n_kept == 4


def test_prf_at_too_high_threshold_keeps_nothing() -> None:
    # threshold 1.0: keep nothing → precision undefined→0, recall 0 (both kept
    # rows dropped), F1 0. n_kept 0.
    precision, recall, f1, n_kept = ev._prf(SCORES, LABELS, 1.0)
    assert precision == 0.0
    assert recall == 0.0
    assert f1 == 0.0
    assert n_kept == 0


def test_prf_partial_recall_drops_one_kept() -> None:
    # threshold 0.7: keep only {0.9} → 1 true positive, the 0.6 user-kept row is
    # dropped → precision 1.0, recall 0.5, F1 = 2*1*.5/1.5 = 0.6667.
    precision, recall, f1, n_kept = ev._prf(SCORES, LABELS, 0.7)
    assert precision == pytest.approx(1.0)
    assert recall == pytest.approx(0.5)
    assert f1 == pytest.approx(2 / 3)
    assert n_kept == 1


def test_keep_is_inclusive_at_the_threshold() -> None:
    # score == threshold must KEEP (gate fast-rejects only on score < threshold).
    precision, recall, f1, n_kept = ev._prf([0.6], [1], 0.6)
    assert n_kept == 1 and precision == pytest.approx(1.0)


def test_sweep_returns_one_row_per_threshold_in_order() -> None:
    thresholds = [0.0, 0.5, 0.95]
    sweep = ev.sweep_threshold(SCORES, LABELS, thresholds)
    assert [r["threshold"] for r in sweep] == thresholds
    assert set(sweep[0]) == {"threshold", "precision", "recall", "f1", "n_kept"}


def test_sweep_rejects_empty_thresholds_and_length_mismatch() -> None:
    with pytest.raises(ValueError):
        ev.sweep_threshold(SCORES, LABELS, [])
    with pytest.raises(ValueError):
        ev._prf([0.1, 0.2], [1], 0.0)  # mismatched lengths must fail loud


def test_best_by_f1_finds_the_perfect_separator() -> None:
    # The optimal threshold sits between 0.4 (trashed) and 0.6 (kept). Any
    # candidate in (0.4, 0.6] yields F1 == 1.0; lower/higher do not.
    thresholds = [0.0, 0.2, 0.45, 0.5, 0.55, 0.7, 0.95]
    sweep = ev.sweep_threshold(SCORES, LABELS, thresholds)
    best = ev.best_by_f1(sweep)
    assert best["f1"] == pytest.approx(1.0)
    assert 0.4 < best["threshold"] <= 0.6
    # Tie-break prefers the HIGHER threshold (cheaper gate) among the F1==1.0 set.
    perfect = [r["threshold"] for r in sweep if r["f1"] == pytest.approx(1.0)]
    assert best["threshold"] == max(perfect)


def test_best_by_f1_rejects_empty_sweep() -> None:
    with pytest.raises(ValueError):
        ev.best_by_f1([])


def test_precision_floored_returns_none_when_floor_unreachable() -> None:
    # No threshold reaches precision >= 0.99 here except ones that keep only
    # pure-kept rows; with a floor above ANY achievable precision → None.
    sweep = ev.sweep_threshold([0.1, 0.2], [0, 0], [0.0])  # all trashed
    assert ev.best_precision_floored(sweep, 0.5) is None


def test_precision_floored_picks_best_f1_above_floor() -> None:
    thresholds = [0.0, 0.5, 0.7, 0.95]
    sweep = ev.sweep_threshold(SCORES, LABELS, thresholds)
    # Floor 0.9: only thresholds with precision >= 0.9 survive (0.5→1.0, 0.7→1.0,
    # 0.95→0). Among survivors F1 is maximized at 0.5 (perfect recall too).
    floored = ev.best_precision_floored(sweep, 0.9)
    assert floored is not None
    assert floored["threshold"] == pytest.approx(0.5)
    assert floored["f1"] == pytest.approx(1.0)


def test_bootstrap_f1_ci_is_deterministic_and_brackets_point_estimate() -> None:
    scores = [0.05, 0.15, 0.25, 0.65, 0.75, 0.85]
    labels = [0, 0, 0, 1, 1, 1]
    a = ev._bootstrap_f1_ci(scores, labels, 0.5, n_boot=500, seed=7)
    b = ev._bootstrap_f1_ci(scores, labels, 0.5, n_boot=500, seed=7)
    assert a == b  # same seed → identical (repro safe)
    lo, hi = a
    point = ev._prf(scores, labels, 0.5)[2]
    assert 0.0 <= lo <= hi <= 1.0
    assert lo <= point <= hi
