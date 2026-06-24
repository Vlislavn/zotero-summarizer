"""Unit tests for the pure agreement stats in tools/eval_rigor_vs_band.py.

These pin the validity-gate math (weighted κ, Spearman, the false-strong-on-flag
clinical cell) the P4 study uses to decide whether the incumbent abstract-rigor
earns a ranking weight. No DB / no LLM: ``main()`` defers every heavy import.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "eval_rigor_vs_band",
    Path(__file__).resolve().parents[1] / "tools" / "eval_rigor_vs_band.py",
)
ev = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ev)


def test_bin_rigor_thresholds() -> None:
    assert [ev._bin_rigor(r) for r in (1, 2, 3, 4, 5)] == [0, 0, 1, 2, 2]


def test_band_ordinal_holds_uncertain_out() -> None:
    assert ev._band_ordinal("flag") == 0
    assert ev._band_ordinal("neutral") == 1
    assert ev._band_ordinal("highlight") == 2
    assert ev._band_ordinal("uncertain") is None   # held out, not scored
    assert ev._band_ordinal("") is None
    assert ev._band_ordinal(None) is None


def test_weighted_kappa_perfect_and_disagreement() -> None:
    perfect = [(0, 0), (1, 1), (2, 2), (0, 0), (2, 2)]
    assert ev._weighted_kappa(perfect) == pytest.approx(1.0)
    # Systematic far-apart disagreement → strongly negative weighted κ.
    bad = [(0, 2), (2, 0), (0, 2), (2, 0)]
    assert ev._weighted_kappa(bad) < 0.0
    # Single category for both raters → no expected disagreement → 1.0, no crash.
    assert ev._weighted_kappa([(1, 1), (1, 1)]) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        ev._weighted_kappa([])


def test_spearman_monotonic() -> None:
    assert ev._spearman([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)
    assert ev._spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert ev._spearman([1, 1, 1], [1, 2, 3]) == 0.0   # zero variance → 0, no crash
    with pytest.raises(ValueError):
        ev._spearman([1.0], [1.0])


def test_false_strong_on_flag_counts_the_harmful_cell() -> None:
    pairs = [(5.0, "flag"), (4.0, "flag"), (3.0, "flag"), (5.0, "highlight"), (2.0, "flag")]
    # rigor≥4 AND band==flag → the first two only.
    assert ev._false_strong_on_flag(pairs) == 2


def test_auc_matches_separation() -> None:
    assert ev._auc([0.9, 0.8, 0.2, 0.1], [1, 1, 0, 0]) == 1.0
    with pytest.raises(ValueError):
        ev._auc([0.5, 0.6], [1, 1])
