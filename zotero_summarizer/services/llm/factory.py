"""Build provider-agnostic LLM clients with centrally resolved credentials."""

from __future__ import annotations

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations.llm import LLMClient
from zotero_summarizer.models.providers import (
    ProviderConfig,
    ProviderType,
    ResolvedStage,
)
from zotero_summarizer.services._adapters import build_llm
from zotero_summarizer.services._common import settings
from zotero_summarizer.services.llm.credentials import get_api_key
from zotero_summarizer.services.llm.thinking import (
    apply_effort_openai,
    effort_to_anthropic_budget,
)


def resolve_api_key(provider: ProviderConfig) -> str:
    """Resolve a key from OS storage or the legacy environment fallback."""
    resolved = get_api_key(provider.api_key_env)
    if not resolved and provider.is_local and provider.api_key_env == "OLLAMA_API_KEY":
        return "local"
    if not resolved:
        raise APIError(
            error="missing_api_key",
            message=f"No saved credential or environment value found for {provider.api_key_env}",
            status_code=400,
            details={"provider": provider.name, "api_key_env": provider.api_key_env},
        )
    return resolved[0]


def _override_thinking(extra_body: dict | None, enable: bool) -> dict | None:
    """Override thinking only for providers that advertise the compatible key."""
    if not extra_body or "chat_template_kwargs" not in extra_body:
        return extra_body
    ctk = dict(extra_body["chat_template_kwargs"])
    ctk["enable_thinking"] = enable
    return {**extra_body, "chat_template_kwargs": ctk}


def build_client_for_provider(
    provider: ProviderConfig, model: str, *, enable_thinking: bool | None = None
) -> LLMClient:
    """Construct the live client for one provider/model pair."""
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
        )

    if provider.type == ProviderType.anthropic:
        from zotero_summarizer.integrations.llm_anthropic import AnthropicLLMClient

        return AnthropicLLMClient(
            model=model,
            api_key=api_key,
            max_tokens=provider.max_tokens,
            base_url=provider.base_url,
            thinking_budget=effort_to_anthropic_budget(provider.thinking_effort),
        )

    raise APIError(
        error="unknown_provider_type",
        message=f"Unsupported provider type {provider.type!r}",
        status_code=400,
        details={"provider": provider.name},
    )


def build_client_for_stage(resolved: ResolvedStage) -> LLMClient:
    """Build the client for a stage already resolved to provider+model."""
    return build_client_for_provider(resolved.provider, resolved.model)
