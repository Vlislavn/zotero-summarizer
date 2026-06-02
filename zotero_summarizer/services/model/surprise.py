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

Reference: Maystre et al. 2025, Spotify Calibrated Recommendations
(arXiv:2509.05460); Kotkov et al. RecSys 2024 (serendipity).
"""
from __future__ import annotations

import math

DEFAULT_BLACK_SWAN_MIN_SCORE = 0.30  # below this, even the best surprise isn't worth surfacing


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
