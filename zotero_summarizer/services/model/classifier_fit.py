"""Classifier fit functions (split from classifier.py)."""
from __future__ import annotations

import hashlib  # noqa: F401
import json  # noqa: F401
import logging  # noqa: F401
import sqlite3  # noqa: F401
import time  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any, Callable  # noqa: F401

import numpy as np  # noqa: F401

from zotero_summarizer.services.model.classifier_const import *  # noqa: F401,F403


def predict_named(model: Any, X: np.ndarray, **kwargs: Any):
    """Call ``model.predict``, wrapping a numpy ``X`` in a DataFrame whose
    columns match the model's ``feature_names_in_``.

    LightGBM's sklearn wrapper sets ``feature_names_in_`` (``Column_0`` …) even
    when fit on an ndarray, so predicting with a bare ndarray trips sklearn's
    feature-name validation with a benign UserWarning. No-op for models without
    ``feature_names_in_`` (e.g. logreg / TabPFN fit on ndarray) and for inputs
    that are already framed.
    """
    fn = getattr(model, "feature_names_in_", None)
    if fn is not None and not hasattr(X, "columns"):
        import pandas as pd

        X = pd.DataFrame(X, columns=fn)
    return model.predict(X, **kwargs)


def _fit_predict(
    classifier_name: str,
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    *,
    pca_dim: int = 100,
    return_train_probs: bool = False,
    objective: str = "regression",
    pca_specter_dim: int | None = None,
    lgbm_params: dict[str, Any] | None = None,
    sample_weight: np.ndarray | None = None,
) -> tuple[np.ndarray | None, np.ndarray]:
    """Fit ``classifier_name`` and return ``(train_scores_or_None, val_scores)``.

    Sprint-1: default objective is ``regression`` — every model predicts
    the continuous relevance label in [1, 5]. Sprint-3b: when
    ``pca_specter_dim`` is set (not None), the 768-d SPECTER2 block is
    PCA-reduced to that many components inside the fold (TRAIN-fit, val-
    transform, no leakage), then concatenated with the tabular extras.
    Sprint-3c: ``lgbm_params`` lets Optuna pass hyperparameter overrides
    into the LightGBM constructor without touching this function's body.

    ``train_scores`` is used by callers that fit a downstream calibrator on
    training-set predictions; ``None`` for the held-out predict-only path.
    """
    if pca_specter_dim is not None:
        X_train, X_val = _reduce_for_tabpfn(
            X_train, X_val, pca_dim=pca_specter_dim,
        )

    if classifier_name == "logreg":
        if objective == "regression":
            from sklearn.linear_model import Ridge

            clf = Ridge(alpha=1.0, random_state=42)
            clf.fit(X_train, y_train, sample_weight=sample_weight)
            p_val = clf.predict(X_val)
            p_train = clf.predict(X_train) if return_train_probs else None
            return p_train, p_val
        from sklearn.linear_model import LogisticRegression

        clf = LogisticRegression(
            C=1.0,
            class_weight="balanced",
            max_iter=1000,
            solver="lbfgs",
        )
        clf.fit(X_train, y_train)
        p_val = clf.predict_proba(X_val)[:, 1]
        p_train = clf.predict_proba(X_train)[:, 1] if return_train_probs else None
        return p_train, p_val

    if classifier_name == "tabpfn":
        X_train_red, X_val_red = _reduce_for_tabpfn(X_train, X_val, pca_dim=pca_dim)
        if objective == "regression":
            from tabpfn import TabPFNRegressor

            reg = TabPFNRegressor(
                n_estimators=8,
                device="auto",
                ignore_pretraining_limits=False,
                random_state=42,
            )
            reg.fit(X_train_red, y_train)
            p_val = reg.predict(X_val_red)
            p_train = reg.predict(X_train_red) if return_train_probs else None
            return p_train, p_val
        from tabpfn import TabPFNClassifier

        clf = TabPFNClassifier(
            n_estimators=8,
            device="auto",
            ignore_pretraining_limits=False,
            random_state=42,
        )
        clf.fit(X_train_red, y_train)
        p_val = clf.predict_proba(X_val_red)[:, 1]
        p_train = clf.predict_proba(X_train_red)[:, 1] if return_train_probs else None
        return p_train, p_val

    if classifier_name == "lightgbm":
        import lightgbm as lgb

        if objective == "regression":
            defaults = {
                "objective": "regression",
                "n_estimators": 200,
                "num_leaves": 15,
                "max_depth": 4,
                "learning_rate": 0.05,
                "min_child_samples": 10,
                "reg_lambda": 1.0,
                "verbose": -1,
                "random_state": 42,
                "n_jobs": 1,
                "num_threads": 1,
            }
            if lgbm_params:
                defaults.update(lgbm_params)
            reg = lgb.LGBMRegressor(**defaults)
            reg.fit(X_train, y_train, sample_weight=sample_weight)
            p_val = predict_named(reg, X_val)
            p_train = predict_named(reg, X_train) if return_train_probs else None
            return p_train, p_val

        clf = lgb.LGBMClassifier(
            n_estimators=200,
            num_leaves=15,
            max_depth=4,
            learning_rate=0.05,
            min_child_samples=10,
            reg_lambda=1.0,
            class_weight="balanced",
            verbose=-1,
            random_state=42,
            n_jobs=1,
            num_threads=1,
        )
        clf.fit(X_train, y_train)
        p_val = clf.predict_proba(X_val)[:, 1]
        p_train = clf.predict_proba(X_train)[:, 1] if return_train_probs else None
        return p_train, p_val

    raise ValueError(
        f"unknown classifier_name {classifier_name!r}; "
        "use 'logreg', 'tabpfn', or 'lightgbm'"
    )


def _fit_calibrator(p_train: np.ndarray, y_train: np.ndarray, *, method: str = "isotonic"):
    """Fit a probability calibrator on training scores.

    * ``isotonic``: monotonic step-function, no parametric assumption.
    * ``sigmoid``: Platt scaling (logistic on raw scores).
    * ``none``: identity — return raw probabilities.
    """
    if method == "none":
        return None
    if method == "isotonic":
        from sklearn.isotonic import IsotonicRegression

        cal = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        cal.fit(p_train, y_train)
        return cal
    if method == "sigmoid":
        from sklearn.linear_model import LogisticRegression

        cal = LogisticRegression(solver="lbfgs", max_iter=1000)
        cal.fit(p_train.reshape(-1, 1), y_train)
        return cal
    raise ValueError(f"unknown calibration method {method!r}")


def _apply_calibrator(calibrator, p: np.ndarray) -> np.ndarray:
    if calibrator is None:
        return np.asarray(p, dtype=np.float64)
    # IsotonicRegression: 1-D in/out. LogisticRegression: needs 2-D.
    try:
        return calibrator.transform(p).astype(np.float64)
    except AttributeError:
        return calibrator.predict_proba(np.asarray(p).reshape(-1, 1))[:, 1].astype(np.float64)


def _find_optimal_threshold(
    y: np.ndarray,
    p: np.ndarray,
    *,
    strategy: str = "youden",
) -> float:
    """Pick the binary cutoff on calibrated probs.

    ``youden``: TPR(t) − FPR(t) maximised over all candidate thresholds (the
    classical operating-point choice when FP and FN cost equally).
    ``f1``: F1-score maximised — biased toward recall when positives are
    rare.
    """
    from sklearn.metrics import f1_score, roc_curve

    if len(set(y)) < 2:
        return 0.5
    if strategy == "youden":
        fpr, tpr, thresholds = roc_curve(y, p)
        j = tpr - fpr
        # Skip the first threshold (which is +inf in sklearn).
        best = int(np.argmax(j[1:])) + 1
        return float(thresholds[best])
    if strategy == "f1":
        # Sweep over unique predicted probabilities.
        cand = np.unique(np.concatenate([[0.0, 1.0], p]))
        scores = [f1_score(y, (p >= t).astype(int), zero_division=0) for t in cand]
        return float(cand[int(np.argmax(scores))])
    raise ValueError(f"unknown threshold strategy {strategy!r}")


def _adaptive_4class_cutoffs(p: np.ndarray, t_keep: float) -> tuple[float, float]:
    """Split keep/skip groups by quantile to derive must/could thresholds.

    Returns ``(must_threshold, could_threshold)`` such that:
      * ``p >= must_threshold``        → must_read   (top quarter of keep group)
      * ``t_keep <= p < must_threshold`` → should_read
      * ``could_threshold <= p < t_keep`` → could_read
      * ``p < could_threshold``        → dont_read   (bottom quarter of skip group)

    Uses the **75th percentile** of the keep group for ``must_threshold`` and
    the **25th percentile** of the skip group for ``could_threshold``. This
    avoids the degenerate "median = 0" case that made ``dont_read``
    unreachable when negatives clustered tightly near zero. Falls back to a
    small offset around ``t_keep`` if a group is empty or collapses.
    """
    keep_probs = p[p >= t_keep]
    skip_probs = p[p < t_keep]
    if len(keep_probs) >= 4:
        must_t = float(np.quantile(keep_probs, 0.75))
    elif len(keep_probs) >= 1:
        must_t = float(np.max(keep_probs))
    else:
        must_t = float(t_keep)
    if len(skip_probs) >= 4:
        could_t = float(np.quantile(skip_probs, 0.25))
    elif len(skip_probs) >= 1:
        could_t = float(np.min(skip_probs))
    else:
        could_t = float(t_keep)
    # Guard against the buckets collapsing into each other.
    must_t = max(must_t, t_keep)
    could_t = min(could_t, t_keep)
    # If the skip distribution is degenerate (everything at 0), pull could_t
    # off the floor so dont_read is actually reachable.
    if could_t <= 0.0 and t_keep > 0.0:
        could_t = t_keep / 4.0
    return must_t, could_t


def _prob_to_priority_adaptive(
    p: float,
    *,
    t_keep: float,
    t_must: float,
    t_could: float,
) -> str:
    """4-class label using calibrated probability + tuned cutoffs."""
    if p >= t_must:
        return "must_read"
    if p >= t_keep:
        return "should_read"
    if p >= t_could:
        return "could_read"
    return "dont_read"


def _reduce_for_tabpfn(
    X_train: np.ndarray,
    X_val: np.ndarray,
    *,
    pca_dim: int = 100,
) -> tuple[np.ndarray, np.ndarray]:
    """PCA-reduce the SPECTER2 part (first 768 dims). Tabular extras pass through.

    Originally added for TabPFN's 500-feature ceiling; in Sprint-3b we
    apply the same reduction to LightGBM/Ridge to control overfitting on
    n≈500 training rows where 768 raw embedding dims dominate the 12
    tabular extras. PCA is fit on the TRAIN fold only (no test leakage).
    """
    from sklearn.decomposition import PCA

    emb_train = X_train[:, :EMBEDDING_DIM]
    emb_val = X_val[:, :EMBEDDING_DIM]
    extras_train = X_train[:, EMBEDDING_DIM:]
    extras_val = X_val[:, EMBEDDING_DIM:]
    actual_dim = min(pca_dim, emb_train.shape[0], emb_train.shape[1])
    pca = PCA(n_components=actual_dim, random_state=42)
    emb_train_red = pca.fit_transform(emb_train)
    emb_val_red = pca.transform(emb_val)
    return (
        np.concatenate([emb_train_red, extras_train], axis=1).astype(np.float32),
        np.concatenate([emb_val_red, extras_val], axis=1).astype(np.float32),
    )


__all__ = [
    "predict_named",
    "_fit_predict",
    "_fit_calibrator",
    "_apply_calibrator",
    "_find_optimal_threshold",
    "_adaptive_4class_cutoffs",
    "_prob_to_priority_adaptive",
    "_reduce_for_tabpfn",
]
