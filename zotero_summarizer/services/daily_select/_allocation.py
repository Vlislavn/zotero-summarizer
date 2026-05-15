"""Greedy role allocator for the daily slate.

Single responsibility: given a candidate pool + audit pool + role quotas,
produce the ordered list of SlatePapers and the list of role names that
fell through to model_fallback.

The slot order (model, surprise, audit, diversity) and the model_fallback
contract are mandated by the Phase 1.17 plan.
"""
from __future__ import annotations

import random
from typing import Any

from zotero_summarizer.services.daily_select._dataclasses import SlatePaper
from zotero_summarizer.services.surprise import DEFAULT_BLACK_SWAN_MIN_SCORE

ROLE_ORDER: tuple[str, ...] = ("model", "surprise", "audit", "diversity")


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
    )


def _pick_model(
    pool: list[dict[str, Any]],
    n: int,
    chosen_ids: set[int],
) -> list[dict[str, Any]]:
    """Top-N by composite_score among unchosen items."""
    ordered = sorted(
        (c for c in pool if c["id"] not in chosen_ids),
        key=lambda c: c["composite_score"],
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


def _pick_audit(
    audit_pool: list[dict[str, Any]],
    n: int,
    chosen_ids: set[int],
    *,
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Random pick from gate_rejected, prefer prestige >= 0.5 when any qualify."""
    eligible = [c for c in audit_pool if c["id"] not in chosen_ids]
    if not eligible:
        return []
    high_prestige = [c for c in eligible if c["prestige_score"] >= 0.5]
    source = high_prestige if high_prestige else eligible
    indexes = list(range(len(source)))
    rng.shuffle(indexes)
    return [source[i] for i in indexes[:n]]


def _pick_diversity(
    pool: list[dict[str, Any]],
    n: int,
    chosen_ids: set[int],
) -> list[dict[str, Any]]:
    """Highest composite among unchosen items with corpus_affinity < 0.0."""
    eligible = [
        c for c in pool
        if c["id"] not in chosen_ids and c["corpus_affinity"] < 0.0
    ]
    eligible.sort(key=lambda c: c["composite_score"], reverse=True)
    return eligible[:n]


def allocate(
    *,
    candidate_pool: list[dict[str, Any]],
    audit_pool: list[dict[str, Any]],
    roles: dict[str, int],
    K: int,
    rng: random.Random,
) -> tuple[list[SlatePaper], list[str]]:
    """Run the greedy role allocator.

    Returns (papers, empty_role_events). Each unfilled slot from a role's
    intended sub-pool contributes one ``model_fallback`` paper drawn from
    the highest-remaining composite score AND one entry to
    ``empty_role_events``. The final list is truncated to K.
    """
    chosen_ids: set[int] = set()
    papers: list[SlatePaper] = []
    empty_roles: list[str] = []

    role_pickers = {
        "model": lambda n: _pick_model(candidate_pool, n, chosen_ids),
        "surprise": lambda n: _pick_surprise(
            candidate_pool, n, chosen_ids, min_score=DEFAULT_BLACK_SWAN_MIN_SCORE
        ),
        "audit": lambda n: _pick_audit(audit_pool, n, chosen_ids, rng=rng),
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
