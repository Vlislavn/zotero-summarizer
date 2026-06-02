"""5×5 repeated stratified K-fold CV with BCa bootstrap CIs (run_baseline)
and stratified subsample learning curve (run_learning_curve)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from zotero_summarizer.services.model import classifier
from zotero_summarizer.services.model.eval_baseline._bootstrap import bca_ci
from zotero_summarizer.services.model.eval_baseline._featurize import (
    FeaturizedGolden,
    featurize_golden,
)
from zotero_summarizer.services.model.eval_baseline._metrics import (
    FoldMetrics,
    compute_fold_metrics,
)


LOGGER = logging.getLogger(__name__)


DEFAULT_N_REPEATS = 5
DEFAULT_N_FOLDS = 5
DEFAULT_BOOTSTRAP = 2000
DEFAULT_BOOTSTRAP_SEED = 42
DEFAULT_LEARNING_CURVE_FRACTIONS = (0.15, 0.30, 0.60, 0.85, 1.00)


@dataclass
class MetricCI:
    point: float
    ci_low: float
    ci_high: float
    n_bootstrap: int


@dataclass
class BaselineReport:
    n_repeats: int
    n_folds: int
    n_bootstrap: int
    n_rows_total: int
    n_features: int
    classifier_name: str
    folds: list[FoldMetrics]
    spearman_rho: MetricCI
    kendall_tau: MetricCI
    auc: MetricCI
    ndcg_at_10: MetricCI
    mae: MetricCI
    cohen_kappa: MetricCI
    elapsed_seconds: float
    seed: int = DEFAULT_BOOTSTRAP_SEED
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LearningCurvePoint:
    n_train: int
    fraction: float
    spearman: MetricCI
    ndcg_at_10: MetricCI


@dataclass
class LearningCurveReport:
    points: list[LearningCurvePoint]
    n_repeats: int
    n_folds: int
    n_bootstrap: int
    seed: int
    elapsed_seconds: float
    metadata: dict[str, Any] = field(default_factory=dict)


def _is_degenerate_val(feat: FeaturizedGolden, val_idx: np.ndarray) -> bool:
    """A fold whose val slice has a single class is unusable for AUC/Spearman."""
    return len(set(feat.y_binary[val_idx])) < 2


def _recompute_library_columns(
    feat: FeaturizedGolden, train_idx: np.ndarray,
) -> np.ndarray:
    """Return a copy of ``feat.X`` whose 5 P-set library columns are rebuilt
    against a positive set drawn ONLY from the train fold (leave-one-out per
    row). A single up-front featurization computes those columns against ALL
    rows — so val rows leak into their own features; rebuilding per fold against
    train-only P makes the fold metrics reflect serve time. P is built from the
    embeddings already in ``X`` (no DB re-read)."""
    from zotero_summarizer.services.model.library_features import (
        compute_library_features,
        positive_library_from_embeddings,
    )

    rows = feat.selected_rows or []
    emb = classifier.EMBEDDING_DIM
    lib = positive_library_from_embeddings(
        [rows[i] for i in train_idx],
        [feat.item_keys[i] for i in train_idx],
        feat.X[train_idx, :emb],
    )
    lo = emb + 7  # P-set features occupy extra-indices 7..11
    X = feat.X.copy()
    for i in range(X.shape[0]):
        authors_i = (rows[i].get("authors") or "").strip()
        X[i, lo:lo + 5] = compute_library_features(
            feat.X[i, :emb], lib,
            candidate_authors=authors_i, exclude_item_key=feat.item_keys[i],
        )
    return X


def _one_fold_metrics(
    feat: FeaturizedGolden,
    train_idx: np.ndarray,
    val_idx: np.ndarray,
    *,
    classifier_name: str,
    pca_dim: int,
    X_override: np.ndarray | None = None,
) -> FoldMetrics:
    """Run one CV fold (regression path).

    Sprint-1: regression directly on `gold_inferred_relevance`. No
    calibrator, no quantile bins. Output is clipped to [1, 5] and a
    pseudo-probability is derived for `compute_fold_metrics` (which still
    needs a [0, 1] vector for AUC) by `(score - 1) / 4`.
    Sprint-3b/c: if `tune.load_tuned_params` returns non-empty overrides
    (i.e. `optuna-best-params.json` exists), they're applied inside the
    fold for both the PCA dim and the LightGBM hyperparameters. Missing
    file ⇒ Sprint-1/2 defaults.
    """
    if _is_degenerate_val(feat, val_idx):
        raise ValueError(
            "_one_fold_metrics called on a degenerate val slice — "
            "caller must filter via _is_degenerate_val first"
        )
    from zotero_summarizer.services.model.tune import load_tuned_params

    tuned_params, tuned_pca = load_tuned_params()
    X = feat.X if X_override is None else X_override
    X_tr, y_tr = X[train_idx], feat.y_continuous[train_idx]
    X_val = X[val_idx]
    sw = feat.sample_weights[train_idx] if feat.sample_weights is not None else None
    _, p_val_raw = classifier._fit_predict(
        classifier_name, X_tr, y_tr, X_val,
        pca_dim=pca_dim, return_train_probs=False,
        objective="regression",
        pca_specter_dim=tuned_pca,
        lgbm_params=tuned_params or None,
        sample_weight=sw,
    )
    p_val = np.clip(np.asarray(p_val_raw, dtype=np.float64), 1.0, 5.0)
    pseudo_proba = (p_val - 1.0) / 4.0
    return compute_fold_metrics(
        y_true_continuous=feat.y_continuous[val_idx],
        y_true_binary=feat.y_binary[val_idx],
        y_true_priority=[feat.y_priority[i] for i in val_idx],
        proba=pseudo_proba,
    )


def run_baseline(
    rows: list[dict[str, str]],
    *,
    corpus_db_path: Path,
    goals_config: Any,
    classifier_name: str = "lightgbm",
    n_repeats: int = DEFAULT_N_REPEATS,
    n_folds: int = DEFAULT_N_FOLDS,
    n_bootstrap: int = DEFAULT_BOOTSTRAP,
    pca_dim: int = 100,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    progress_cb: Callable[[int, int], None] | None = None,
) -> BaselineReport:
    """Run n_repeats × n_folds stratified CV, return aggregated metrics with BCa CIs."""
    from sklearn.model_selection import StratifiedGroupKFold

    from zotero_summarizer.domain import paper_group_id

    t0 = time.time()
    feat = featurize_golden(
        rows,
        corpus_db_path=corpus_db_path,
        goals_config=goals_config,
        progress_cb=progress_cb,
    )
    # Group a paper's twin rows (feed:* + Zotero-key) so they never split across
    # train/val — random K-fold would leak via their near-identical embeddings.
    groups = np.array([paper_group_id(r) for r in (feat.selected_rows or [])])

    fold_results: list[FoldMetrics] = []
    for repeat_i in range(n_repeats):
        skf = StratifiedGroupKFold(n_splits=n_folds, shuffle=True, random_state=seed + repeat_i)
        for fold_i, (train_idx, val_idx) in enumerate(skf.split(feat.X, feat.y_binary, groups)):
            if _is_degenerate_val(feat, val_idx):
                LOGGER.warning(
                    "baseline repeat=%d/%d fold=%d: degenerate (single-class val) — skipped",
                    repeat_i + 1, n_repeats, fold_i + 1,
                )
                continue
            X_fold = _recompute_library_columns(feat, train_idx)
            fm = _one_fold_metrics(
                feat, train_idx, val_idx,
                classifier_name=classifier_name, pca_dim=pca_dim, X_override=X_fold,
            )
            fold_results.append(fm)
            LOGGER.info(
                "baseline repeat=%d/%d fold=%d/%d  rho=%.3f auc=%.3f ndcg=%.3f kappa=%.3f",
                repeat_i + 1, n_repeats, fold_i + 1, n_folds,
                fm.spearman_rho, fm.auc, fm.ndcg_at_10, fm.cohen_kappa,
            )

    if len(fold_results) < n_folds:
        raise RuntimeError(
            f"too few non-degenerate folds ({len(fold_results)}); cannot bootstrap"
        )

    def _ci(attr: str) -> MetricCI:
        vals = np.array([getattr(fm, attr) for fm in fold_results], dtype=np.float64)
        point, low, high = bca_ci(vals, n_bootstrap=n_bootstrap, seed=seed)
        return MetricCI(point=point, ci_low=low, ci_high=high, n_bootstrap=n_bootstrap)

    return BaselineReport(
        n_repeats=n_repeats,
        n_folds=n_folds,
        n_bootstrap=n_bootstrap,
        n_rows_total=feat.X.shape[0],
        n_features=feat.n_features,
        classifier_name=classifier_name,
        folds=fold_results,
        spearman_rho=_ci("spearman_rho"),
        kendall_tau=_ci("kendall_tau"),
        auc=_ci("auc"),
        ndcg_at_10=_ci("ndcg_at_10"),
        mae=_ci("mae"),
        cohen_kappa=_ci("cohen_kappa"),
        elapsed_seconds=time.time() - t0,
        seed=seed,
        metadata={
            "method": "5x5 repeated stratified GROUP K-fold CV (grouped by paper id; per-fold leave-one-out P)",
            "metric_primary": "spearman_rho",
            "bootstrap_method": "BCa (Efron 1987)",
            "n_positive_total": int(np.sum(feat.y_binary)),
        },
    )


def _subset_featurized(feat: FeaturizedGolden, sub_idx: np.ndarray, sub_rows: list) -> FeaturizedGolden:
    """A ``FeaturizedGolden`` restricted to the rows at ``sub_idx``."""
    return FeaturizedGolden(
        X=feat.X[sub_idx],
        y_binary=feat.y_binary[sub_idx],
        y_continuous=feat.y_continuous[sub_idx],
        y_priority=[feat.y_priority[i] for i in sub_idx],
        item_keys=[feat.item_keys[i] for i in sub_idx],
        n_features=feat.n_features,
        selected_rows=sub_rows,
    )


def run_learning_curve(
    rows: list[dict[str, str]],
    *,
    corpus_db_path: Path,
    goals_config: Any,
    classifier_name: str = "lightgbm",
    fractions: tuple[float, ...] = DEFAULT_LEARNING_CURVE_FRACTIONS,
    n_repeats: int = 3,
    n_folds: int = 5,
    n_bootstrap: int = 500,
    pca_dim: int = 100,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    progress_cb: Callable[[int, int], None] | None = None,
) -> LearningCurveReport:
    """Sweep n_train (as a fraction of available data)."""
    from sklearn.model_selection import StratifiedGroupKFold

    from zotero_summarizer.domain import paper_group_id

    if any(f <= 0.0 or f > 1.0 for f in fractions):
        raise ValueError(f"learning-curve fractions must be in (0, 1]; got {fractions}")
    if list(fractions) != sorted(fractions):
        raise ValueError("learning-curve fractions must be ascending")

    t0 = time.time()
    feat = featurize_golden(
        rows,
        corpus_db_path=corpus_db_path,
        goals_config=goals_config,
        progress_cb=progress_cb,
    )

    rng = np.random.default_rng(seed)
    points: list[LearningCurvePoint] = []

    for frac in fractions:
        n_target = min(max(50, int(round(feat.X.shape[0] * frac))), feat.X.shape[0])
        rho_vals: list[float] = []
        ndcg_vals: list[float] = []
        for repeat_i in range(n_repeats):
            pos_idx = np.flatnonzero(feat.y_binary == 1)
            neg_idx = np.flatnonzero(feat.y_binary == 0)
            n_pos = max(1, int(round(len(pos_idx) * frac)))
            n_neg = max(1, min(n_target - n_pos, len(neg_idx)))
            sub_pos = rng.choice(pos_idx, size=n_pos, replace=False)
            sub_neg = rng.choice(neg_idx, size=n_neg, replace=False)
            sub_idx = np.concatenate([sub_pos, sub_neg])
            rng.shuffle(sub_idx)
            sub_rows = [feat.selected_rows[i] for i in sub_idx]
            sub_feat = _subset_featurized(feat, sub_idx, sub_rows)
            sub_groups = np.array([paper_group_id(r) for r in sub_rows])
            skf = StratifiedGroupKFold(
                n_splits=n_folds, shuffle=True, random_state=seed + repeat_i,
            )
            for train_idx, val_idx in skf.split(sub_feat.X, sub_feat.y_binary, sub_groups):
                if _is_degenerate_val(sub_feat, val_idx):
                    continue
                X_fold = _recompute_library_columns(sub_feat, train_idx)
                fm = _one_fold_metrics(
                    sub_feat, train_idx, val_idx,
                    classifier_name=classifier_name, pca_dim=pca_dim, X_override=X_fold,
                )
                rho_vals.append(fm.spearman_rho)
                ndcg_vals.append(fm.ndcg_at_10)

        if not rho_vals:
            raise RuntimeError(
                f"learning_curve frac={frac:.2f}: zero non-degenerate folds"
            )

        rho_p, rho_lo, rho_hi = bca_ci(
            np.asarray(rho_vals, dtype=np.float64),
            n_bootstrap=n_bootstrap, seed=seed,
        )
        ndcg_p, ndcg_lo, ndcg_hi = bca_ci(
            np.asarray(ndcg_vals, dtype=np.float64),
            n_bootstrap=n_bootstrap, seed=seed,
        )
        points.append(
            LearningCurvePoint(
                n_train=n_target,
                fraction=frac,
                spearman=MetricCI(point=rho_p, ci_low=rho_lo, ci_high=rho_hi, n_bootstrap=n_bootstrap),
                ndcg_at_10=MetricCI(point=ndcg_p, ci_low=ndcg_lo, ci_high=ndcg_hi, n_bootstrap=n_bootstrap),
            )
        )
        LOGGER.info(
            "learning_curve frac=%.2f n=%d  rho=%.3f [%.3f, %.3f]",
            frac, n_target, rho_p, rho_lo, rho_hi,
        )

    return LearningCurveReport(
        points=points,
        n_repeats=n_repeats,
        n_folds=n_folds,
        n_bootstrap=n_bootstrap,
        seed=seed,
        elapsed_seconds=time.time() - t0,
        metadata={
            "method": "stratified subsample × repeated K-fold CV with BCa bootstrap",
            "metric_primary": "spearman_rho",
            "fractions": list(fractions),
        },
    )
