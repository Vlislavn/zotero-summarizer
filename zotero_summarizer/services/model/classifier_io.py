"""Classifier io functions (split from classifier.py)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from zotero_summarizer.services.model.classifier_const import ClassifierReport, FeedPrediction
from zotero_summarizer.services.golden.csv_store import edit_csv


def write_feed_predictions_csv(
    predictions: list[FeedPrediction],
    path: Path,
) -> None:
    """Write predictions to CSV with an empty ``your_label`` column for review."""
    import csv as _csv
    from dataclasses import asdict as _asdict

    if not predictions:
        path.write_text("")
        return
    fieldnames = list(_asdict(predictions[0]).keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = _csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for p in predictions:
            writer.writerow(_asdict(p))


def format_feed_predictions_markdown(
    predictions: list[FeedPrediction],
    thresholds: dict[str, float | None],
) -> str:
    """Compact human-readable summary suitable for terminal review.

    Sprint-1 (May 2026): the `thresholds` dict now carries `oof_spearman`
    and `n_train` (regression diagnostics) instead of the old binary
    keep/must/could thresholds + AUC. We surface the bucketing thresholds
    from :mod:`domain` instead — they're constants now, not learned.
    """
    from zotero_summarizer.domain import (
        PRIORITY_COULD_READ_THRESHOLD,
        PRIORITY_MUST_READ_THRESHOLD,
        PRIORITY_SHOULD_READ_THRESHOLD,
    )

    lines = []
    lines.append(
        f"score → priority: must≥{PRIORITY_MUST_READ_THRESHOLD} · "
        f"should≥{PRIORITY_SHOULD_READ_THRESHOLD} · could≥{PRIORITY_COULD_READ_THRESHOLD}"
    )
    rho = thresholds.get("oof_spearman")
    rho_label = "n/a" if rho is None else f"{rho:.3f}"
    n_train = int(thresholds.get("n_train", 0))
    lines.append(f"OOF Spearman ρ on training set (n={n_train}): {rho_label}")
    lines.append("")
    lines.append("| # | priority | score (1-5) | title (~80 chars) | venue | authors (1st) |")
    lines.append("|---|---|---|---|---|---|")
    for i, p in enumerate(predictions, start=1):
        title = p.title[:80].replace("|", "\\|")
        first_author = p.authors.split(";")[0].strip()[:30].replace("|", "\\|")
        venue = p.venue[:25].replace("|", "\\|")
        lines.append(
            f"| {i} | **{p.predicted_priority}** | {p.raw_score:.2f} "
            f"| {title} | {venue} | {first_author} |"
        )
    return "\n".join(lines)


def write_predictions_to_csv(
    input_csv: Path,
    report: ClassifierReport,
    *,
    classifier_name: str,
) -> int:
    """Write predictions back into the golden CSV under per-classifier columns.

    Columns ``cls_{name}_score``, ``cls_{name}_priority``, ``cls_{name}_split``
    are created on first use and rewritten on subsequent runs of the SAME
    classifier. Running a different classifier never touches another's
    columns — every run is preserved (FAIR ``Reusable``).

    Rows that didn't get a prediction (skipped during CV) get blank values.
    Returns the number of updated rows.
    """
    if not classifier_name or "/" in classifier_name or " " in classifier_name:
        raise ValueError(
            f"invalid classifier_name {classifier_name!r}; must be a short slug like "
            "'logreg' / 'tabpfn' / 'lightgbm' / 'llm_custom'."
        )

    with edit_csv(input_csv) as (fieldnames, rows):
        score_col = f"cls_{classifier_name}_score"
        priority_col = f"cls_{classifier_name}_priority"
        split_col = f"cls_{classifier_name}_split"
        for col in (score_col, priority_col, split_col):
            if col not in fieldnames:
                fieldnames.append(col)

        cv_by_key = {
            key: (p, pri) for key, p, pri in zip(
                report.item_keys, report.cv_probabilities, report.cv_predictions,
            )
        }
        ho_by_key = {
            key: (p, pri) for key, p, pri in zip(
                report.holdout_item_keys, report.holdout_probabilities, report.holdout_predictions,
            )
        }
        updated = 0
        for row in rows:
            key = row.get("item_key", "")
            if key in cv_by_key:
                p, pri = cv_by_key[key]
                row[score_col] = f"{p:.4f}"
                row[priority_col] = pri
                row[split_col] = "cv"
                updated += 1
            elif key in ho_by_key:
                p, pri = ho_by_key[key]
                row[score_col] = f"{p:.4f}"
                row[priority_col] = pri
                row[split_col] = "holdout"
                updated += 1
            else:
                row.setdefault(score_col, "")
                row.setdefault(priority_col, "")
                row.setdefault(split_col, "")
    return updated


def compute_metrics_against_gold(
    rows: list[dict[str, Any]],
    predictions: dict[str, str],
    *,
    strength_filter: set[str] | None = None,
) -> dict[str, Any]:
    """Score this run's keyed predictions against its effective input snapshot.

    The caller selects CV/holdout keys or current LLM results. No reread of CSV
    labels, later verdicts, or predictions retained from an earlier run.
    """
    from zotero_summarizer.services.model import golden_metrics as gm

    gold: list[str] = []
    pred: list[str] = []
    for row in rows:
        if strength_filter and (row.get("gold_signal_strength") or "").strip() not in strength_filter:
            continue
        g = (row.get("gold_priority_final") or "").strip()
        p = predictions.get((row.get("item_key") or "").strip(), "").strip()
        if not g or not p:
            continue
        gold.append(g)
        pred.append(p)

    return {
        "total": len(gold),
        "accuracy": round(gm.accuracy(gold, pred), 4),
        "per_class": {k: v.as_dict() for k, v in gm.compute_per_class(gold, pred).items()},
        "binary": gm.compute_binary(gold, pred).as_dict(),
        "confusion": gm.compute_confusion(gold, pred),
    }


__all__ = [
    "write_feed_predictions_csv",
    "format_feed_predictions_markdown",
    "write_predictions_to_csv",
    "compute_metrics_against_gold",
]
