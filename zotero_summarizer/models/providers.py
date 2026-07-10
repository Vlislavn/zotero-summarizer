"""LLM provider registry + per-pipeline-stage model routing.

The app calls an LLM in three distinct pipeline stages — **feed** triage
(daemon/RSS scoring), **backlog** triage (the whole-backlog drain behind the
Today button) and **deep_review** (full-text quality + digest). Each stage
picks a named provider + model, falling back to a single global ``default`` when
left blank (configure once, override only where it matters).

A *provider* is a named connection profile with a ``type``: ``openai`` (any
OpenAI-compatible endpoint — OpenAI, Ollama, OpenRouter, vLLM, …) or
``anthropic`` (the native Anthropic messages API). Secrets never live here:
``api_key_env`` holds the *name* of the env var that carries the key, exactly
like the legacy ``LLMConfig.api_key_env``.

These models are pure data + lookups (no env reads, no client building) so they
stay in the ``models`` layer. Resolving a profile to a live client is the job of
``services.llm.factory``.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, model_validator


__all__ = [
    "ProviderType",
    "ProviderConfig",
    "StageModelConfig",
    "DefaultModelConfig",
    "LLMRoutingConfig",
    "ResolvedStage",
    "STAGES",
    "resolve_stage",
]

# The pipeline stages that resolve their own provider + model.
STAGES = ("feed", "backlog", "deep_review")

# Loopback hosts that mark a provider as "local" — its stage runs serially to
# protect host RAM (one big local model can't absorb concurrent inference).
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})


class ProviderType(str, Enum):
    openai = "openai"        # OpenAI-compatible chat (reuses build_llm / OnPrem)
    anthropic = "anthropic"  # native Anthropic messages API


class ProviderConfig(BaseModel):
    name: str = Field(..., min_length=1)          # registry key, e.g. "local", "remote", "claude"
    type: ProviderType = ProviderType.openai
    base_url: Optional[str] = None                # required for openai; optional for anthropic
    api_key_env: str = Field(..., min_length=1)   # env var NAME (never the secret itself)
    # Provider-specific kwargs forwarded to OpenAI-compatible endpoints as `extra_body`
    # (e.g. vLLM's `chat_template_kwargs`). Ignored by the native Anthropic adapter.
    extra_body: Optional[Dict[str, Any]] = None
    # Per-provider generation budget. Reasoning models (a remote reasoning model) need a roomy
    # budget — 16384 — or the thinking phase eats the whole allowance; chat models are
    # fine at 4096.
    max_tokens: int = Field(default=4096, ge=1)
    # OpenAI-path sampling temperature. Default 0 preserves the previously-hardcoded
    # deterministic triage behavior. Threaded into build_llm for openai-type providers;
    # the native Anthropic adapter ignores it (Opus 4.x returns 400 on temperature).
    temperature: float = Field(default=0.0, ge=0, le=2)
    # Reasoning/"thinking" effort for this provider. None = leave the provider untouched
    # (back-compat: inject nothing, deep_review's per-call override still governs). The
    # services.llm.thinking translator maps the level per backend dialect: anthropic →
    # a thinking budget; plain OpenAI reasoning models → reasoning_effort; vLLM/qwen
    # (extra_body.chat_template_kwargs) → enable_thinking on/off (graded collapses there).
    thinking_effort: Optional[Literal["off", "low", "medium", "high"]] = None
    # Use the cheaper deep-review TIER when this provider runs the `deep_review` stage:
    # smaller text cap, 1 self-consistency run, batched goal summaries. Set it on a
    # prefill-bound backend (e.g. ollama) that is too slow for the full review. This is
    # INDEPENDENT of `is_local` (which is loopback/concurrency only): MLX runs on
    # loopback yet is fast, so it stays on the full tier with this flag left False.
    lean_deep_review: bool = Field(default=False)
    # Max concurrent LLM sub-calls within a single deep review (rubric samples, goal
    # summaries). None → inherit the global triage_job_concurrency cap. Local providers
    # always get 1 regardless of this value (RAM protection). For remote reasoning
    # models (e.g. a remote vLLM endpoint) set to 3–4 so the 3 rubric runs and 6 goal calls fan
    # out concurrently without hammering the endpoint.
    max_sub_concurrency: Optional[int] = Field(default=None, ge=1)
    # Context window (tokens) for the model's KV cache. None = use the endpoint's own
    # default (SAFE for remote endpoints — vLLM-style endpoints size their own window). For a
    # LOCAL ollama provider, set this so a long deep-review prompt FITS: ollama's own
    # default is tiny (~2–4k) and silently truncates a 60k-char prompt. Auto-derived for
    # local providers in ``_common._derive_local_num_ctx`` (``derive_num_ctx``); forwarded
    # to ollama as extra_body.num_ctx via LiteLLM. CAUTION:
    # a larger num_ctx grows the KV-cache RAM allocation — on a memory-constrained Mac
    # keep it just-large-enough, not the model's max. Override: ZS_PROVIDER_NUM_CTX.
    num_ctx: Optional[int] = Field(default=None, ge=512)
    # ollama ``keep_alive`` (seconds): how long a LOCAL model stays loaded after a call
    # (warm → no cold-start on the next call). None = ollama default (~5m). This is a LATENCY
    # knob, NOT a correctness one (unlike num_ctx), so it is left OFF by default and is NOT
    # auto-derived: pinning a model resident grows steady-state RAM, which on a memory-
    # constrained Mac is the OPPOSITE of safe (see the 2026-06-12 watchdog panic). Set it
    # explicitly only on a provider where the warm-start win outweighs the RAM hold, then
    # measure. Forwarded as ``extra_body.keep_alive`` (same path as num_ctx). -1 = forever.
    # PROVISIONAL (wired, not measured): see docs/decisions.md ADR-D7 / GAP §G6.
    keep_alive: Optional[int] = Field(default=None)
    # Decoder-level structured output: send ``response_format: {type: json_schema, strict: true}`` so the
    # ENDPOINT constrains decoding to the schema (a real decoder-level guarantee, NOT a prompt
    # hint). MEASURED trade-off on a vLLM endpoint (2026-06-27, GPT-OSS-120B, 1 paper): strict forces
    # enum/range compliance AND reasoning survives (unlike Ollama native format+think, #10929), BUT
    # field completeness DROPS (0.62 vs 1.00 prompt-level — the model satisfices with empty-string
    # defaults under strict guidance) AND needs a roomy max_tokens (8192 truncated→16384 worked;
    # thinking + full JSON must fit). So: enable for models PRONE to invalid JSON (small/Qwen3-on-
    # ollama, where the guarantee outweighs the completeness cost); leave OFF for well-behaved
    # reasoning models (GPT-OSS produces valid JSON already → prompt-level wins on completeness).
    # Prompt-level PydanticOutputParser + retry stays the backstop when False. Per-provider config
    # (like lean_deep_review / max_sub_concurrency), not a ZS_ env knob.
    structured_output: bool = Field(default=False)

    @model_validator(mode="after")
    def _require_base_url_for_openai(self) -> "ProviderConfig":
        if self.type == ProviderType.openai and not (self.base_url or "").strip():
            raise ValueError(f"provider {self.name!r} is type=openai and requires base_url")
        return self

    @property
    def thinking_on(self) -> bool:
        """Whether this provider's reasoning is on. ``thinking_effort`` of ``None``
        (unset) or a graded level means ON; only an explicit ``"off"`` disables it.
        The deep-review DIGEST reads this to decide ``enable_thinking`` — so the
        digest reasons by default (measured NEEDED) and a provider set to
        ``thinking_effort: off`` opts it out, without a separate toggle."""
        return self.thinking_effort != "off"

    @property
    def is_local(self) -> bool:
        """True when this provider points at a loopback host — its triage stage
        runs serially to protect host RAM. ``anthropic`` is always cloud → False.
        For ``openai`` we parse ``base_url``'s host; a missing/unparseable host
        is treated as remote (the safe default — never silently serialise a
        genuinely remote endpoint)."""
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

    provider: Optional[str] = None    # provider NAME (must exist in the registry)
    model: Optional[str] = None


class DefaultModelConfig(BaseModel):
    """The fallback provider+model every stage inherits unless it overrides."""

    provider: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)


class LLMRoutingConfig(BaseModel):
    """Top-level routing block (``goals.yaml: llm_routing``).

    Optional in the file: when absent, ``GoalsConfig`` synthesizes it from the
    legacy flat ``llm:`` block so existing configs keep working unchanged.
    """

    providers: List[ProviderConfig] = Field(default_factory=list)
    default: DefaultModelConfig
    feed: StageModelConfig = Field(default_factory=StageModelConfig)
    backlog: StageModelConfig = Field(default_factory=StageModelConfig)
    deep_review: StageModelConfig = Field(default_factory=StageModelConfig)

    @model_validator(mode="after")
    def _validate_refs(self) -> "LLMRoutingConfig":
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
    return ResolvedStage(stage=stage, provider=routing.provider_by_name(provider_name), model=model)
