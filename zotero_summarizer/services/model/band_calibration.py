"""Out-of-fold monotonic band calibration for the relevance gate.

The regressor ORDERS papers well (high OOF Spearman) but its continuous scores
are compressed toward the mean, so the top band (``must_read``, score ≥ 4.5)
is almost never reached — ``must_read`` recall collapses even for genuinely
great papers. This fits an ISOTONIC (monotone) map ``raw_score → relevance`` on
OUT-OF-FOLD predictions vs the true relevance, so the bins become reachable
WITHOUT changing the ranking (monotone ⇒ argsort and Spearman are preserved; we
deliberately apply it only to the 4-class band, never to the scores used for
ordering).

Honest by construction for a regime with FEW genuinely-great papers and sparse
``must`` labels:

* Isotonic is fit to the REAL label distribution, so it cannot push mass into
  the top band beyond what the data supports (no quantile-inflation of must).
* The calibrator is kept ONLY if it improves the out-of-fold ``must`` + ``should``
  macro-F1 — measured against precision *and* recall, so a calibration that
  floods the top with false ``must_read`` (precision drop) is rejected. Worst
  case is identity. If the model genuinely cannot separate the great papers,
  calibration won't fake it — the real fix is more ``must`` labels, not this.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from zotero_summarizer.domain import ReadingPriority, score_to_priority


# The bands the user acts on: surfacing the few great (must) + worth-reading
# (should) papers. Culling (could/dont) is already well-served, so the calibrator
# is gated on how well it recovers THESE two bands.
_TOP_BANDS = (ReadingPriority.MUST_READ.value, ReadingPriority.SHOULD_READ.value)


def _top_band_macro_f1(gold: list[str], pred: list[str]) -> float:
    """Macro-F1 over the ``must_read`` + ``should_read`` bands (precision AND recall
    — so flooding the top with false positives is penalised, not rewarded)."""
    from zotero_summarizer.services.model import golden_metrics as gm

    per = gm.compute_per_class(gold, pred)
    f1s = [per[b].f1 for b in _TOP_BANDS if b in per]
    return float(sum(f1s) / len(f1s)) if f1s else 0.0


def _bins(scores: np.ndarray) -> list[str]:
    return [score_to_priority(float(s)) for s in np.clip(scores, 1.0, 5.0)]


def fit_band_calibrator(
    preds_oof: Any, y_true: Any, gold_labels: list[str],
) -> tuple[Any, dict[str, Any]]:
    """Fit an OOF isotonic ``raw → relevance`` map; keep it only if it helps.

    Returns ``(calibrator | None, diagnostics)``. ``None`` means "raw scores
    already band best" → predict falls back to identity. The diagnostics record
    the raw vs calibrated top-band F1 and whether it was applied (surfaced in the
    model card so the effect on THIS library is visible, not asserted).
    """
    from sklearn.isotonic import IsotonicRegression

    preds = np.asarray(preds_oof, dtype=np.float64)
    y = np.asarray(y_true, dtype=np.float64)

    f1_raw = _top_band_macro_f1(gold_labels, _bins(preds))

    calibrator = None
    f1_cal = f1_raw
    if preds.size >= 2 and np.unique(preds).size >= 2:
        iso = IsotonicRegression(y_min=1.0, y_max=5.0, out_of_bounds="clip")
        iso.fit(preds, y)
        f1_cal = _top_band_macro_f1(gold_labels, _bins(iso.predict(preds)))
        if f1_cal > f1_raw:
            calibrator = iso

    diag = {
        "applied": calibrator is not None,
        "top_band_f1_raw": round(f1_raw, 4),
        "top_band_f1_calibrated": round(f1_cal, 4),
    }
    return calibrator, diag


def apply_band_calibration(calibrator: Any, scores: Any) -> np.ndarray:
    """Map raw scores → calibrated band scores in ``[1, 5]``; identity when
    ``calibrator is None`` (backward-compatible with pre-calibration artifacts)."""
    clipped = np.clip(np.asarray(scores, dtype=np.float64), 1.0, 5.0)
    if calibrator is None:
        return clipped
    return np.clip(calibrator.predict(clipped), 1.0, 5.0)
