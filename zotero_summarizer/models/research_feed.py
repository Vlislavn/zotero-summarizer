"""Contracts for the source-neutral weekly Research Intelligence feed."""
from __future__ import annotations

from pydantic import ConfigDict
from pydantic import model_validator

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


class TelegramTarget(BaseModel):
    """Explicit public peer selection; never an invite, user, or personal inbox."""

    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["broadcast", "group", "topic"]
    username: str
    message_id: int | None = Field(default=None, ge=1, le=2**31 - 1)

    @model_validator(mode="before")
    @classmethod
    def _link(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        name = data.get("username")
        if isinstance(name, str) and name.strip().startswith(("https://", "http://")):
            link = urlsplit(name.strip())
            parts = link.path.strip("/").split("/")
            if (link.scheme != "https" or link.netloc != "t.me" or link.query or link.fragment
                    or len(parts) not in {1, 2}):
                raise ValueError("use a public Telegram username or t.me link")
            name = parts[0]
            if len(parts) == 2:
                if not parts[1].isascii() or not parts[1].isdigit():
                    raise ValueError("invalid Telegram message link")
                number = int(parts[1])
                if data.get("message_id") not in (None, number):
                    raise ValueError("Telegram message link differs from selection")
                data["message_id"] = number
        data["username"] = normalize_telegram_username(name)
        if data["username"] in {"me", "self", "c", "joinchat", "s"}:
            raise ValueError("select a public channel or discussion group")
        return data

    @model_validator(mode="after")
    def _scope(self):
        if self.kind == "topic" and self.message_id is None:
            raise ValueError("forum topic requires its root message ID")
        if self.kind == "group" and self.message_id is not None:
            raise ValueError("choose topic for a scoped discussion link")
        return self

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.username}:{self.message_id or 0}"


class TelegramModelSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)

    @field_validator("provider", "model")
    @classmethod
    def _nonblank(cls, value):
        if not value.strip():
            raise ValueError("explicit Telegram provider/model required")
        return value.strip()


class TelegramLimits(BaseModel):
    """Operational work caps, not hardware or model capacity certification."""

    model_config = ConfigDict(extra="forbid", strict=True)
    sources: int = Field(default=30, ge=1, le=30)
    posts_per_channel: int = Field(default=3, ge=1, le=3)
    replies_per_thread: int = Field(default=300, ge=1, le=300)
    messages: int = Field(default=300, ge=1, le=300)
    prompt_characters: int = Field(default=24000, ge=1000, le=24000)
    context_tokens: int = Field(default=32768, ge=2048, le=32768)
    output_tokens: int = Field(default=2048, ge=512, le=4096)


class TelegramSourceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sources: list[TelegramTarget] = Field(max_length=30)
    model: TelegramModelSelection | None = None
    limits: TelegramLimits = Field(default_factory=TelegramLimits)

    @model_validator(mode="after")
    def _unique(self):
        if len({target.key for target in self.sources}) != len(self.sources):
            raise ValueError("duplicate Telegram sources")  # the 30-source cap is the field's max_length
        return self


class ResearchProfile(BaseModel):
    schema_version: Literal[1] = 1
    themes: list[str]
    projects: list[str]
    shortlist_budget: _ShortlistBudget = 20
    card_budget: _CardBudget = 10
    telegram_channels: list[str] = Field(default_factory=list)
    telegram_targets: list[TelegramTarget] = Field(default_factory=list, max_length=30)
    telegram_model: TelegramModelSelection | None = None
    telegram_limits: TelegramLimits = Field(default_factory=TelegramLimits)
    topic_taxonomy: list[str] = Field(default_factory=lambda: sorted(TOPIC_TAXONOMY))

    @field_validator("topic_taxonomy")
    @classmethod
    def _known_topics(cls, values: list[str]) -> list[str]:
        unknown = set(values) - TOPIC_TAXONOMY
        if unknown:
            raise ValueError(f"unknown research-feed topics: {sorted(unknown)}")
        return list(dict.fromkeys(values))


LiveTelegramChannelId = Annotated[
    int, Field(strict=True, ge=-(2**63), lt=-1000000000000),
]

def validate_live_telegram_channel_id(value: int) -> int:
    """Validate a marked live channel ID without converting saved identities."""
    try:
        return TypeAdapter(LiveTelegramChannelId).validate_python(value)
    except ValueError:
        raise ValueError("invalid live Telegram channel ID") from None

def normalize_telegram_username(value: str) -> str:
    """Application binding key, not proof of Telegram API/public-channel validity."""
    if not isinstance(value, str):
        raise ValueError("invalid Telegram username")
    bare = value.strip().removeprefix("@")
    if (not 1 <= len(bare) <= 32 or not bare.isascii()
            or not all(char.isalnum() or char == "_" for char in bare)
            or not any(char.isalpha() for char in bare)):
        raise ValueError("invalid Telegram username")
    return bare.lower()

class TelegramPost(BaseModel):
    """Canonical saved input only: no embedded assessments or private session fields."""

    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1, 2]
    channel_id: int = Field(ge=-(2**63), le=2**63 - 1)
    message_id: int = Field(ge=1, le=2**63 - 1)
    username: str | None = None
    permalink: str
    text: str
    published_at: str
    updated_at: str | None = None
    urls: list[str] = Field(default_factory=list)
    skip_reason: str | None = None
    # schema_version 2 additions (stored in post_json; no SQL migration):
    fwd_from: list[int] | None = None
    reply_to_id: int | None = Field(default=None, ge=1, le=2**31 - 1)  # a channel post replying to another post
    replies_count: int = Field(default=0, ge=0, le=2**31 - 1)

    @field_validator("schema_version", mode="before")
    @classmethod
    def _version(cls, value):
        if type(value) is not int or value not in (1, 2):
            raise ValueError("saved Telegram input requires schema_version 1 or 2")
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
        if self.fwd_from is not None:
            if self.schema_version < 2:
                raise ValueError("fwd_from requires schema_version 2")
            origin_channel, origin_message = (self.fwd_from + [None, None])[:2]
            if (len(self.fwd_from) != 2 or type(origin_channel) is not int or origin_channel == 0
                    or type(origin_message) is not int or origin_message < 1):
                raise ValueError("fwd_from must be [nonzero channel_id, message_id>=1]")
        if self.replies_count and self.schema_version < 2:
            raise ValueError("replies_count requires schema_version 2")
        for value in [self.permalink, *self.urls]:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("post locators must be absolute HTTP(S) URLs")
        return self

    @property
    def source_id(self) -> str:
        return f"telegram:{self.channel_id}:{self.message_id}"

class TelegramPage(BaseModel):
    """One requested page, not a claim of complete channel history."""

    model_config = ConfigDict(extra="forbid", strict=True)
    posts: tuple[TelegramPost, ...] = Field(max_length=100)
    complete: bool

class ResolvedTelegramChannel(BaseModel):
    """Application resolution result, without SDK or session metadata."""

    model_config = ConfigDict(extra="forbid", strict=True)
    channel_id: LiveTelegramChannelId
    public_broadcast: bool

class TelegramTransportError(Exception):
    """Bounded injected-transport failure; never carries an upstream message."""

    def __init__(self, code: str, *, retry_seconds: int | None = None):
        if type(code) is not str or code not in {
            "missing_session", "session_unauthorized", "revoked_session",
            "network_error", "channel_unavailable", "flood_wait",
        }:
            raise ValueError("invalid Telegram transport error code")
        if code == "flood_wait":
            if type(retry_seconds) is not int or retry_seconds <= 0:
                raise ValueError("invalid Telegram retry seconds")
        elif retry_seconds is not None:
            raise ValueError("retry seconds require Telegram flood_wait")
        self.code = code
        self.retry_seconds = retry_seconds
        super().__init__(code)


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
        if self.assessment == "unknown" and not self.evidence_quote.strip():
            self.evidence_quote = ""
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
    "FeedReviewContext", "FeedProjectAssessment", "FeedReproduction", "FeedImpact",
    "FeedAssessment", "FeedDigestResponse", "parse_run_budgets", "TelegramPost",
]
