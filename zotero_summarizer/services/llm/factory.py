"""Build provider-agnostic LLM clients with centrally resolved credentials."""

from __future__ import annotations

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations.llm import LLMClient
from zotero_summarizer.models.providers import (
    LLMCapability,
    ProviderConfig,
    ProviderType,
    ResolvedStage,
    resolve_stage,
)
from zotero_summarizer.runtime import RuntimeState
from zotero_summarizer.services._adapters import build_llm
from zotero_summarizer.services._common import settings
from zotero_summarizer.services.llm.credentials import get_api_key
from zotero_summarizer.services.llm.thinking import (
    apply_effort_openai,
    effort_to_anthropic_budget,
)


def resolve_stage_client(
    runtime_state: RuntimeState, stage: str, *, enable_thinking: bool | None = None,
    expected_identity: str | None = None,
) -> LLMClient:
    """Resolve an owned stage snapshot and lazily cache its client on the runtime.

    ``enable_thinking=False`` disables declared chat thinking or minimizes active
    three-level reasoning to low; unsupported options remain untouched.
    ``expected_identity`` binds consent before cache access or credential lookup.
    Provider failures surface at use time, never during startup.
    """
    with runtime_state._stage_clients_lock:
        config = runtime_state.app_state.config
        if not config.llm_enabled:
            raise APIError(
                "llm_disabled",
                "AI features are disabled; enable AI in Settings to run this action",
                status_code=409,
            )
        resolved = resolve_stage(config.llm_routing, stage).model_copy(deep=True)
        identity = resolved.fingerprint()
        if expected_identity is not None and identity != expected_identity:
            raise APIError("processing_boundary_changed", "Resolved stage differs from the requested processing boundary", status_code=409)
        cache_key = (stage, enable_thinking, identity)
        generation = runtime_state._stage_clients_generation
        cached = runtime_state._stage_clients.get(cache_key)
        if cached is not None:
            return cached

    # Provider/credential construction must not block config invalidation.
    if enable_thinking is None:
        client = build_client_for_stage(resolved)
    else:
        client = build_client_for_provider(
            resolved.provider, resolved.model, enable_thinking=enable_thinking
        )
    with runtime_state._stage_clients_lock:
        if generation == runtime_state._stage_clients_generation:
            return runtime_state._stage_clients.setdefault(cache_key, client)
    return client


def resolve_api_key(provider: ProviderConfig) -> str:
    """Resolve a key from OS storage or the legacy environment fallback."""
    resolved = get_api_key(provider.api_key_env)
    if not resolved:
        if provider.is_local:
            return "local"
        raise APIError(
            error="missing_api_key",
            message=f"No saved credential or environment value found for {provider.api_key_env}",
            status_code=400,
            details={"provider": provider.name, "api_key_env": provider.api_key_env},
        )
    return resolved[0]


def _override_thinking(extra_body: dict | None, enable: bool) -> dict | None:
    """Disable declared chat thinking or minimize active three-level reasoning."""
    if not extra_body:
        return extra_body
    if "chat_template_kwargs" not in extra_body:
        return {**extra_body, "reasoning_effort": "low"} if not enable and extra_body.get("reasoning_effort") in {"low", "medium", "high"} else extra_body
    ctk = dict(extra_body["chat_template_kwargs"])
    ctk["enable_thinking"] = enable
    return {**extra_body, "chat_template_kwargs": ctk}


def build_client_for_provider(
    provider: ProviderConfig, model: str, *, enable_thinking: bool | None = None,
    verification: bool = False,
) -> LLMClient:
    """Construct the live client for one provider/model pair."""
    if LLMCapability.text not in provider.available_capabilities:
        raise APIError("unsupported_capabilities", "Selected provider does not declare text support", status_code=422)
    verification_options = {}
    if verification:
        from zotero_summarizer.services.setup.load_admission import refused_local_load
        from zotero_summarizer.services.setup.load_attempt import SetupAttempt, current_attempt

        if refusal := refused_local_load(provider, model):
            raise APIError(refusal["reason_code"], refusal["detail"], status_code=409,
                           details={"attempted": False, "hardware_safe": "unknown"})
        attempt = current_attempt() or SetupAttempt()
        request_owner = attempt.bind_client(settings(), provider.model_copy(deep=True), model)
        verification_options = {"verification": True, "before_request": attempt.require_active,
                                "request_owner": request_owner}
    api_key = resolve_api_key(provider)

    if provider.type == ProviderType.openai:
        extra_body = apply_effort_openai(provider.thinking_effort, provider.extra_body)
        if provider.num_ctx is not None:
            extra_body = {**(extra_body or {}), "num_ctx": provider.num_ctx}
        if provider.keep_alive is not None:
            extra_body = {**(extra_body or {}), "keep_alive": provider.keep_alive}
        if enable_thinking is not None:
            extra_body = _override_thinking(extra_body, enable_thinking)
        return build_llm(
            provider.base_url,
            model,
            api_key,
            max_tokens=provider.max_tokens,
            temperature=provider.temperature,
            extra_body=extra_body,
            request_timeout_seconds=settings().summary_timeout_seconds,
            structured_output=provider.structured_output,
            **verification_options,
        )

    if provider.type == ProviderType.anthropic:
        from zotero_summarizer.integrations.llm_anthropic import AnthropicLLMClient

        return AnthropicLLMClient(
            model=model,
            api_key=api_key,
            max_tokens=provider.max_tokens,
            base_url=provider.base_url,
            thinking_budget=effort_to_anthropic_budget(provider.thinking_effort),
            **({**verification_options, "timeout": settings().summary_timeout_seconds} if verification else {}),
        )

    raise APIError(
        error="unknown_provider_type",
        message=f"Unsupported provider type {provider.type!r}",
        status_code=400,
        details={"provider": provider.name},
    )


def build_client_for_stage(resolved: ResolvedStage, *, verification: bool = False) -> LLMClient:
    """Build the client for a stage already resolved to provider+model."""
    return build_client_for_provider(resolved.provider, resolved.model,
                                     **({"verification": True} if verification else {}))
