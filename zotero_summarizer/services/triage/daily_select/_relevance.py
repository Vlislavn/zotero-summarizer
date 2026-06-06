"""Heuristic, NO-LLM "why it matters" chips for the Today slate.

Single responsibility: turn the per-paper signals already computed during
candidate normalization (:mod:`_candidate`) into a short, ordered list of
plain-language reason chips for the card. Pure thresholds on numbers — no LLM,
no I/O — so it works fully offline and for *every* gate type (unlike SHAP,
which is LightGBM-only and empty otherwise).

Thresholds reuse the canonical project constants where they exist
(``PRIORITY_SHOULD_READ_THRESHOLD``, ``DEFAULT_BLACK_SWAN_MIN_SCORE``) so a
chip and the rest of the pipeline never disagree. ``corpus_affinity`` (goal-text
similarity) is the strongest ranking lever and is the primary reason.
"""
from __future__ import annotations

from zotero_summarizer.domain import PRIORITY_SHOULD_READ_THRESHOLD
from zotero_summarizer.services.model.surprise import DEFAULT_BLACK_SWAN_MIN_SCORE

# Goal-text similarity bands. Mirror models.config.CorpusConfig.similarity_threshold
# (-0.30) and the diversity picker's strictly-negative predicate
# (_allocation._pick_diversity, corpus_affinity < 0.0).
_GOAL_STRONG = 0.30
_GOAL_MILD = 0.10
# Author standing: matches _candidate.row_prestige's log1p(30) reference point.
_H_INDEX_SENIOR = 30
# OpenAlex field+year-normalized citation percentile in [0, 1] — top quintile.
_CITED_PCT = 0.80
# Cap so the card stays scannable (Miller's Law); strongest reasons win.
_MAX_WHY = 3


def build_why(
    *,
    composite_score: float,
    corpus_affinity: float,
    surprise_score: float,
    h_index: int | None = None,
    citation_percentile: float | None = None,
) -> list[str]:
    """Ordered, capped list of plain-language reason chips for one card.

    Strongest signal first; at most one goal-match chip. Missing signals are
    simply omitted (fail-soft). An empty list is a valid "no signal cleared a
    threshold" result, not an error.
    """
    why: list[str] = []

    # 1. Goal match — the primary lever, exactly one chip.
    if corpus_affinity >= _GOAL_STRONG:
        why.append("Strong goal match")
    elif corpus_affinity >= _GOAL_MILD:
        why.append("On-topic for you")
    elif corpus_affinity < 0.0:
        why.append("Off your usual track")

    # 2. The model's own relevance score.
    if composite_score >= PRIORITY_SHOULD_READ_THRESHOLD:
        why.append("High model relevance")

    # 3. Author standing.
    if h_index is not None and h_index >= _H_INDEX_SENIOR:
        why.append(f"Senior authors (h={int(h_index)})")

    # 4. Citation impact.
    if citation_percentile is not None and citation_percentile >= _CITED_PCT:
        why.append("Highly cited")

    # 5. Anomaly / black-swan.
    if surprise_score >= DEFAULT_BLACK_SWAN_MIN_SCORE:
        why.append("Surprising")

    return why[:_MAX_WHY]


__all__ = ["build_why"]
