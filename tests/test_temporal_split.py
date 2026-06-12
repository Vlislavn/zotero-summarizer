"""Unit tests for the temporal-holdout diagnostic in classifier_training.

Pure-function tests (no CSV / corpus / network): the split must hold out the
NEWEST rows as whole groups, undated rows must sort as oldest, and the metric
helper must return its defined ``None`` (not a fake number) whenever a forward
Spearman would be noise — tiny holdouts and constant label vectors, which
small test fixtures legitimately produce.
"""
from __future__ import annotations

import numpy as np

from zotero_summarizer.services.model.classifier_training import (
    _TrainMatrix,
    _temporal_group_split,
    _temporal_holdout_metrics,
)


def _rows(days: list[float | None]) -> list[dict]:
    return [
        {"days_since_added": "" if d is None else str(d)}
        for d in days
    ]


def test_split_holds_out_newest_whole_groups() -> None:
    # 10 groups × 2 rows, ages 100..10 — the newest 20% (2 groups, 4 rows)
    # must be held out, and group pairs must never straddle the split.
    days = [float(d) for d in (100, 100, 90, 90, 80, 80, 70, 70, 60, 60,
                               50, 50, 40, 40, 30, 30, 20, 20, 10, 10)]
    groups = [f"g{i // 2}" for i in range(20)]
    tr, te = _temporal_group_split(_rows(days), groups)
    assert sorted(te.tolist()) == [16, 17, 18, 19]  # ages 20 and 10
    assert sorted(tr.tolist()) == list(range(16))
    for g in set(groups):
        sides = {("te" if i in set(te.tolist()) else "tr") for i, gg in enumerate(groups) if gg == g}
        assert len(sides) == 1  # whole group on one side


def test_rows_without_dates_sort_oldest() -> None:
    # Undated rows must land in TRAIN (treated as oldest), never the holdout.
    days: list[float | None] = [None, None, 30.0, 20.0, 10.0, 5.0, 4.0, 3.0, 2.0, 1.0]
    groups = [f"g{i}" for i in range(10)]
    tr, te = _temporal_group_split(_rows(days), groups)
    assert 0 in tr.tolist() and 1 in tr.tolist()
    assert all(days[i] is not None for i in te.tolist())


def _synthetic(n: int, *, constant_y: bool = False, seed: int = 7):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 5)).astype(np.float32)
    if constant_y:
        y = np.full(n, 3.0)
    else:
        y = np.clip(3.0 + 1.5 * X[:, 0] + rng.normal(scale=0.3, size=n), 1.0, 5.0)
    sw = np.ones(n, dtype=np.float32)
    rows = _rows([float(n - i) for i in range(n)])  # strictly newer toward the end
    groups = [f"g{i}" for i in range(n)]
    return X, y, sw, rows, groups


def test_metrics_none_when_holdout_too_small() -> None:
    X, y, sw, rows, groups = _synthetic(40)  # 20% of 40 = 8 < _TEMPORAL_MIN_TEST
    assert _temporal_holdout_metrics(
        "lightgbm", _TrainMatrix(X, y, sw), rows, groups, pca_dim=100
    ) is None


def test_metrics_none_on_constant_labels() -> None:
    X, y, sw, rows, groups = _synthetic(200, constant_y=True)
    assert _temporal_holdout_metrics(
        "lightgbm", _TrainMatrix(X, y, sw), rows, groups, pca_dim=100
    ) is None


def test_metrics_computed_on_learnable_signal() -> None:
    X, y, sw, rows, groups = _synthetic(200)
    out = _temporal_holdout_metrics("lightgbm", _TrainMatrix(X, y, sw), rows, groups, pca_dim=100)
    assert out is not None
    assert out["temporal_holdout_n"] == 40
    # y is a clean function of X[:, 0]; the forward Spearman must reflect it.
    assert 0.5 < out["temporal_spearman"] <= 1.0
