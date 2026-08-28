"""Pure engineering-card projection from an existing deep-review artifact."""
from __future__ import annotations

from typing import Any

from zotero_summarizer.models import (
    ResearchCandidate,
    ResearchEngineeringCard,
    ResearchFeedTriage,
    ResearchIdea,
    ResearchProfile,
)
from zotero_summarizer.services.library.review_fleet.propose import effective_read_decision


_TOPIC_TERMS = {
    "agents": ("agent", "multi-agent"), "evaluation": ("evaluation", "assess", "metric"),
    "interpretability": ("interpret", "mechanistic"), "reinforcement-learning": ("reinforcement", "reward"),
    "reasoning": ("reasoning",), "multimodal": ("multimodal",), "biology": ("biomedical", "clinical", "biology"),
    "genomics": ("genomic", "omics"), "tool-use": ("tool use", "tool-using", "tool calling"),
    "safety": ("safety", "risk"), "alignment": ("alignment", "specification"), "planning": ("planning", "long-horizon"),
    "memory": ("memory", "retrieval"), "long-context": ("long context", "long-context"),
    "uncertainty": ("uncertainty",), "calibration": ("calibration",), "verification": ("verification", "verify"),
    "hallucination-detection": ("hallucination", "grounding"), "trajectory-analysis": ("trajectory", "trace"),
    "benchmarks": ("benchmark",),
}


def topic_tags(text: str, profile: ResearchProfile) -> list[str]:
    lower = text.lower()
    return [topic for topic in profile.topic_taxonomy
            if any(term in lower for term in _TOPIC_TERMS.get(topic, (topic,)))]


def _project_use(project: str, topics: list[str]) -> str:
    lower = project.lower()
    if "activation" in lower and "interpretability" in topics:
        return "activation_oracle_extension"
    if "biomedical" in lower and ({"biology", "genomics"} & set(topics)):
        return "biomedical_agent_extension"
    if "harness" in lower and ({"agents", "tool-use", "planning"} & set(topics)):
        return "integrate_into_harness"
    if "evaluation" in lower and ({"evaluation", "benchmarks", "verification"} & set(topics)):
        return "use_as_benchmark"
    return "not_applicable"


def _reproducibility(review: dict[str, Any], code_urls: list[str]) -> tuple[str, list[str]]:
    quality = review.get("quality") or {}
    missing = [str(value) for value in (quality.get("missing_critical") or []) if str(value).strip()]
    if not code_urls:
        missing.append("No verified code URL")
    if missing:
        return "blocked", list(dict.fromkeys(missing))
    return "one_week", []


def build_card(
    candidate: ResearchCandidate,
    triage: ResearchFeedTriage,
    review: dict[str, Any],
    profile: ResearchProfile,
) -> ResearchEngineeringCard:
    digest, quality = review.get("digest") or {}, review.get("quality") or {}
    code = review.get("code_link") or {}
    code_urls = list(candidate.code_urls)
    if code.get("found") and code.get("exists") is True and code.get("relevance") == "matched":
        code_urls.append(str(code["url"]))
    evidence_text = " ".join([
        candidate.title, candidate.abstract, str(digest.get("tldr") or ""),
        str(digest.get("methods") or ""), " ".join(digest.get("implementation") or []),
    ])
    topics = topic_tags(evidence_text, profile)
    worth, _flags = effective_read_decision(
        digest, quality, goal_summaries=review.get("goal_summaries"),
    )
    uses = [_project_use(project, topics) for project in triage.matched_projects]
    uses = list(dict.fromkeys(use for use in uses if use != "not_applicable")) or ["not_applicable"]
    relevance = str(digest.get("relevance") or triage.rationale)
    use_notes = [f"{project}: {relevance}" for project in triage.matched_projects] or ["No concrete project fit found."]
    implementations = [str(value).strip() for value in (digest.get("implementation") or []) if str(value).strip()]
    target = triage.matched_projects[0] if triage.matched_projects else "not_applicable"
    ideas = [ResearchIdea(
        proposed_change=idea, source_paper_id=candidate.source_id, target_project=target,
        minimal_validation_experiment=f"Compare one existing {target} task with and without this change.",
    ) for idea in implementations[:3]]
    tier, missing = _reproducibility(review, code_urls)
    return ResearchEngineeringCard(
        source_id=candidate.source_id,
        problem=str(digest.get("tldr") or candidate.abstract[:500] or candidate.title),
        core_idea=str(digest.get("key_strength") or digest.get("executive_summary") or ""),
        engineering_novelty=str(digest.get("methods") or ""),
        contribution_kind=triage.contribution_kind, topic_tags=topics,
        code_urls=code_urls, dataset_urls=candidate.dataset_urls, model_urls=candidate.model_urls,
        benchmark_names=[candidate.title] if "benchmark" in triage.contribution_kind else [],
        reproducibility_tier=tier,
        reproducibility_rationale=("Verified code and no critical missing inputs." if not missing else "; ".join(missing)),
        missing_reproduction_inputs=missing, project_uses=uses, project_use_notes=use_notes,
        research_impact=max(0, min(5, int(digest.get("significance") or 0))),
        production_impact=min(5, 2 + int(bool(implementations)) + int(bool(code_urls))),
        personal_novelty=max(0, min(5, int(digest.get("novelty") or 0))),
        worth_reading=worth or "skip", research_ideas=ideas,
        evidence_gaps=[str(value) for value in (quality.get("red_flags") or []) if str(value).strip()],
    )


__all__ = ["build_card", "topic_tags"]
