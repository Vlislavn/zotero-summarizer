"""Contracts for the source-neutral weekly Research Intelligence feed."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


TOPIC_TAXONOMY = frozenset({
    "agents", "evaluation", "interpretability", "reinforcement-learning", "reasoning",
    "multimodal", "biology", "genomics", "tool-use", "safety", "alignment", "planning",
    "memory", "long-context", "uncertainty", "calibration", "verification",
    "hallucination-detection", "trajectory-analysis", "benchmarks",
})


class ResearchProfile(BaseModel):
    schema_version: int = 1
    themes: list[str]
    projects: list[str]
    shortlist_budget: int = Field(default=20, ge=1, le=100)
    card_budget: int = Field(default=10, ge=1, le=20)
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
    research_impact: int = Field(ge=0, le=5)
    production_impact: int = Field(ge=0, le=5)
    personal_novelty: int = Field(ge=0, le=5)
    worth_reading: Literal["read", "skim", "skip"]
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
]
