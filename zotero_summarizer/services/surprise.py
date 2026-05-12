"""Black-swan / serendipity scoring — surfaces unexpected papers in the inbox.

The user wants the Inbox to surface 1-2 papers per run that are *outside* their
stated interests but rigorous and novel enough to warrant attention. Plain
relevance ranking systematically misses these — by construction, a high-affinity
score correlates with "topics you already track."

Approach (per the SOTA plan, refined for unit balance):

    dim_quality = ((rigor + novelty) / 2) / 5    # normalize to [0, 1]
    surprise_score = max(0, dim_quality - corpus_affinity)  # affinity is in [-1, 1]

clipped to [0, 1]. High rigor + high novelty + LOW affinity = high surprise.
When affinity is high (paper is "like what you already read"), surprise collapses
to ~0 even if the LLM ranked the paper highly — that's intentional, because the
relevance pipeline will already surface it.
Then reserve N_surprise = floor(black_swan_fraction * inbox_size) slots in
the slate for the highest-surprise items NOT already in the relevance pick.

Reference: Maystre et al. 2025, Spotify Calibrated Recommendations
(arXiv:2509.05460); Kotkov et al. RecSys 2024 (serendipity).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

logger = logging.getLogger(__name__)

DEFAULT_BLACK_SWAN_FRACTION = 0.10
DEFAULT_BLACK_SWAN_MIN_SCORE = 0.30  # below this, even the best surprise isn't worth surfacing


class _Surpriseable(Protocol):
    """Anything that exposes LLM dimensions + corpus affinity."""

    methodological_rigor: float
    novelty_for_goals: float
    corpus_affinity: float


def compute_surprise_score(
    methodological_rigor: float,
    novelty_for_goals: float,
    corpus_affinity: float,
) -> float:
    """Surprise = max(0, mean_quality - corpus_affinity), clipped to [0, 1].

    The LLM dimensions (1-5) are normalized to [0, 1] before subtracting affinity
    so both terms are on the same scale. Affinity ranges [-1, 1] (cosine-style).

    Inputs:
        methodological_rigor: LLM 1-5 score.
        novelty_for_goals: LLM 1-5 score.
        corpus_affinity: float in [-1, 1] from the existing corpus matcher.
            Higher = more like the user's existing library.

    Output:
        Float in [0, 1]. ~0 when affinity is high. Near 1 when the LLM rated the
        paper highly AND it's far from the user's library.
    """
    try:
        rigor = float(methodological_rigor)
        novelty = float(novelty_for_goals)
        affinity = float(corpus_affinity)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(rigor) or math.isnan(novelty) or math.isnan(affinity):
        return 0.0

    # Clamp LLM scores to [0, 5] then normalize to [0, 1].
    rigor_n = min(5.0, max(0.0, rigor)) / 5.0
    novelty_n = min(5.0, max(0.0, novelty)) / 5.0
    dim_quality = (rigor_n + novelty_n) / 2.0
    raw = dim_quality - affinity
    if raw <= 0.0:
        return 0.0
    return min(1.0, raw)


@dataclass(frozen=True)
class BlackSwanResult:
    """Outcome of black-swan slot allocation."""

    black_swan_selected: list[Any]
    """Items chosen to fill the surprise slots (excluding overlap with the
    primary relevance pick)."""

    slot_count: int
    """Number of slots allocated by the fraction policy."""

    inspected: int
    """Number of rejected-pool items considered."""

    min_score_threshold: float


def allocate_black_swan_slots(
    inbox_size: int,
    rejected_pool: Iterable[Any],
    already_selected_keys: set[str],
    *,
    fraction: float = DEFAULT_BLACK_SWAN_FRACTION,
    min_score: float = DEFAULT_BLACK_SWAN_MIN_SCORE,
    surprise_attr: str = "surprise_score",
    key_attr: str = "key",
) -> BlackSwanResult:
    """Pick N=floor(fraction*inbox_size) highest-surprise items from the rejected pool.

    The function is intentionally conservative: it only surfaces items whose
    surprise score clears `min_score`. This prevents a flat / boring batch from
    auto-promoting mediocre rejects just because the slot exists.

    Args:
        inbox_size: number of relevance-selected items going into the inbox
            (used to compute how many surprise slots to allocate).
        rejected_pool: items NOT chosen by plateau_select. Each must expose
            `surprise_score` and `key` (override via `surprise_attr`/`key_attr`).
        already_selected_keys: keys already chosen by plateau_select — excluded
            to avoid double-counting.
        fraction: surprise-slot fraction of the inbox (default 10%).
        min_score: minimum surprise_score to be considered (default 0.30).
        surprise_attr / key_attr: attribute names on the candidate objects.

    Returns:
        BlackSwanResult.
    """
    if inbox_size <= 0:
        return BlackSwanResult(
            black_swan_selected=[],
            slot_count=0,
            inspected=0,
            min_score_threshold=min_score,
        )

    slot_count = math.floor(max(0.0, fraction) * inbox_size)
    if slot_count <= 0:
        return BlackSwanResult(
            black_swan_selected=[],
            slot_count=0,
            inspected=0,
            min_score_threshold=min_score,
        )

    eligible: list[Any] = []
    inspected = 0
    for cand in rejected_pool:
        inspected += 1
        key = str(getattr(cand, key_attr, "") or "")
        if key and key in already_selected_keys:
            continue
        score = float(getattr(cand, surprise_attr, 0.0) or 0.0)
        if score < min_score:
            continue
        eligible.append(cand)

    eligible.sort(
        key=lambda c: float(getattr(c, surprise_attr, 0.0) or 0.0),
        reverse=True,
    )

    chosen = eligible[:slot_count]
    logger.info(
        "black_swan: inbox=%d slots=%d eligible=%d chosen=%d min_score=%.2f",
        inbox_size,
        slot_count,
        len(eligible),
        len(chosen),
        min_score,
    )
    return BlackSwanResult(
        black_swan_selected=chosen,
        slot_count=slot_count,
        inspected=inspected,
        min_score_threshold=min_score,
    )
