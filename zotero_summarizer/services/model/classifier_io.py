"""Classifier io functions (split from classifier.py)."""
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
    thresholds: dict[str, float],
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
    rho = thresholds.get("oof_spearman", 0.0)
    n_train = int(thresholds.get("n_train", 0))
    lines.append(f"OOF Spearman ρ on training set (n={n_train}): {rho:.3f}")
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
    import csv

    if not classifier_name or "/" in classifier_name or " " in classifier_name:
        raise ValueError(
            f"invalid classifier_name {classifier_name!r}; must be a short slug like "
            "'logreg' / 'tabpfn' / 'lightgbm' / 'llm_kather'."
        )

    rows: list[dict[str, str]] = []
    with input_csv.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

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

    tmp = input_csv.with_suffix(input_csv.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    tmp.replace(input_csv)
    return updated


def compute_metrics_against_gold(
    input_csv: Path,
    *,
    strength_filter: set[str] | None = None,
    split: str | None = None,
    priority_column: str = "cls_priority",
    split_column: str | None = None,
) -> dict[str, Any]:
    """Read predictions vs ``gold_priority_final`` and compute P/R/F1.

    ``priority_column`` selects which prediction column to score (e.g.
    ``cls_tabpfn_priority``, ``cls_llm_kather_priority``).

    ``split`` ∈ {"cv", "holdout", None}. When set, filters by
    ``split_column`` (defaults to ``cls_{name}_split`` derived from
    ``priority_column`` when not provided). LLM-classifier columns don't have
    a split — pass ``split=None``.
    """
    import csv

    from zotero_summarizer.services.model import golden_metrics as gm

    if split is not None and split_column is None:
        # Auto-derive split column name from the priority column. Strips the
        # trailing "_priority" and appends "_split".
        if priority_column.endswith("_priority"):
            split_column = priority_column[: -len("_priority")] + "_split"
        else:
            split_column = "cls_split"

    gold: list[str] = []
    pred: list[str] = []
    with input_csv.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if strength_filter:
                if (row.get("gold_signal_strength") or "").strip() not in strength_filter:
                    continue
            if split is not None and split_column:
                if (row.get(split_column) or "").strip() != split:
                    continue
            g = (row.get("gold_priority_final") or "").strip()
            p = (row.get(priority_column) or "").strip()
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
