"""Reliability metric computation: Cohen's κ + ICC(2,1) + Pearson + Spearman.

The Pearson r is the empirical upper bound any model's Spearman ρ can
achieve on this task (Hooker et al. 2019; McHugh 2012; Koo & Li 2016).

Heavy deps (numpy, scipy, sklearn) are imported lazily inside the
functions so a module-level ``from zotero_summarizer.services import
relabel_audit`` doesn't drag them in.
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.services.relabel_audit._constants import (
    AGE_BUCKET_NAMES,
    AUDIT_PRIORITY_NAMES,
    AuditMetrics,
    AuditResponse,
)


def _icc_2_1(scores_a: list[float], scores_b: list[float]) -> float:
    """ICC(2,1) — absolute agreement, single-rater, two-way random effects.

    Reference: Koo & Li 2016. The two timepoints are treated as two
    "raters" rating the same N subjects.
    """
    if len(scores_a) != len(scores_b):
        raise ValueError("scores_a and scores_b must be the same length")
    n = len(scores_a)
    if n < 2:
        raise ValueError("ICC needs n ≥ 2 paired observations")
    import numpy as np

    a = np.asarray(scores_a, dtype=np.float64)
    b = np.asarray(scores_b, dtype=np.float64)
    M = np.stack([a, b], axis=1)
    k = 2
    grand_mean = float(M.mean())
    row_means = M.mean(axis=1)
    col_means = M.mean(axis=0)
    ss_total = float(((M - grand_mean) ** 2).sum())
    ss_rows = float(k * ((row_means - grand_mean) ** 2).sum())
    ss_cols = float(n * ((col_means - grand_mean) ** 2).sum())
    ss_err = ss_total - ss_rows - ss_cols
    ms_rows = ss_rows / (n - 1)
    ms_cols = ss_cols / (k - 1)
    ms_err = ss_err / ((n - 1) * (k - 1))
    denom = ms_rows + (k - 1) * ms_err + k * (ms_cols - ms_err) / n
    if denom == 0.0:
        raise ValueError("ICC denominator is zero — all scores identical")
    return float((ms_rows - ms_err) / denom)


def compute_metrics(responses: list[AuditResponse]) -> AuditMetrics:
    """Cohen's κ + ICC(2,1) + Pearson r + Spearman ρ over the paired verdicts."""
    if not responses:
        raise ValueError("cannot compute metrics on zero responses")
    from scipy.stats import pearsonr, spearmanr
    from sklearn.metrics import cohen_kappa_score

    original_cat = [r.original_priority for r in responses]
    new_cat = [r.new_priority for r in responses]
    original_cont = [r.original_inferred_relevance for r in responses]
    new_cont = [r.new_relevance for r in responses]

    kappa = float(cohen_kappa_score(original_cat, new_cat, labels=list(AUDIT_PRIORITY_NAMES)))
    kappa_w = float(
        cohen_kappa_score(
            original_cat, new_cat,
            labels=list(AUDIT_PRIORITY_NAMES),
            weights="quadratic",
        )
    )
    icc = _icc_2_1(original_cont, new_cont)
    pearson_res = pearsonr(original_cont, new_cont)
    pearson = float(pearson_res.statistic) if hasattr(pearson_res, "statistic") else float(pearson_res[0])
    spearman_res = spearmanr(original_cont, new_cont)
    spearman = float(spearman_res.statistic) if hasattr(spearman_res, "statistic") else float(spearman_res[0])

    by_bucket: dict[str, float] = {}
    for bucket in AGE_BUCKET_NAMES:
        bucket_rs = [r for r in responses if r.age_bucket == bucket]
        if len(bucket_rs) < 5:
            continue
        b_orig = [r.original_priority for r in bucket_rs]
        b_new = [r.new_priority for r in bucket_rs]
        if len(set(b_orig)) < 2 or len(set(b_new)) < 2:
            continue
        by_bucket[bucket] = float(
            cohen_kappa_score(b_orig, b_new, labels=list(AUDIT_PRIORITY_NAMES))
        )

    by_class: dict[str, float] = {}
    for cls in AUDIT_PRIORITY_NAMES:
        in_class = [r for r in responses if r.original_priority == cls]
        if not in_class:
            continue
        by_class[cls] = sum(1 for r in in_class if r.new_priority == cls) / len(in_class)

    return AuditMetrics(
        n_paired=len(responses),
        cohen_kappa=kappa,
        cohen_kappa_weighted=kappa_w,
        icc_2_1=icc,
        pearson_r=pearson,
        spearman_rho=spearman,
        by_age_bucket=by_bucket,
        by_class=by_class,
        metadata={
            "interpretation_kappa": (
                "Landis-Koch 1977: 0.0-0.2 slight, 0.2-0.4 fair, "
                "0.4-0.6 moderate, 0.6-0.8 substantial, 0.8-1.0 near-perfect"
            ),
            "interpretation_icc": (
                "Koo-Li 2016: <0.5 poor, 0.5-0.75 moderate, "
                "0.75-0.9 good, >0.9 excellent"
            ),
            "interpretation_pearson": (
                "Pearson r is the empirical upper bound on Spearman ρ "
                "any model can achieve on this task (irreducible noise floor)."
            ),
        },
    )


def metrics_to_dict(metrics: AuditMetrics) -> dict[str, Any]:
    return {
        "type": "relabel_audit_metrics",
        "n_paired": metrics.n_paired,
        "cohen_kappa": round(metrics.cohen_kappa, 6),
        "cohen_kappa_weighted": round(metrics.cohen_kappa_weighted, 6),
        "icc_2_1": round(metrics.icc_2_1, 6),
        "pearson_r": round(metrics.pearson_r, 6),
        "spearman_rho": round(metrics.spearman_rho, 6),
        "by_age_bucket": {k: round(v, 6) for k, v in metrics.by_age_bucket.items()},
        "by_class": {k: round(v, 6) for k, v in metrics.by_class.items()},
        "metadata": metrics.metadata,
    }


__all__ = ["compute_metrics", "metrics_to_dict"]
