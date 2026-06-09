"""TabPFN inference is pinned to CPU, not the contended shared GPU pool.

Regression for `TabPFNMPSOutOfMemoryError` that crashed gate scoring: a
`device="auto"` TabPFN became the third claimant on the SPECTER2+reranker-
saturated Apple-Silicon MPS pool and OOM'd ("MPS out of memory with 50 test
samples"). TabPFN re-fits in-context per predict over a tiny ~500-row context,
so CPU costs ~nothing and *cannot* OOM (no fixed memory ceiling).

These tests pin every TabPFN constructor to `device="cpu"` via a recording spy
(no transformer, no GPU) — covering BOTH the runtime `_raw_predict` path (the
original 50-sample trace) and the OOF/training `_fit_predict` path (a different
call site AND a different data shape), so the fix is proven to generalise across
TabPFN predict sites rather than patching the one that happened to crash.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
from sklearn.decomposition import PCA

from zotero_summarizer.services.model import classifier
from zotero_summarizer.services.model.classifier_artifact import TrainedClassifier
from zotero_summarizer.services.model.classifier_const import EMBEDDING_DIM, FEATURE_DIM


class _DeviceSpyRegressor:
    """Stand-in for ``tabpfn.TabPFNRegressor`` that records the requested device
    and returns a deterministic per-row score — no real model, no GPU."""

    devices: list[str | None] = []

    def __init__(self, **kwargs):
        _DeviceSpyRegressor.devices.append(kwargs.get("device"))

    def fit(self, X, y):  # noqa: D401 - mimics the sklearn API
        return self

    def predict(self, X):
        return np.full(len(X), 3.0, dtype=np.float64)


def _toy_matrix(n_rows: int, *, seed: int) -> np.ndarray:
    """Random FEATURE_DIM-wide feature matrix (embedding block + tabular extras)."""
    rng = np.random.default_rng(seed)
    return rng.standard_normal((n_rows, FEATURE_DIM)).astype(np.float32)


def test_raw_predict_pins_tabpfn_to_cpu():
    """Original failing trace: runtime gate scoring (_raw_predict) on ~50 items."""
    _DeviceSpyRegressor.devices = []
    n_train, n_new = 30, 50  # n_new = 50 mirrors the OOM trace
    X_train = _toy_matrix(n_train, seed=1)
    y_train = np.random.default_rng(2).uniform(1.0, 5.0, size=n_train)
    pca = PCA(n_components=10, random_state=42).fit(X_train[:, :EMBEDDING_DIM])
    artifact = TrainedClassifier(
        classifier_name="tabpfn",
        golden_csv_sha256="x",
        feature_dim=FEATURE_DIM,
        pca_dim=10,
        X_train=X_train,
        y_train=y_train,
        pca_object=pca,
    )
    X_new = _toy_matrix(n_new, seed=3)

    with patch("tabpfn.TabPFNRegressor", _DeviceSpyRegressor):
        out = artifact._raw_predict(X_new)

    assert out.shape == (n_new,)
    assert _DeviceSpyRegressor.devices == ["cpu"], (
        "runtime TabPFN scoring must run on CPU, never the contended GPU pool"
    )


def test_fit_predict_oof_path_pins_tabpfn_to_cpu():
    """Different call site + data shape than the trace: the OOF/training fold
    predict (classifier._fit_predict -> _fit_tabpfn) must also pin CPU."""
    _DeviceSpyRegressor.devices = []
    n_train, n_val = 40, 15  # deliberately unlike the 30/50 runtime shapes
    X_train = _toy_matrix(n_train, seed=4)
    X_val = _toy_matrix(n_val, seed=5)
    y_train = np.random.default_rng(6).uniform(1.0, 5.0, size=n_train)

    with patch("tabpfn.TabPFNRegressor", _DeviceSpyRegressor):
        p_train, p_val = classifier._fit_predict(
            "tabpfn", X_train, y_train, X_val,
            objective="regression", pca_dim=10, return_train_probs=True,
        )

    assert p_val.shape == (n_val,)
    assert p_train.shape == (n_train,)
    # The regressor is built once (then predicts val + train); every build is CPU.
    assert _DeviceSpyRegressor.devices == ["cpu"]
