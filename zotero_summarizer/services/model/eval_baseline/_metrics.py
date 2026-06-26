"""Per-fold metric computation for eval_baseline.

Defines the metric set used to characterise the model's ordering quality:
Spearman ρ, Kendall τ, AUC, NDCG@10, MAE, Cohen's κ (4-class). The continuous
target is ``gold_inferred_relevance`` ∈ [1, 5]; the binary target is
``must/should`` vs everything else; the priority is the 4-class label.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from zotero_summarizer import domain


# Bin edges (inferred-relevance → priority class) come from the single source
# in ``domain`` so eval mirrors exactly how labels are derived and predicted.
PRIORITY_BIN_EDGES = (
    domain.PRIORITY_COULD_READ_THRESHOLD,
    domain.PRIORITY_SHOULD_READ_THRESHOLD,
    domain.PRIORITY_MUST_READ_THRESHOLD,
)
PRIORITY_NAMES = ("dont_read", "could_read", "should_read", "must_read")


def priority_from_continuous(score: float) -> str:
    """Map a continuous [1, 5] score onto the 4-class priority."""
    return domain.score_to_priority(score)


@dataclass
class FoldMetrics:
    """Metrics from a single CV fold."""

    spearman_rho: float
    kendall_tau: float
    auc: float
    ndcg_at_10: float
    mae: float
    cohen_kappa: float
    n_rows: int
    n_positive: int


def compute_fold_metrics(
    y_true_continuous: np.ndarray,
    y_true_binary: np.ndarray,
    y_true_priority: list[str],
    proba: np.ndarray,
) -> FoldMetrics:
    """Compute every metric for one fold's held-out predictions."""
    from scipy.stats import kendalltau, spearmanr
    from sklearn.metrics import (
        cohen_kappa_score,
        mean_absolute_error,
        ndcg_score,
        roc_auc_score,
    )

    if len(set(y_true_binary)) < 2:
        raise ValueError(
            "fold has single-class y_true_binary — caller must skip this fold "
            "(metrics like AUC are undefined)"
        )
    if len(set(y_true_continuous.tolist())) < 2:
        raise ValueError(
            "fold has constant y_true_continuous — Spearman/Kendall are undefined"
        )

    rho_res = spearmanr(proba, y_true_continuous)
    rho = float(rho_res.statistic) if hasattr(rho_res, "statistic") else float(rho_res[0])
    tau_res = kendalltau(proba, y_true_continuous)
    tau = float(tau_res.statistic) if hasattr(tau_res, "statistic") else float(tau_res[0])
    auc = float(roc_auc_score(y_true_binary, proba))

    k = min(10, len(proba))
    ndcg = float(
        ndcg_score(
            y_true_continuous.reshape(1, -1),
            proba.reshape(1, -1),
            k=k,
        )
    )

    proba_rescaled = 1.0 + 4.0 * np.clip(proba, 0.0, 1.0)
    mae = float(mean_absolute_error(y_true_continuous, proba_rescaled))
    pred_priority = [priority_from_continuous(float(s)) for s in proba_rescaled]
    kappa = float(
        cohen_kappa_score(y_true_priority, pred_priority, labels=list(PRIORITY_NAMES))
    )

    return FoldMetrics(
        spearman_rho=rho,
        kendall_tau=tau,
        auc=auc,
        ndcg_at_10=ndcg,
        mae=mae,
        cohen_kappa=kappa,
        n_rows=len(y_true_continuous),
        n_positive=int(np.sum(y_true_binary)),
    )
