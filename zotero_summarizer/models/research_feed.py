"""Contracts for the source-neutral weekly Research Intelligence feed."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator


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
    telegram_channels: list[str] = Field(default_factory=list)
    topic_taxonomy: list[str] = Field(default_factory=lambda: sorted(TOPIC_TAXONOMY))

    @field_validator("topic_taxonomy")
    @classmethod
    def _known_topics(cls, values: list[str]) -> list[str]:
        unknown = set(values) - TOPIC_TAXONOMY
        if unknown:
            raise ValueError(f"unknown research-feed topics: {sorted(unknown)}")
        return list(dict.fromkeys(values))


class TelegramPost(BaseModel):
    """Canonical saved input only: no embedded assessments or private session fields."""

    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1]
    channel_id: int = Field(ge=-(2**63), le=2**63 - 1)
    message_id: int = Field(ge=1, le=2**63 - 1)
    username: str | None = None
    permalink: str
    text: str
    published_at: str
    updated_at: str | None = None
    urls: list[str] = Field(default_factory=list)
    skip_reason: str | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def _version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("saved Telegram input requires schema_version=1")
        return value

    @field_validator("published_at", "updated_at")
    @classmethod
    def _aware_date(cls, value: str | None) -> str | None:
        if value is not None:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.utcoffset() is None:
                raise ValueError("post dates require a timezone")
        return value

    @model_validator(mode="after")
    def _original_fields(self) -> TelegramPost:
        if self.channel_id == 0:
            raise ValueError("channel_id must be nonzero")
        if self.skip_reason is not None and not self.skip_reason.strip():
            raise ValueError("skip_reason must be nonempty when supplied")
        if not self.text.strip() and self.skip_reason is None:
            raise ValueError("empty posts require an explicit skip_reason")
        for value in [self.permalink, *self.urls]:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("post locators must be absolute HTTP(S) URLs")
        return self

    @property
    def source_id(self) -> str:
        return f"telegram:{self.channel_id}:{self.message_id}"


class TelegramNewsVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Literal["keep", "drop", "uncertain"]
    matched_interests: list[str]
    reason: str = Field(min_length=1)
    evidence_quote: str = Field(min_length=1)
    summary: str = ""

    @model_validator(mode="after")
    def _required_text(self) -> TelegramNewsVerdict:
        if not self.reason.strip() or not self.evidence_quote.strip():
            raise ValueError("verdict requires a reason and original evidence quote")
        if self.decision == "keep" and not self.summary.strip():
            raise ValueError("keep requires a summary")
        return self


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
    "parse_run_budgets", "TelegramPost", "TelegramNewsVerdict",
]
