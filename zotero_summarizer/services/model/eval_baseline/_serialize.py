"""JSON serialization for BaselineReport / LearningCurveReport."""

from __future__ import annotations

from typing import Any

from zotero_summarizer.services.model.eval_baseline._runners import (
    BaselineReport,
    LearningCurveReport,
    MetricCI,
)


def _ci_dict(ci: MetricCI) -> dict[str, float | int]:
    return {
        "point": round(ci.point, 6),
        "ci_low": round(ci.ci_low, 6),
        "ci_high": round(ci.ci_high, 6),
        "n_bootstrap": ci.n_bootstrap,
    }


def report_to_dict(report: BaselineReport) -> dict[str, Any]:
    return {
        "type": "baseline_report",
        "classifier": report.classifier_name,
        "n_rows_total": report.n_rows_total,
        "n_features": report.n_features,
        "n_repeats": report.n_repeats,
        "n_folds": report.n_folds,
        "n_bootstrap": report.n_bootstrap,
        "seed": report.seed,
        "elapsed_seconds": round(report.elapsed_seconds, 2),
        "metrics": {
            "spearman_rho": _ci_dict(report.spearman_rho),
            "kendall_tau": _ci_dict(report.kendall_tau),
            "auc": _ci_dict(report.auc),
            "ndcg_at_10": _ci_dict(report.ndcg_at_10),
            "mae": _ci_dict(report.mae),
            "cohen_kappa": _ci_dict(report.cohen_kappa),
        },
        "folds": [
            {
                "spearman_rho": round(fm.spearman_rho, 6),
                "kendall_tau": round(fm.kendall_tau, 6),
                "auc": round(fm.auc, 6),
                "ndcg_at_10": round(fm.ndcg_at_10, 6),
                "mae": round(fm.mae, 6),
                "cohen_kappa": round(fm.cohen_kappa, 6),
                "n_rows": fm.n_rows,
                "n_positive": fm.n_positive,
            }
            for fm in report.folds
        ],
        "metadata": report.metadata,
    }


def learning_curve_to_dict(report: LearningCurveReport) -> dict[str, Any]:
    return {
        "type": "learning_curve_report",
        "n_repeats": report.n_repeats,
        "n_folds": report.n_folds,
        "n_bootstrap": report.n_bootstrap,
        "seed": report.seed,
        "elapsed_seconds": round(report.elapsed_seconds, 2),
        "points": [
            {
                "n_train": p.n_train,
                "fraction": p.fraction,
                "spearman_rho": _ci_dict(p.spearman),
                "ndcg_at_10": _ci_dict(p.ndcg_at_10),
            }
            for p in report.points
        ],
        "metadata": report.metadata,
    }
