"""Whole-current-cohort ordering reuses the existing relevance/goal/prestige blend."""
from zotero_summarizer.services.model.rank_blend import blend_scores
from zotero_summarizer.services.research_feed.source import number, summary


def rank_candidates(assessed, rows):
    scored, unscored, failed, inputs = [], [], [], {}
    for candidate, triage in assessed:
        if not triage.include:
            continue
        key = candidate.source_id
        try:
            row = rows.get(key) or {}
            raw = row.get("composite_score")
            if raw is None:
                raw = summary(row).get("composite_relevance_score")
            if raw is None:
                unscored.append((candidate, triage))
                inputs[key] = {"relevance": None}
                continue
            aux = summary(row, "aux_context")
            goals = aux.get("goal_sims", {})
            if not isinstance(goals, dict) or any(value is None for value in goals.values()):
                raise ValueError("invalid goal similarity")
            inputs[key] = {"relevance": number(raw),
                "goal": max((number(v, minimum=-1, maximum=1) for v in goals.values()), default=None),
                "prestige": number(aux["citation_percentile"], maximum=1) if aux.get("citation_percentile") is not None else None}
            scored.append((candidate, triage))
        except (ValueError, TypeError) as exc:
            failed.append({"source_id": key, "error": f"ranking input invalid: {exc}"})
    blended = blend_scores(*[[inputs[c.source_id][field] for c, _t in scored]
                             for field in ("relevance", "goal", "prestige")])
    for (candidate, _triage), blend in zip(scored, blended, strict=True):
        inputs[candidate.source_id]["blend"] = blend
    scored.sort(key=lambda pair: inputs[pair[0].source_id]["blend"], reverse=True)
    unscored.sort(key=lambda pair: (pair[1].score, pair[1].confidence, pair[0].title), reverse=True)
    return scored + unscored, {"contract": "weekly_rank_v1", "inputs": inputs}, failed
