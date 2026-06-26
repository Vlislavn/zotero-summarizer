"""Plateau-based selection of the top-K candidates from a scored batch.

The user wants the Inbox to contain only the *plateau* of high-relevance papers,
not a fixed top-K. We use kneedle (Satopaa et al. 2011, "Finding a 'kneedle' in
a haystack") to find the elbow of the descending composite-score curve, then
apply a safety cap of min(hard_max, max(hard_min, ceil(target_fraction * N))).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Iterable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SelectionResult:
    """Outcome of plateau selection.

    The `selected` list is the sorted prefix to land in the Inbox; `rejected`
    is the long tail. `reason` captures *why* the cutoff fell where it did so
    the CLI can log "elbow at index 8" / "cap overrode elbow".
    """

    selected: list[Any]
    rejected: list[Any]
    knee_index: int | None
    cutoff: int
    reason: str
    safety_cap: int
    score_curve: list[float] = field(default_factory=list)


def plateau_select(
    candidates: Iterable[Any],
    target_fraction: float = 0.05,
    hard_min: int = 10,
    hard_max: int = 15,
    score_attr: str = "composite_score",
    kneedle_sensitivity: float = 1.0,
) -> SelectionResult:
    """Select the plateau of best candidates by descending score.

    Args:
        candidates: any iterable of objects with a `composite_score` attribute
            (or override via `score_attr`). Inputs are sorted internally.
        target_fraction: when the elbow is unhelpful or absent, fall back to a
            cap of ceil(target_fraction * N).
        hard_min: minimum slate size (so a tiny batch still has SOMETHING).
        hard_max: absolute upper bound regardless of distribution.
        score_attr: name of the score attribute on each candidate.
        kneedle_sensitivity: kneed `S` parameter; higher = stricter elbow.

    Returns:
        SelectionResult with selected/rejected lists and an audit trail.

    Notes:
        - We only invoke kneed when N >= 5. With fewer points the elbow detector
          is unreliable; for very small batches we fall back to the safety cap.
        - The hard_max is the user-stated "~15 papers per run" upper bound.
    """
    items_sorted = sorted(
        candidates,
        key=lambda c: float(getattr(c, score_attr, 0.0)),
        reverse=True,
    )
    total = len(items_sorted)
    score_curve = [float(getattr(c, score_attr, 0.0)) for c in items_sorted]

    if total == 0:
        return SelectionResult(
            selected=[],
            rejected=[],
            knee_index=None,
            cutoff=0,
            reason="empty_batch",
            safety_cap=0,
            score_curve=score_curve,
        )

    # Safety-cap math: target_fraction*N clipped to [hard_min, hard_max] and
    # never exceeding the batch size.
    target_count = max(hard_min, math.ceil(target_fraction * total))
    safety_cap = min(hard_max, target_count, total)

    knee_index: int | None = None
    if total >= 5:
        try:
            from kneed import KneeLocator

            kl = KneeLocator(
                x=list(range(total)),
                y=score_curve,
                curve="convex",
                direction="decreasing",
                S=kneedle_sensitivity,
            )
            if kl.elbow is not None:
                knee_index = int(kl.elbow)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("plateau_select: kneed failed (%s); falling back to cap", exc)
            knee_index = None

    if knee_index is None:
        cutoff = safety_cap
        reason = "flat_distribution_fallback_to_cap" if total >= 5 else "tiny_batch_fallback_to_cap"
    else:
        # +1 because knee_index is the elbow point itself (still in plateau).
        elbow_cutoff = max(hard_min, min(knee_index + 1, total))
        if elbow_cutoff > safety_cap:
            cutoff = safety_cap
            reason = "cap_overrode_elbow"
        else:
            cutoff = elbow_cutoff
            reason = "elbow"

    selected = items_sorted[:cutoff]
    rejected = items_sorted[cutoff:]

    logger.info(
        "plateau_select: total=%d cutoff=%d reason=%s knee=%s safety_cap=%d",
        total,
        cutoff,
        reason,
        knee_index,
        safety_cap,
    )

    return SelectionResult(
        selected=selected,
        rejected=rejected,
        knee_index=knee_index,
        cutoff=cutoff,
        reason=reason,
        safety_cap=safety_cap,
        score_curve=score_curve,
    )
