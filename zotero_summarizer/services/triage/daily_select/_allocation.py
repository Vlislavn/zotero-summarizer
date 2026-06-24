"""Greedy role allocator for the daily slate.

Single responsibility: given a candidate pool + role quotas, produce the
ordered list of SlatePapers and the list of role names that fell through
to model_fallback.

The slot order (model, surprise, diversity) and the model_fallback
contract are mandated by the Phase 1.17 plan. The former ``audit`` role
(gate-rejected spot-check) was removed entirely — spot-check lives in the
Review page + Today's SpotCheck section (``services/library/review``).
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.domain import PRIORITY_COULD_READ_THRESHOLD
from zotero_summarizer.services._common import band_primary_enabled
from zotero_summarizer.services.triage.daily_select._dataclasses import SlatePaper
from zotero_summarizer.services.model.rank_blend import quality_bonus
from zotero_summarizer.services.model.surprise import DEFAULT_BLACK_SWAN_MIN_SCORE

ROLE_ORDER: tuple[str, ...] = ("model", "surprise", "diversity")

# The model role (and its fallback) never surfaces a ``dont_read``-band paper
# (composite < 2.0): on a weak feed week the top-K-by-blend would otherwise pad
# Today with papers below the user's reading bar. Keyed to the canonical
# ``could_read`` threshold (single source of truth in ``domain``) so the slate's
# "hidden as too weak" line matches the band a card would display. Surprise and
# diversity are deliberately NOT floored — they exist to surface off-pattern /
# off-library papers and own their own predicates.
MODEL_RELEVANCE_FLOOR = PRIORITY_COULD_READ_THRESHOLD


def _to_slate_paper(cand: dict[str, Any], *, role: str) -> SlatePaper:
    return SlatePaper(
        item_key=cand["item_key"],
        item_id=cand["id"],
        title=cand["title"],
        authors=cand["authors"],
        venue=cand["venue"],
        role=role,
        composite_score=cand["composite_score"],
        surprise_score=cand["surprise_score"],
        corpus_affinity=cand["corpus_affinity"],
        prestige_score=cand["prestige_score"],
        rationale=cand["rationale"],
        shap_top=cand["shap_top"],
        decision=cand["decision"],
        max_author_h_index=cand.get("max_author_h_index"),
        feed_name=cand.get("feed_name", ""),
        quality=cand.get("quality") or {},
        abstract=cand.get("abstract", ""),
        pub_year=cand.get("pub_year"),
        why=cand.get("why", []),
        goal_sim=cand.get("goal_sim"),
    )


def _model_score(cand: dict[str, Any], *, use_band: bool) -> float:
    """``rank_score`` + the capped deep-review QUALITY lift. Quality is applied
    ONLY here, in the FLOORED model role (and its fallback) — never in the
    un-floored surprise/diversity pickers, whose job is off-pattern / off-library
    discovery, not quality. The cap bounds the within-role reorder; the floor
    (on the raw composite, below) is untouched, so a below-bar paper can never be
    lifted into the slate by quality."""
    q = cand.get("quality") or {}
    return cand["rank_score"] + quality_bonus(
        q.get("quality_band"), q.get("grade"), use_band=use_band
    )


def _pick_model(
    pool: list[dict[str, Any]],
    n: int,
    chosen_ids: set[int],
    *,
    min_composite: float = MODEL_RELEVANCE_FLOOR,
) -> list[dict[str, Any]]:
    """Top-N by ``rank_score`` + capped quality lift among unchosen items AT/ABOVE
    the relevance floor — the system's current best recommendation: the gate
    composite blended with goal-text similarity and known prestige
    (``_candidate.attach_rank_scores``), then floated by deep-review quality, with
    ``dont_read``-band papers (composite < ``min_composite``) excluded so a weak
    feed week doesn't pad Today with below-the-bar picks. Falls back to pure
    composite order automatically when the blend/quality signals are absent."""
    use_band = band_primary_enabled()
    ordered = sorted(
        (c for c in pool if c["id"] not in chosen_ids and c["composite_score"] >= min_composite),
        key=lambda c: _model_score(c, use_band=use_band),
        reverse=True,
    )
    return ordered[:n]


def _pick_surprise(
    pool: list[dict[str, Any]],
    n: int,
    chosen_ids: set[int],
    *,
    min_score: float,
) -> list[dict[str, Any]]:
    """Highest surprise_score among unchosen items clearing the floor."""
    eligible = [
        c for c in pool
        if c["id"] not in chosen_ids and c["surprise_score"] >= min_score
    ]
    eligible.sort(key=lambda c: c["surprise_score"], reverse=True)
    return eligible[:n]


def _pick_diversity(
    pool: list[dict[str, Any]],
    n: int,
    chosen_ids: set[int],
) -> list[dict[str, Any]]:
    """Highest ``rank_score`` among unchosen items with corpus_affinity < 0.0
    — the best off-library candidate, judged by the same blended order the
    model picks use."""
    eligible = [
        c for c in pool
        if c["id"] not in chosen_ids and c["corpus_affinity"] < 0.0
    ]
    eligible.sort(key=lambda c: c["rank_score"], reverse=True)
    return eligible[:n]


def allocate(
    *,
    candidate_pool: list[dict[str, Any]],
    roles: dict[str, int],
    K: int,
) -> tuple[list[SlatePaper], list[str]]:
    """Run the greedy role allocator.

    Returns (papers, empty_role_events). Each unfilled slot from a role's
    intended sub-pool contributes one ``model_fallback`` paper drawn from
    the highest-remaining ``rank_score`` (the shared relevance×goal×prestige
    blend) AND one entry to ``empty_role_events``. The final list is
    truncated to K.
    """
    chosen_ids: set[int] = set()
    papers: list[SlatePaper] = []
    empty_roles: list[str] = []

    role_pickers = {
        "model": lambda n: _pick_model(candidate_pool, n, chosen_ids),
        "surprise": lambda n: _pick_surprise(
            candidate_pool, n, chosen_ids, min_score=DEFAULT_BLACK_SWAN_MIN_SCORE
        ),
        "diversity": lambda n: _pick_diversity(candidate_pool, n, chosen_ids),
    }

    for role in ROLE_ORDER:
        wanted = int(roles.get(role, 0))
        if wanted <= 0:
            continue
        picked = role_pickers[role](wanted)
        for cand in picked:
            chosen_ids.add(cand["id"])
            papers.append(_to_slate_paper(cand, role=role))
        missing = wanted - len(picked)
        for _ in range(missing):
            fallback = _pick_model(candidate_pool, 1, chosen_ids)
            empty_roles.append(role)
            if not fallback:
                # Genuinely nothing left — slate becomes shorter than K.
                continue
            chosen_ids.add(fallback[0]["id"])
            papers.append(_to_slate_paper(fallback[0], role="model_fallback"))

    # Cap in case roles sum to > K.
    return papers[:K], empty_roles


__all__ = ["allocate", "ROLE_ORDER"]
