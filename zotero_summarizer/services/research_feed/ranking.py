"""Pure observed-history projection and whole-cohort weekly ordering."""
from __future__ import annotations

from typing import Any

from zotero_summarizer.models import ResearchCandidate, ResearchFeedTriage
from zotero_summarizer.services.model.rank_blend import blend_scores
from zotero_summarizer.services.research_feed.source import number, summary


def rank_candidates(
    assessed: list[tuple[ResearchCandidate, ResearchFeedTriage]],
    accepted_rows: dict[str, dict[str, Any]],
    source_order_ids: list[str],
) -> tuple[list[tuple[ResearchCandidate, ResearchFeedTriage]], dict[str, Any], list[dict[str, str]]]:
    """Rank valid observations before budgets; preserve an unobserved legacy tail."""
    positions = {key: index for index, key in enumerate(source_order_ids)}
    assessed_ids = [candidate.source_id for candidate, _triage in assessed]
    if (len(positions) != len(source_order_ids) or len(set(assessed_ids)) != len(assessed_ids)
            or not set(assessed_ids) <= positions.keys()):
        raise ValueError("ranking cohort identities are invalid")
    inputs: dict[str, dict[str, Any]] = {
        key: {"input_order": index, "status": "not_applicable"} for key, index in positions.items()
    }
    scored, unscored, failures = [], [], []
    for candidate, triage in sorted(assessed, key=lambda pair: positions[pair[0].source_id]):
        if not triage.include:
            continue
        key = candidate.source_id
        entry = inputs[key]
        row = accepted_rows.get(key)
        try:
            raw = (row or {}).get("composite_score")
            origin = "native_column"
            if raw is None:
                raw = summary(row).get("composite_relevance_score")
                origin = "summary_fallback"
            if raw is None:
                entry.update(status="unscored", reason="no_observed_composite", relevance=None,
                             relevance_origin="unobserved", blend_key=None)
                unscored.append((candidate, triage))
                continue
            relevance = number(raw, default=0.0, maximum=5.0)
            aux = summary(row, field="aux_context")
            goal_sims = aux.get("goal_sims")
            if goal_sims is not None and (not isinstance(goal_sims, dict)
                                         or any(value is None for value in goal_sims.values())):
                raise ValueError("stored goal similarity is invalid")
            goals = [number(value, default=0.0, minimum=-1.0, maximum=1.0)
                     for value in (goal_sims or {}).values()]
            citation = aux.get("citation_percentile")
            if citation is not None:
                citation = number(citation, default=0.0, maximum=1.0)
        except (ValueError, TypeError):
            entry.update(status="failed", reason="ranking_input_invalid")
            failures.append({"source_id": key, "error": "ValueError: ranking_input_invalid"})
            continue
        entry.update(status="scored", relevance=relevance, relevance_origin=origin,
                     goal_sim=max(goals, default=None), citation_percentile=citation)
        scored.append((candidate, triage))

    keys = blend_scores(
        [inputs[candidate.source_id]["relevance"] for candidate, _triage in scored],
        [inputs[candidate.source_id]["goal_sim"] for candidate, _triage in scored],
        [inputs[candidate.source_id]["citation_percentile"] for candidate, _triage in scored],
    )
    for (candidate, _triage), blend_key in zip(scored, keys, strict=True):
        inputs[candidate.source_id]["blend_key"] = blend_key
    scored.sort(key=lambda pair: inputs[pair[0].source_id]["blend_key"], reverse=True)
    unscored.sort(key=lambda pair: (pair[1].score, pair[1].confidence, pair[0].title), reverse=True)
    included = scored + unscored
    for order, (candidate, _triage) in enumerate(included, 1):
        inputs[candidate.source_id]["order"] = order
    return included, {
        "contract": "weekly_rank_v1", "cohort_scope": "b1_eligible_valid_observed", "inputs": inputs,
    }, failures
