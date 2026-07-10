"""Label-drift visibility for the continuous-training gate (Part 3).

The golden CSV is re-exported from live Zotero state each train (`goldenset export`),
so labels shift silently as verdicts land — a new 🧠 here, a dont_read there. With a
small temporal holdout (n≈111) that drift can swing the forward ρ a lot, and the
operator gets no signal that the *data* changed (only that the *number* did).

This surfaces the drift at train time: the label-class distribution of THIS run vs
the n_train of the PRIOR run (read from the run log). Logged + returned for the run
log so it's visible on the ModelCard, not buried.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from zotero_summarizer.domain import ReadingPriority
from zotero_summarizer.services import run_log

LOGGER = logging.getLogger(__name__)

# The four bands in priority order (must_read first — the scarce, load-bearing one).
_LABEL_ORDER = (
    ReadingPriority.MUST_READ.value,
    ReadingPriority.SHOULD_READ.value,
    ReadingPriority.COULD_READ.value,
    ReadingPriority.DONT_READ.value,
)


def label_distribution(gold_labels: list[str]) -> dict[str, int]:
    """Count the gold label classes (in canonical band order; unknown labels last)."""
    counts: dict[str, int] = {label: 0 for label in _LABEL_ORDER}
    for label in gold_labels:
        label = (label or "").strip()
        counts[label] = counts.get(label, 0) + 1
    return counts


def _prior_n_train(runs_log_path: Path | None, classifier_name: str) -> int | None:
    """The n_train of the previous run for this classifier, or None (first run)."""
    if runs_log_path is None or not runs_log_path.exists():
        return None
    runs = run_log.load_runs(runs_log_path)
    latest = run_log.latest_per_classifier(runs).get(classifier_name)
    if not latest:
        return None
    cv = latest.get("cv") or {}
    n = cv.get("n_rows")
    return int(n) if isinstance(n, (int, float)) else None


def log_label_drift(
    gold_labels: list[str],
    n_train: int,
    *,
    classifier_name: str,
    runs_log_path: Path | None,
) -> dict[str, Any]:
    """Compute + log the label distribution and the n_train delta vs the prior run.

    Pure read of the run log (no write — the caller appends the run entry). Returns a
    ``label_drift`` dict for the run-log entry: ``{distribution, n_train, n_train_delta,
    prior_n_train}``. The delta is ``None`` on the first run (no prior).
    """
    dist = label_distribution(gold_labels)
    prior = _prior_n_train(runs_log_path, classifier_name)
    delta = None if prior is None else n_train - prior
    LOGGER.info(
        "label drift: n_train=%d (Δ=%s) must=%d should=%d could=%d dont=%d",
        n_train,
        "first-run" if delta is None else f"{delta:+d}",
        dist[ReadingPriority.MUST_READ.value],
        dist[ReadingPriority.SHOULD_READ.value],
        dist[ReadingPriority.COULD_READ.value],
        dist[ReadingPriority.DONT_READ.value],
    )
    return {
        "distribution": dist,
        "n_train": n_train,
        "prior_n_train": prior,
        "n_train_delta": delta,
    }


__all__ = ["label_distribution", "log_label_drift"]
