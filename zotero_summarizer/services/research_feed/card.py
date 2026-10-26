"""Pure engineering-card projection from an existing deep-review artifact."""
from __future__ import annotations

from typing import Any

from zotero_summarizer.models import (
    ResearchCandidate,
    ResearchEngineeringCard,
    ResearchFeedTriage,
    ResearchProfile,
)
from zotero_summarizer.services.library.review_fleet.propose import _goal_evidence, effective_read_decision


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


def _reproducibility(review: dict[str, Any], code_urls: list[str]) -> tuple[str, list[str]]:
    quality = review.get("quality") or {}
    missing = [str(value) for value in (quality.get("missing_critical") or []) if str(value).strip()]
    if not code_urls:
        missing.append("No verified code URL")
    if missing:
        return "blocked", list(dict.fromkeys(missing))
    return "unknown", []


def _assessment_fields(assessment: dict) -> dict:
    fields = {"project_assessment_status": assessment["status"], "project_uses": [],
              "project_use_notes": ["Project mechanisms and validation experiments have not been assessed."],
              "research_ideas": [], "research_impact": 0, "production_impact": 0, "personal_novelty": 0}
    if assessment["status"] == "not_assessed":
        return fields
    content = assessment["content"]
    fields.update(project_assessments=content["projects"], assessment_evidence=assessment["evidence"],
                  assessment_origin="ai_generated", scientific_verification="unverified",
                  reproduction_assessment=content["reproduction"], impact_assessment=content["impact"],
                  project_use_notes=["AI-generated project proposals; scientifically unverified."])
    for record in content["projects"]:
        fields["project_use_notes"].append(f"{record['target_project']} — {record['assessment']}: {record['rationale']}")
        if record["use_kind"] is not None and record["use_kind"] not in fields["project_uses"]:
            fields["project_uses"].append(record["use_kind"])
        if record["assessment"] == "applicable":
            fields["project_use_notes"].append(
                f"{record['mechanism']} → {record['target_component']}; change: {record['proposed_change']}; "
                f"validate: {record['minimal_validation_experiment']}")
            fields["research_ideas"].append({"proposed_change": record["proposed_change"],
                "source_paper_id": record["source_id"], "target_project": record["target_project"],
                "minimal_validation_experiment": record["minimal_validation_experiment"]})
    reproduction, impact = content["reproduction"], content["impact"]
    if reproduction is not None:
        fields.update(reproducibility_tier=reproduction["tier"],
                      reproducibility_rationale="AI estimate, unverified: " + reproduction["rationale"],
                      missing_reproduction_inputs=reproduction["missing_inputs"])
    if impact is not None:
        fields.update(research_impact=impact["research"], production_impact=impact["production"],
                      personal_novelty=impact["personal_novelty"])
    return fields


def _goal_notes(review: dict[str, Any], profile: ResearchProfile) -> list[str]:
    goals = review.get("goal_summaries")
    if not isinstance(goals, list):
        return []
    notes = []
    for cell in goals:
        if not isinstance(cell, dict) or cell.get("goal") not in profile.projects:
            continue
        summary, quotes = cell.get("summary"), cell.get("supporting_quotes")
        if (_goal_evidence([cell]) == "match" and isinstance(summary, str) and summary.strip()
                and isinstance(quotes, list) and quotes
                and all(isinstance(quote, str) and quote.strip() for quote in quotes)):
            notes.append(f"Retained goal observation (unverified) — {cell['goal']}: {summary}")
    return notes


def build_card(
    candidate: ResearchCandidate,
    triage: ResearchFeedTriage,
    review: dict[str, Any],
    profile: ResearchProfile,
    *, assessment: dict | None = None,
) -> ResearchEngineeringCard:
    from zotero_summarizer.services.library import _feed_assessment
    _feed_assessment.review_evidence(review)
    if assessment is None:
        assessment = _feed_assessment.qualify(review, _feed_assessment.context_for(candidate.source_id, profile))
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
    worth, flags = effective_read_decision(
        digest, quality, goal_summaries=review.get("goal_summaries"),
    )
    tier, missing = _reproducibility(review, code_urls)
    fields = {"reproducibility_tier": tier, "missing_reproduction_inputs": missing,
              "reproducibility_rationale": "Reproduction effort has not been assessed." if not missing else "; ".join(missing),
              **_assessment_fields(assessment)}
    fields["project_use_notes"].extend(_goal_notes(review, profile))
    fields["missing_reproduction_inputs"] = list(dict.fromkeys([*missing, *fields["missing_reproduction_inputs"]]))
    if missing and assessment.get("content", {}).get("reproduction") is not None:
        fields.update(reproducibility_tier="blocked", reproducibility_rationale=
                      "; ".join(missing) + "; " + fields["reproducibility_rationale"])
    return ResearchEngineeringCard(
        source_id=candidate.source_id,
        problem=str(digest.get("tldr") or candidate.abstract[:500] or candidate.title),
        core_idea=str(digest.get("key_strength") or digest.get("executive_summary") or ""),
        engineering_novelty=str(digest.get("methods") or ""),
        contribution_kind=triage.contribution_kind, topic_tags=topics,
        code_urls=code_urls, dataset_urls=candidate.dataset_urls, model_urls=candidate.model_urls,
        benchmark_names=[candidate.title] if "benchmark" in triage.contribution_kind else [],
        **fields, worth_reading=worth or "unknown",
        evidence_gaps=list(dict.fromkeys([
            *review.get("reading_policy_flags", []), *flags,
            *[str(value) for value in (quality.get("red_flags") or []) if str(value).strip()],
        ])),
    )


__all__ = ["build_card", "topic_tags"]
