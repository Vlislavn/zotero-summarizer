"""Heuristic, NO-LLM "why it matters" chips for the Today slate.

Single responsibility: turn the per-paper signals already computed during
candidate normalization (:mod:`_candidate`) into a short, ordered list of
plain-language reason chips for the card. Pure thresholds on numbers — no LLM,
no I/O — so it works fully offline and for *every* gate type (unlike SHAP,
which is LightGBM-only and empty otherwise).

Two distinct signals, labeled honestly (they used to be conflated):

* ``goal_sim`` — cosine to the user's stated research-goal texts ("what you
  SAID you want"). Goal chips key on THIS, with pool-relative tercile bands
  (mirrors the frontend ``relevanceBands.goalHighKeys`` idiom) — raw-cosine
  constants would drift across embedding models and goal phrasings.
* ``corpus_affinity`` — engagement-weighted similarity to the user's saved
  library ("what you DID"). Its positive chip says library, not goal; its
  negative band stays the diversity-picker predicate
  (``_allocation._pick_diversity``, corpus_affinity < 0.0).

Other thresholds reuse the canonical project constants where they exist
(``PRIORITY_SHOULD_READ_THRESHOLD``, ``DEFAULT_BLACK_SWAN_MIN_SCORE``) so a
chip and the rest of the pipeline never disagree.
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.domain import PRIORITY_SHOULD_READ_THRESHOLD
from zotero_summarizer.services.model.surprise import DEFAULT_BLACK_SWAN_MIN_SCORE

# Engagement-affinity band for the positive library chip. Pre-existing band for
# this signal (kept from the original chip thresholds; corpus_affinity is a
# pos−neg difference in [-1, 1], where ≥0.30 is a decisively library-like paper).
_AFFINITY_STRONG = 0.30
# Author standing: matches _candidate.row_prestige's log1p(30) reference point.
_H_INDEX_SENIOR = 30
# OpenAlex field+year-normalized citation percentile in [0, 1] — top quintile.
_CITED_PCT = 0.80
# Cap so the card stays scannable (Miller's Law); strongest reasons win.
_MAX_WHY = 3


def goal_bands(candidates: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    """``(strong, mild)`` goal-chip thresholds = lower edges of the top and
    middle terciles of the cohort's PRESENT, POSITIVE goal_sims (self-
    calibrating — no absolute cosine constant to tune or drift). ``(None,
    None)`` when fewer than 3 candidates carry a positive goal signal: a
    tercile needs 3 points, and with 1–2 the bands degenerate (strong == the
    lone weak value), crowning e.g. a goal_sim of 0.2 "Strong goal match"
    right under the weak-week banner. No goal chips then, never a guessed
    band."""
    vals = sorted(
        c["goal_sim"] for c in candidates
        if c.get("goal_sim") is not None and c["goal_sim"] > 0
    )
    if len(vals) < 3:
        return None, None
    return vals[(2 * len(vals)) // 3], vals[len(vals) // 3]


def build_why(
    *,
    composite_score: float,
    corpus_affinity: float,
    surprise_score: float,
    h_index: int | None = None,
    citation_percentile: float | None = None,
    goal_sim: float | None = None,
    goal_strong: float | None = None,
    goal_mild: float | None = None,
) -> list[str]:
    """Ordered, capped list of plain-language reason chips for one card.

    Strongest signal first; at most one goal chip and one library chip.
    Missing signals are simply omitted (fail-soft). An empty list is a valid
    "no signal cleared a threshold" result, not an error. Goal chips require
    BOTH a real per-card ``goal_sim`` and cohort bands from :func:`goal_bands`
    — a card is never called a goal match without goal evidence.
    """
    why: list[str] = []

    # 1. Goal match — what the user SAID they want; the strongest ranking lever.
    if goal_sim is not None and goal_sim > 0 and goal_strong is not None:
        if goal_sim >= goal_strong:
            why.append("Strong goal match")
        elif goal_mild is not None and goal_sim >= goal_mild:
            why.append("On-topic for you")

    # 2. Library engagement — what the user DID (kept deliberately: the wanted
    #    library-anchored pull). Negative = the diversity-picker predicate.
    if corpus_affinity >= _AFFINITY_STRONG:
        why.append("Like papers you've saved")
    elif corpus_affinity < 0.0:
        why.append("Off your usual track")

    # 3. The model's own relevance score.
    if composite_score >= PRIORITY_SHOULD_READ_THRESHOLD:
        why.append("High model relevance")

    # 4. Author standing.
    if h_index is not None and h_index >= _H_INDEX_SENIOR:
        why.append(f"Senior authors (h={int(h_index)})")

    # 5. Citation impact.
    if citation_percentile is not None and citation_percentile >= _CITED_PCT:
        why.append("Highly cited")

    # 6. Anomaly / black-swan.
    if surprise_score >= DEFAULT_BLACK_SWAN_MIN_SCORE:
        why.append("Surprising")

    return why[:_MAX_WHY]


def attach_why(candidates: list[dict[str, Any]]) -> None:
    """Attach ``why`` chips to each candidate IN PLACE.

    Goal bands are pool-relative: derived from the candidates themselves
    (cohort terciles, see :func:`goal_bands`)."""
    strong, mild = goal_bands(candidates)
    for c in candidates:
        c["why"] = build_why(
            composite_score=c["composite_score"],
            corpus_affinity=c["corpus_affinity"],
            surprise_score=c["surprise_score"],
            h_index=c.get("max_author_h_index"),
            citation_percentile=c.get("citation_percentile"),
            goal_sim=c.get("goal_sim"),
            goal_strong=strong,
            goal_mild=mild,
        )


__all__ = ["build_why", "attach_why", "goal_bands"]
