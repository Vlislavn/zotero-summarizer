"""Provider profiles and default/per-stage model routing data."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, model_validator

__all__ = [
    "STAGES",
    "DefaultModelConfig",
    "LLMRoutingConfig",
    "ProviderConfig",
    "ProviderType",
    "ResolvedStage",
    "StageModelConfig",
    "resolve_stage",
]

STAGES = ("feed", "backlog", "deep_review")

# Loopback hosts that mark a provider as "local" — its stage runs serially to
# protect host RAM (one big local model can't absorb concurrent inference).
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})


class ProviderType(str, Enum):
    openai = "openai"  # OpenAI-compatible chat (reuses build_llm / OnPrem)
    anthropic = "anthropic"  # native Anthropic messages API


class ProviderConfig(BaseModel):
    name: str = Field(
        ..., min_length=1
    )  # registry key, e.g. "local", "remote", "claude"
    type: ProviderType = ProviderType.openai
    base_url: str | None = None  # required for openai; optional for anthropic
    api_key_env: str = Field(
        ..., min_length=1
    )  # env var NAME (never the secret itself)
    extra_body: dict[str, Any] | None = None
    max_tokens: int = Field(default=4096, ge=1)
    temperature: float = Field(default=0.0, ge=0, le=2)
    thinking_effort: Literal["off", "low", "medium", "high"] | None = None
    lean_deep_review: bool = Field(default=False)
    max_sub_concurrency: int | None = Field(default=None, ge=1)
    num_ctx: int | None = Field(default=None, ge=512)
    keep_alive: int | None = Field(default=None)
    structured_output: bool = Field(default=False)

    @model_validator(mode="after")
    def _require_base_url_for_openai(self) -> ProviderConfig:
        if self.type == ProviderType.openai and not (self.base_url or "").strip():
            raise ValueError(
                f"provider {self.name!r} is type=openai and requires base_url"
            )
        return self

    @property
    def thinking_on(self) -> bool:
        """Only an explicit ``off`` disables reasoning."""
        return self.thinking_effort != "off"

    @property
    def is_local(self) -> bool:
        """Whether the endpoint is loopback and must use local concurrency limits."""
        if self.type is ProviderType.anthropic:
            return False
        raw = (self.base_url or "").strip()
        if not raw:
            return False
        # base_url may omit a scheme ("localhost:11434/v1"); urlsplit needs one
        # to populate .hostname, so prepend a dummy scheme when absent.
        if "://" not in raw:
            raw = "http://" + raw
        host = (urlsplit(raw).hostname or "").lower()
        return host in _LOCAL_HOSTS


class StageModelConfig(BaseModel):
    """Per-stage selection. Either field left ``None`` inherits the global default."""

    provider: str | None = None  # provider NAME (must exist in the registry)
    model: str | None = None


class DefaultModelConfig(BaseModel):
    """The fallback provider+model every stage inherits unless it overrides."""

    provider: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)


class LLMRoutingConfig(BaseModel):
    """Top-level routing block (``goals.yaml: llm_routing``).

    Optional in the file: when absent, ``GoalsConfig`` synthesizes it from the
    legacy flat ``llm:`` block so existing configs keep working unchanged.
    """

    providers: list[ProviderConfig] = Field(default_factory=list)
    default: DefaultModelConfig
    feed: StageModelConfig = Field(default_factory=StageModelConfig)
    backlog: StageModelConfig = Field(default_factory=StageModelConfig)
    deep_review: StageModelConfig = Field(default_factory=StageModelConfig)

    @model_validator(mode="after")
    def _validate_refs(self) -> LLMRoutingConfig:
        names = [p.name for p in self.providers]
        if len(set(names)) != len(names):
            raise ValueError("provider names must be unique")
        known = set(names)
        if self.default.provider not in known:
            raise ValueError(
                f"default.provider {self.default.provider!r} is not in the providers registry"
            )
        for stage in STAGES:
            sm: StageModelConfig = getattr(self, stage)
            if sm.provider is not None and sm.provider not in known:
                raise ValueError(
                    f"{stage}.provider {sm.provider!r} is not in the providers registry"
                )
        return self

    def provider_by_name(self, name: str) -> ProviderConfig:
        for provider in self.providers:
            if provider.name == name:
                return provider
        raise KeyError(f"unknown provider {name!r}")


class ResolvedStage(BaseModel):
    """A stage's effective provider profile + model after inheritance."""

    stage: str
    provider: ProviderConfig
    model: str


def resolve_stage(routing: LLMRoutingConfig, stage: str) -> ResolvedStage:
    """Resolve ``stage`` (one of :data:`STAGES`) to its effective provider+model,
    inheriting any unset field from ``routing.default``."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")
    selection: StageModelConfig = getattr(routing, stage)
    provider_name = selection.provider or routing.default.provider
    model = selection.model or routing.default.model
    return ResolvedStage(
        stage=stage, provider=routing.provider_by_name(provider_name), model=model
    )
