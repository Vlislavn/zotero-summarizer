"""Forward-looking temporal-holdout diagnostic for the relevance regressor.

Production always predicts the FUTURE (today's feed) from the PAST (everything
labeled so far), but the shuffled GroupKFold OOF lets folds train on rows newer
than their validation rows: measured 0.653 OOF Spearman vs 0.394 on a strict
train-on-oldest/test-on-newest split of the same data
(tools/eval_temporal_objective.py, 2026-06-12, n=1852, dont_read share 52%→68%
across eras). Every retrain logs the forward-looking number next to the
comparable OOF series so drift and honest performance stay visible. Lives in its
own module so ``classifier_training`` stays under the 500-LOC cap.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from zotero_summarizer.services.model import classifier

_TEMPORAL_HOLDOUT_FRACTION = 0.2
_TEMPORAL_MIN_TEST = 30  # below this a forward Spearman is noise, not signal
_NO_DATE_SENTINEL = 1e9  # undated rows sort as oldest, never in the holdout


def _row_days(row: dict[str, Any]) -> float:
    """``days_since_added`` as a sortable age; UNDATED rows return the sentinel.

    Feed-sourced rows carry ``days_since_added == "-1"`` ("no Zotero date yet"),
    and they are ~72% of training-eligible rows. The old ``float(raw) if raw``
    parsed ``-1`` as the *newest* possible age, so the forward-looking temporal
    split (sorts ascending, holds out the newest) became ~94% undated feed rows —
    measuring feed-junk separation, not recent reading decisions (fixed
    2026-06-19). Any non-positive value is therefore treated as undated.
    """
    raw = (row.get("days_since_added") or "").strip()
    if not raw:
        return _NO_DATE_SENTINEL
    days = float(raw)
    return days if days >= 0 else _NO_DATE_SENTINEL


def _temporal_group_split(
    train_rows: list[dict[str, Any]], groups: list[str]
) -> tuple["np.ndarray", "np.ndarray"]:
    """``(train_idx, test_idx)``: newest ~20% of the DATED rows held out, whole groups.

    Group-aware (``paper_group_id``) so the same paper never lands on both
    sides; a group's age is its NEWEST member (min ``days_since_added``). Only
    genuinely-dated groups can enter the forward holdout — undated (sentinel)
    rows are training context, never the "future" we test against. The holdout
    fraction is taken over the DATED pool (not all rows), so a corpus that is
    mostly undated doesn't swallow the entire dated set into the test side.
    """
    group_age: dict[str, float] = {}
    for row, g in zip(train_rows, groups):
        d = _row_days(row)
        group_age[g] = min(d, group_age.get(g, _NO_DATE_SENTINEL))
    dated_groups = [
        g for g in sorted(group_age, key=lambda g: group_age[g])
        if group_age[g] < _NO_DATE_SENTINEL
    ]
    n_dated_rows = sum(1 for g in groups if group_age[g] < _NO_DATE_SENTINEL)
    target = int(n_dated_rows * _TEMPORAL_HOLDOUT_FRACTION)
    test_groups: set[str] = set()
    covered = 0
    for g in dated_groups:
        if covered >= target:
            break
        test_groups.add(g)
        covered += sum(1 for gg in groups if gg == g)
    test_mask = np.asarray([g in test_groups for g in groups])
    return np.where(~test_mask)[0], np.where(test_mask)[0]


def _temporal_holdout_metrics(
    classifier_name: str,
    matrix: Any,
    train_rows: list[dict[str, Any]],
    groups: list[str],
    *,
    pca_dim: int,
) -> dict[str, Any] | None:
    """Forward-looking Spearman on the newest-20% holdout; one extra fit.

    ``matrix`` is any object exposing ``X``/``y``/``sample_weight`` arrays (the
    training :class:`_TrainMatrix`). ``None`` is the defined "metric not
    computable" result — holdout under ``_TEMPORAL_MIN_TEST`` rows or a constant
    label vector on either side (Spearman undefined) — which small test fixtures
    legitimately hit.
    """
    from scipy.stats import spearmanr

    X, y, sw = matrix.X, matrix.y, matrix.sample_weight
    tr, te = _temporal_group_split(train_rows, groups)
    if len(te) < _TEMPORAL_MIN_TEST:
        return None
    if len(set(y[tr].tolist())) < 2 or len(set(y[te].tolist())) < 2:
        return None
    _, p_te = classifier._fit_predict(
        classifier_name, X[tr], y[tr], X[te],
        pca_dim=pca_dim, return_train_probs=False,
        objective="regression", sample_weight=sw[tr],
    )
    rho = float(spearmanr(y[te], p_te).statistic)
    return {"temporal_spearman": round(rho, 4), "temporal_holdout_n": int(len(te))}
