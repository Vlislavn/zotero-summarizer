"""Out-of-fold band calibration: monotone, self-gated, identity-safe.

Makes the compressed top band reachable (must_read recall) WITHOUT reordering,
and only when it actually improves OOF must+should F1."""

from __future__ import annotations

import numpy as np

from zotero_summarizer.domain import score_to_priority
from zotero_summarizer.services.model.band_calibration import (
    apply_band_calibration,
    fit_band_calibrator,
)


# Compressed predictions: the regressor ORDERS correctly (must>should>could>dont)
# but squashes the top — a true must_read (5.0) is predicted 4.2, which bins to
# should_read (<4.5), so raw must_read recall is 0.
_PREDS = np.array([4.2] * 10 + [3.8] * 10 + [2.8] * 10 + [1.5] * 10, dtype=float)
_Y_TRUE = np.array([5.0] * 10 + [4.0] * 10 + [3.0] * 10 + [1.0] * 10, dtype=float)
_GOLD = (["must_read"] * 10 + ["should_read"] * 10 + ["could_read"] * 10 + ["dont_read"] * 10)


def test_identity_when_calibrator_is_none():
    out = apply_band_calibration(None, [0.5, 3.7, 9.0])
    assert list(out) == [1.0, 3.7, 5.0]  # clipped to [1,5], otherwise unchanged


def test_raw_top_band_is_broken_without_calibration():
    # Sanity: the compressed must_read predictions never cross 4.5.
    assert score_to_priority(4.2) == "should_read"


def test_calibrator_is_applied_when_it_lifts_top_band_f1():
    cal, diag = fit_band_calibrator(_PREDS, _Y_TRUE, _GOLD)
    assert cal is not None
    assert diag["applied"] is True
    assert diag["top_band_f1_calibrated"] > diag["top_band_f1_raw"]


def test_calibration_makes_the_top_band_reachable():
    cal, _ = fit_band_calibrator(_PREDS, _Y_TRUE, _GOLD)
    # the compressed must_read score now crosses into must_read
    banded = apply_band_calibration(cal, [4.2])
    assert score_to_priority(float(banded[0])) == "must_read"


def test_calibration_preserves_ranking_monotone():
    cal, _ = fit_band_calibrator(_PREDS, _Y_TRUE, _GOLD)
    scores = np.linspace(1.0, 5.0, 25)
    banded = apply_band_calibration(cal, scores)
    assert np.all(np.diff(banded) >= -1e-9)  # non-decreasing ⇒ argsort preserved


def test_identity_fallback_when_already_well_banded():
    # Raw predictions already land in the correct bins → calibration can't beat
    # them → returns None (identity), never a regression.
    preds = np.array([4.8] * 10 + [4.0] * 10 + [2.8] * 10 + [1.5] * 10, dtype=float)
    cal, diag = fit_band_calibrator(preds, _Y_TRUE, _GOLD)
    assert cal is None
    assert diag["applied"] is False


def test_degenerate_constant_predictions_return_identity():
    cal, diag = fit_band_calibrator(np.full(20, 3.0), _Y_TRUE[:20], _GOLD[:20])
    assert cal is None and diag["applied"] is False
