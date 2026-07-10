"""Global relevance ranking under an EXPLICIT constrained re-rank contract.

The expert review's third fix (spec §8, §13.3): a bare additive
``blend + quality_bonus`` cannot GUARANTEE "quality can never bury a high-query
paper" — min-max normalization lets a low-relevance paper overtake a high one
whenever their scores are close. This module instead computes a single master
``query_score`` (our cross-encoder over the union — source rank is NOT relevance)
and orders by a lexicographic key with a real guarantee:

    (relevance_bucket = floor(query_score / EPSILON)) desc,
    quality_evidence                                  desc,
    exact query_score                                 desc

Quality can reorder A above B ONLY when they fall in the same relevance bucket;
across buckets, higher query_score always wins. ``goal_sim`` (standing goals) is
a near-zero-weight tiebreak in a targeted session (the query IS the goal), folded
only into the exact-score leg. Retracted candidates sink (integrity, spec §11).
"""
from __future__ import annotations

import os
import time
from typing import Any

from zotero_summarizer.services.model.reranker import get_reranker
from zotero_summarizer.services.search._models import Candidate

# Bucket width for the constrained contract: query scores within EPSILON are
# "tied on relevance", so quality may reorder them. Named constant + env override
# (no magic number); the eval seed (spec §14) tunes it. bge cross-encoder logits
# are unbounded (~[-11, 11]); 0.5 groups near-ties without collapsing the scale.
DEFAULT_EPSILON = 0.5
# Weak standing-goal tiebreak weight for a targeted session — the query already
# IS the goal (spec §8: ~0-0.05, NOT the shipped 0.4).
GOAL_TIEBREAK_WEIGHT = 0.05
# How long to wait for the async reranker load before degrading to lexical
# scoring (mirrors hybrid_search's degradation ladder). Named + env-overridable.
_RERANK_WAIT_SECS = 8.0


def _epsilon() -> float:
    raw = (os.getenv("ZS_SEARCH_RANK_EPSILON") or "").strip()
    try:
        return float(raw) if raw else DEFAULT_EPSILON
    except ValueError:
        return DEFAULT_EPSILON


def _doc_text(cand: Candidate) -> str:
    return (cand.title + "\n" + cand.abstract).strip()


def _lexical_score(query: str, cand: Candidate) -> float:
    """Deterministic token-overlap fallback when the cross-encoder isn't ready —
    a real relevance signal (Jaccard of query terms in title+abstract), so the
    pipeline still produces a genuine ordering (degradation ladder, not a stub)."""
    q_terms = {t for t in query.lower().split() if len(t) > 2}
    if not q_terms:
        return 0.0
    doc_terms = set(_doc_text(cand).lower().split())
    return len(q_terms & doc_terms) / len(q_terms)


def score_query_relevance(query: str, candidates: list[Candidate], *, reranker_model: str) -> str:
    """Fill ``candidate.query_score`` for every candidate. Uses the local
    cross-encoder; if it is still loading after a short wait, degrades to a lexical
    score. Returns the mode used ("cross_encoder" | "lexical")."""
    query = (query or "").strip()
    if not candidates or not query:
        for cand in candidates:
            cand.query_score = 0.0
        return "lexical"

    reranker = get_reranker(reranker_model)
    reranker.ensure_loaded_async()
    deadline = time.monotonic() + _RERANK_WAIT_SECS
    while not reranker.is_ready() and reranker.is_loading() and time.monotonic() < deadline:
        time.sleep(0.2)

    pairs = [(cand.candidate_id, _doc_text(cand)) for cand in candidates]
    ranked = reranker.rerank(query, pairs, top_n=len(pairs))
    if ranked:
        scores = dict(ranked)
        for cand in candidates:
            cand.query_score = float(scores.get(cand.candidate_id, 0.0))
        return "cross_encoder"
    for cand in candidates:
        cand.query_score = _lexical_score(query, cand)
    return "lexical"


def _quality_evidence(cand: Candidate) -> float:
    """A single quality magnitude for the middle leg of the contract. Uses the
    query-INDEPENDENT QualityEval band/grade only (never the goal-conditioned
    digest grade). 0.0 when unreviewed → quality is a no-op before light review."""
    band = str(cand.quality.get("quality_band") or "").lower()
    grade = str(cand.quality.get("grade") or "").upper()
    band_lift = {"highlight": 1.0, "flag": -1.0}.get(band, 0.0)
    grade_lift = {"A": 0.3, "B": 0.15, "C": 0.0, "D": -0.15}.get(grade, 0.0)
    return band_lift + grade_lift


def constrained_key(cand: Candidate, *, epsilon: float) -> tuple[float, float, float]:
    """The lexicographic sort key (spec §8). Retracted → hard-sink bucket."""
    qs = cand.query_score if cand.query_score is not None else 0.0
    if cand.is_retracted:
        return (float("-inf"), 0.0, qs)
    bucket = qs // epsilon
    goal = cand.goal_sim if cand.goal_sim is not None else 0.0
    exact = qs + GOAL_TIEBREAK_WEIGHT * goal
    return (bucket, _quality_evidence(cand), exact)


def rank_candidates(candidates: list[Candidate], *, epsilon: float | None = None) -> list[Candidate]:
    """Order in place + return, by the constrained contract. Call AFTER
    ``score_query_relevance`` (and after light review, so quality is populated)."""
    eps = epsilon if epsilon is not None else _epsilon()
    candidates.sort(key=lambda c: constrained_key(c, epsilon=eps), reverse=True)
    return candidates


__all__ = [
    "DEFAULT_EPSILON",
    "GOAL_TIEBREAK_WEIGHT",
    "score_query_relevance",
    "constrained_key",
    "rank_candidates",
]
