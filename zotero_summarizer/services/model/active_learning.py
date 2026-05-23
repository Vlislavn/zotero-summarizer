"""Active-learning suggestions — find border-case library rows whose
re-labelling would maximally improve the model.

Two reasons a row is "high information":
  1. **Border uncertainty** — the regression score is close to a priority
     threshold (4.5 / 3.6 / 2.6). One additional label there moves the
     decision boundary.
  2. **Disagreement** — the model's predicted priority differs from the
     currently-derived `gold_priority_final`. The user's "ground truth"
     for that row is implicitly weak (no clean engagement signal).

We rank by a single score: distance to the nearest threshold, smaller is
better. A side-channel boost is given to rows whose model prediction and
derived priority disagree.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zotero_summarizer.domain import (
    PRIORITY_COULD_READ_THRESHOLD,
    PRIORITY_MUST_READ_THRESHOLD,
    PRIORITY_SHOULD_READ_THRESHOLD,
)


LOGGER = logging.getLogger(__name__)


_THRESHOLDS = (
    PRIORITY_COULD_READ_THRESHOLD,
    PRIORITY_SHOULD_READ_THRESHOLD,
    PRIORITY_MUST_READ_THRESHOLD,
)


@dataclass(frozen=True)
class LabelSuggestion:
    item_key: str
    title: str
    authors: str
    venue: str
    abstract_preview: str
    predicted_score: float
    predicted_priority: str
    current_priority: str
    border_distance: float
    disagrees: bool


def _distance_to_nearest_threshold(score: float) -> float:
    """Return the absolute distance to the nearest priority boundary."""
    return float(min(abs(score - t) for t in _THRESHOLDS))


def suggest_border_labels(
    rows: list[dict[str, str]],
    *,
    corpus_db_path: Path,
    goals_config: Any,
    golden_csv: Path,
    classifier_name: str = "lightgbm",
    top_k: int = 20,
    abstract_preview_chars: int = 220,
) -> list[LabelSuggestion]:
    """Score every library row, return top-K closest to a class border.

    Filters: only library items (item_key NOT prefixed with `feed:` or
    `note:`). Trash items dropped. Rows without title+abstract dropped.

    Performance: re-uses the persisted gate model via
    :func:`classifier_persistence.load_or_train` instead of retraining on
    every call. The earlier ``predict_new_items`` path ran a full 5-fold
    CV + final fit + featurised all ~1.3k training rows on each request,
    which made the endpoint take >10 minutes (effectively a timeout). The
    cached model is loaded in milliseconds when ``golden_csv``'s sha is
    unchanged; only the ~K library items being scored are featurised.

    ``golden_csv`` is required (no implicit relative-path default) so the
    sha-based cache check in ``load_or_train`` always points at the real
    training file — a wrong path would silently force a slow retrain.
    """
    from zotero_summarizer.services.model import classifier_persistence as cp

    library_items: list[dict[str, str]] = []
    for r in rows:
        ik = (r.get("item_key") or "").strip()
        if not ik or ik.startswith(("feed:", "note:")):
            continue
        if str(r.get("in_trash", "")).strip().lower() in ("true", "1"):
            continue
        if not (r.get("title") or "").strip() or not (r.get("abstract") or "").strip():
            continue
        library_items.append(r)
    if not library_items:
        return []

    # Load the cached model (or train once if the golden CSV changed).
    # Predict — no retrain — only the library_items are featurised.
    LOGGER.info(
        "active_learning: scoring %d library rows with cached %s model",
        len(library_items), classifier_name,
    )
    trained = cp.load_or_train(
        golden_csv,
        classifier_name=classifier_name,
        corpus_db_path=corpus_db_path,
        goals_config=goals_config,
    )
    predictions = trained.predict(
        library_items,
        corpus_db_path=corpus_db_path,
        goals_config=goals_config,
    )

    by_key = {p.item_key: p for p in predictions}
    suggestions: list[LabelSuggestion] = []
    for it in library_items:
        key = (it.get("item_key") or "").strip()
        pred = by_key.get(key)
        if pred is None:
            continue
        current = (it.get("gold_priority_final") or "").strip() or "unknown"
        d = _distance_to_nearest_threshold(pred.raw_score)
        disagrees = pred.predicted_priority != current
        suggestions.append(LabelSuggestion(
            item_key=key,
            title=pred.title,
            authors=pred.authors,
            venue=pred.venue,
            abstract_preview=pred.abstract_preview,
            predicted_score=pred.raw_score,
            predicted_priority=pred.predicted_priority,
            current_priority=current,
            border_distance=d,
            disagrees=disagrees,
        ))

    # Rank: closest to threshold first; tie-break by disagreement (those go up).
    suggestions.sort(key=lambda s: (s.border_distance, 0 if s.disagrees else 1))
    return suggestions[:top_k]


def load_rows(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        raise FileNotFoundError(f"golden CSV missing: {csv_path}")
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def format_suggestions_markdown(suggestions: list[LabelSuggestion]) -> str:
    """Compact terminal-friendly summary."""
    lines = [
        "| # | item_key | score | predicted | current | Δborder | conflict | title (~70) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, s in enumerate(suggestions, start=1):
        title = (s.title or "")[:70].replace("|", "\\|")
        conflict = "‼" if s.disagrees else ""
        lines.append(
            f"| {i} | `{s.item_key}` | {s.predicted_score:.2f} | "
            f"**{s.predicted_priority}** | {s.current_priority} | "
            f"{s.border_distance:.2f} | {conflict} | {title} |"
        )
    return "\n".join(lines)
