"""Contracts for the source-neutral weekly Research Intelligence feed."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator, model_validator

from zotero_summarizer.models.triage import PaperDigest


TOPIC_TAXONOMY = frozenset({
    "agents", "evaluation", "interpretability", "reinforcement-learning", "reasoning",
    "multimodal", "biology", "genomics", "tool-use", "safety", "alignment", "planning",
    "memory", "long-context", "uncertainty", "calibration", "verification",
    "hallucination-detection", "trajectory-analysis", "benchmarks",
})


_ShortlistBudget = Annotated[int, Field(strict=True, ge=1, le=100)]
_CardBudget = Annotated[int, Field(strict=True, ge=1, le=20)]


def parse_run_budgets(shortlist, cards, source_limit, timeout) -> tuple[int | None, int | None, int, int]:
    # ponytail: bounded one-shot work; larger jobs need explicit chunked scheduling.
    return TypeAdapter(tuple[
        _ShortlistBudget | None, _CardBudget | None,
        Annotated[int, Field(strict=True, ge=1, le=5000)],
        Annotated[int, Field(strict=True, ge=1, le=86400)],
    ]).validate_python((shortlist, cards, source_limit, timeout))


class ResearchProfile(BaseModel):
    schema_version: Literal[1] = 1
    themes: list[str]
    projects: list[str]
    shortlist_budget: _ShortlistBudget = 20
    card_budget: _CardBudget = 10
    topic_taxonomy: list[str] = Field(default_factory=lambda: sorted(TOPIC_TAXONOMY))

    @field_validator("topic_taxonomy")
    @classmethod
    def _known_topics(cls, values: list[str]) -> list[str]:
        unknown = set(values) - TOPIC_TAXONOMY
        if unknown:
            raise ValueError(f"unknown research-feed topics: {sorted(unknown)}")
        return list(dict.fromkeys(values))


class ResearchCandidate(BaseModel):
    source_id: str
    source: str
    title: str
    abstract: str = ""
    url: str = ""
    doi: str | None = None
    published_at: datetime | None = None
    updated_at: datetime | None = None
    authors: list[str] = Field(default_factory=list)
    venue: str | None = None
    pdf_url: str | None = None
    code_urls: list[str] = Field(default_factory=list)
    dataset_urls: list[str] = Field(default_factory=list)
    model_urls: list[str] = Field(default_factory=list)


class ResearchFeedTriage(BaseModel):
    include: bool
    score: int = Field(ge=1, le=5)
    confidence: float = Field(ge=0.0, le=1.0)
    matched_themes: list[str] = Field(default_factory=list)
    matched_projects: list[str] = Field(default_factory=list)
    contribution_kind: list[str] = Field(default_factory=list)
    artifact_signal: Literal["present", "absent", "unknown"] = "unknown"
    novelty_claim: str = ""
    rationale: str


class ResearchIdea(BaseModel):
    proposed_change: str
    source_paper_id: str
    target_project: str
    minimal_validation_experiment: str


ProjectUse = Literal[
    "integrate_into_harness", "use_as_benchmark", "replace_component", "use_as_baseline",
    "test_hypothesis", "activation_oracle_extension", "biomedical_agent_extension",
    "not_applicable",
]


class FeedReviewContext(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_id: str = Field(min_length=1)
    profile: ResearchProfile

    @model_validator(mode="after")
    def _keys(self) -> FeedReviewContext:
        keys = self.profile.projects
        if not self.source_id.strip() or any(not key.strip() for key in keys) or len(set(keys)) != len(keys):
            raise ValueError("feed context requires nonblank unique source/project keys")
        return self


class FeedProjectAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_id: str
    target_project: str
    assessment: Literal["applicable", "not_applicable", "unknown"]
    use_kind: ProjectUse | None
    rationale: str = Field(min_length=1)
    mechanism: str
    target_component: str
    proposed_change: str
    minimal_validation_experiment: str
    evidence_quote: str

    @model_validator(mode="after")
    def _use_contract(self) -> FeedProjectAssessment:
        proposal = (self.mechanism, self.target_component, self.proposed_change, self.minimal_validation_experiment)
        if not self.rationale.strip():
            raise ValueError("assessment requires rationale")
        if self.assessment == "applicable":
            if self.use_kind in (None, "not_applicable") or not all(value.strip() for value in (*proposal, self.evidence_quote)):
                raise ValueError("positive use requires mechanism, component, change, experiment and quote")
        elif any(proposal) or self.use_kind != ("not_applicable" if self.assessment == "not_applicable" else None):
            raise ValueError("nonpositive assessment cannot propose a use")
        if self.assessment != "unknown" and not self.evidence_quote.strip():
            raise ValueError("assessed use requires original evidence")
        return self


class _FeedWarrant(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    rationale: str = Field(min_length=1)
    evidence_quote: str = Field(min_length=1)

    @field_validator("rationale", "evidence_quote")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("rationale and quote must be nonblank")
        return value


class FeedReproduction(_FeedWarrant):
    tier: Literal["one_day", "one_week", "multi_week", "blocked", "unknown"]
    missing_inputs: list[str]


class FeedImpact(_FeedWarrant):
    research: int = Field(ge=0, le=5)
    production: int = Field(ge=0, le=5)
    personal_novelty: int = Field(ge=0, le=5)


class FeedAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    projects: list[FeedProjectAssessment]
    reproduction: FeedReproduction | None
    impact: FeedImpact | None


class FeedDigestResponse(PaperDigest):
    """Explicit feed-only response; optional failure never retries the base digest."""
    feed_assessment: FeedAssessment | None = None

    @field_validator("feed_assessment", mode="wrap")
    @classmethod
    def _optional_assessment(cls, value, handler):
        try:
            return handler(value)
        except ValidationError:
            return None


class ResearchEngineeringCard(BaseModel):
    source_id: str
    problem: str
    core_idea: str
    engineering_novelty: str
    contribution_kind: list[str] = Field(default_factory=list)
    topic_tags: list[str] = Field(default_factory=list)
    code_urls: list[str] = Field(default_factory=list)
    dataset_urls: list[str] = Field(default_factory=list)
    model_urls: list[str] = Field(default_factory=list)
    benchmark_names: list[str] = Field(default_factory=list)
    reproducibility_tier: Literal["one_day", "one_week", "multi_week", "blocked", "unknown"]
    reproducibility_rationale: str
    missing_reproduction_inputs: list[str] = Field(default_factory=list)
    project_uses: list[ProjectUse] = Field(default_factory=list)
    project_use_notes: list[str] = Field(default_factory=list)
    project_assessment_status: Literal["not_assessed", "assessed", "partial"] = "not_assessed"
    project_assessments: list[FeedProjectAssessment] = Field(default_factory=list)
    assessment_origin: Literal["ai_generated"] | None = None
    scientific_verification: Literal["unverified"] | None = None
    assessment_evidence: dict[str, dict | None] = Field(default_factory=dict)
    reproduction_assessment: FeedReproduction | None = None
    impact_assessment: FeedImpact | None = None
    research_impact: int = Field(ge=0, le=5)
    production_impact: int = Field(ge=0, le=5)
    personal_novelty: int = Field(ge=0, le=5)
    worth_reading: Literal["read", "skim", "skip", "unknown"]
    research_ideas: list[ResearchIdea] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)

    @field_validator("code_urls", "dataset_urls", "model_urls")
    @classmethod
    def _absolute_urls(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if any(urlsplit(value).scheme not in {"http", "https"} or not urlsplit(value).hostname
               for value in cleaned):
            raise ValueError("artifact URLs must be absolute HTTP(S) URLs")
        return cleaned


__all__ = [
    "TOPIC_TAXONOMY", "ResearchCandidate", "ResearchEngineeringCard",
    "ResearchFeedTriage", "ResearchIdea", "ResearchProfile",
    "parse_run_budgets",
]
