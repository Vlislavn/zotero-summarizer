"""Pure engineering-card projection from an existing deep-review artifact."""
from __future__ import annotations

from typing import Any

from zotero_summarizer.models import (
    ResearchCandidate,
    ResearchEngineeringCard,
    ResearchFeedTriage,
    ResearchProfile,
)
from zotero_summarizer.services.library._review_cache import REVIEW_CONTRACT_VERSION
from zotero_summarizer.services.library.review_fleet.propose import effective_read_decision


PROJECT_USE_NOT_ASSESSED = (
    "NOT_ASSESSED: source-bound project mechanism, target component and validation experiment are not supplied."
)
PROJECTION_CONTRACT = "source_qualified_feed_v2"


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


def _sha256(value: object) -> bool:
    return type(value) is str and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def review_evidence(review: dict[str, Any] | None) -> dict[str, Any]:
    """Inspect the current-reader's existing binding, without resolving or acquiring a source."""
    result = {"availability": "unknown", "basis": "unknown", "qualified": False, "reason": "review_missing"}
    if review is None:
        return result
    digest, identity = review.get("digest"), review.get("review_identity")
    result["basis"] = digest.get("basis") if isinstance(digest, dict) else None
    result["acquire_outcome"] = review.get("acquire_outcome")
    bound = (isinstance(identity, dict) and identity.get("source_kind") in {"library", "override"}
             and isinstance(identity.get("source_path"), str) and bool(identity["source_path"].strip())
             and _sha256(identity.get("source_sha256")))
    if bound and review.get("needs_pdf") is True:
        result["reason"] = "conflicting_source_evidence"
        return result
    if bound:
        result.update(availability="available", source_binding=dict(identity))
    elif review.get("needs_pdf") is True and review.get("acquire_outcome") in {
        "no_oa_source", "needs_login", "fetch_failed", "offline_uncached",
        "browser_not_attempted", "browser_extra_unavailable",
    }:
        result.update(availability="unavailable", reason="acquisition_unavailable")
        return result
    if not bound:
        result["reason"] = "review_source_unbound"
    elif (type(review.get("review_contract_version")) is not int
          or review["review_contract_version"] != REVIEW_CONTRACT_VERSION
          or not _sha256(identity.get("generation_sha256"))):
        result["reason"] = "review_identity_unqualified"
    elif not isinstance(digest, dict) or not digest:
        result["reason"] = "digest_missing"
    elif digest.get("basis") != "full_text":
        result["reason"] = "digest_basis_unqualified"
    else:
        result.update(qualified=True, reason="source_bound_full_text")
    return result


def _project_notes(review: dict[str, Any], profile: ResearchProfile) -> list[str]:
    notes = [PROJECT_USE_NOT_ASSESSED]
    for goal in review.get("goal_summaries") or []:
        if (isinstance(goal, dict) and goal.get("goal") in profile.projects
                and goal.get("retrieval_state") == "hit" and goal.get("relevant") is True
                and goal.get("abstained") is False and isinstance(goal.get("summary"), str)
                and goal["summary"].strip() and isinstance(goal.get("supporting_quotes"), list)
                and any(isinstance(quote, str) and quote.strip() for quote in goal["supporting_quotes"])):
            notes.append(f"Unassessed project evidence — {goal['goal']}: {goal['summary']}")
    return notes


def _assessment_fields(candidate: ResearchCandidate, review: dict, profile: ResearchProfile) -> dict:
    from zotero_summarizer.services.library import _feed_assessment

    assessment = _feed_assessment.qualify(review, _feed_assessment.context_for(candidate.source_id, profile))
    fields = {"project_assessment_status": assessment["status"], "project_assessment_reason": assessment["reason"],
              "project_assessments": [],
              "project_uses": [], "project_use_notes": _project_notes(review, profile), "research_ideas": [],
              "research_impact": 0, "production_impact": 0, "personal_novelty": 0,
              "reproducibility_tier": "unknown",
              "reproducibility_rationale": "Reproduction effort and required inputs have not been assessed."}
    if not assessment["qualified"]:
        return fields
    records = assessment["content"]["projects"]
    fields.update(assessment_origin="ai_generated", scientific_verification="unverified",
                  assessment_evidence=assessment["evidence"],
                  impact_assessment=assessment["content"]["impact"],
                  reproduction_assessment=assessment["content"]["reproduction"],
                  project_use_notes=[f"AI-generated, unverified project assessment: {assessment['status']}."])
    for index, record in enumerate(records):
        fields["project_assessments"].append({**record, "original_evidence": assessment["evidence"][f"/projects/{index}"]})
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
    reproduction, impact = assessment["content"]["reproduction"], assessment["content"]["impact"]
    if reproduction is not None:
        fields.update(reproducibility_tier=reproduction["tier"],
                      reproducibility_rationale="AI estimate, unverified: " + reproduction["rationale"],
                      missing_reproduction_inputs=reproduction["missing_inputs"])
    if impact is not None:
        fields.update(research_impact=impact["research"], production_impact=impact["production"],
                      personal_novelty=impact["personal_novelty"])
    return fields


def build_card(
    candidate: ResearchCandidate,
    triage: ResearchFeedTriage,
    review: dict[str, Any],
    profile: ResearchProfile,
) -> ResearchEngineeringCard:
    evidence = review_evidence(review)
    if not evidence["qualified"]:
        raise ValueError(evidence["reason"])
    digest, quality = review["digest"], review.get("quality") or {}
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
    assessment = _assessment_fields(candidate, review, profile)
    return ResearchEngineeringCard(
        source_id=candidate.source_id,
        problem=str(digest.get("tldr") or candidate.abstract[:500] or candidate.title),
        core_idea=str(digest.get("key_strength") or digest.get("executive_summary") or ""),
        engineering_novelty=str(digest.get("methods") or ""),
        contribution_kind=triage.contribution_kind, topic_tags=topics,
        code_urls=code_urls, dataset_urls=candidate.dataset_urls, model_urls=candidate.model_urls,
        benchmark_names=[candidate.title] if "benchmark" in triage.contribution_kind else [],
        **assessment,
        worth_reading=worth or "unknown",
        evidence_gaps=list(dict.fromkeys([
            *review.get("reading_policy_flags", []), *flags,
            *[str(value) for value in (quality.get("red_flags") or []) if str(value).strip()],
            *[str(value) for value in (quality.get("missing_critical") or []) if str(value).strip()],
        ])),
    )


__all__ = ["build_card", "topic_tags"]
