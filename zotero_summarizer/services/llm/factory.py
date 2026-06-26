"""Build a live LLM client from a provider profile, dispatching on ``type``.

``openai`` reuses ``services._adapters.build_llm`` (the OnPrem OpenAI-compatible
wrapper); ``anthropic`` builds the native ``AnthropicLLMClient``. Both return
something satisfying the ``LLMClient`` protocol (``.prompt`` / ``.pydantic_prompt``),
so call sites are provider-agnostic.

The API key is read here (services layer) from the env var named by
``provider.api_key_env`` — never stored in config. A missing key raises
``APIError(missing_api_key)`` so the manual operational-check and PUT /api/config
return a clean 400 rather than crashing.
"""
from __future__ import annotations

import os

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations.llm import LLMClient
from zotero_summarizer.models.providers import ProviderConfig, ProviderType, ResolvedStage
from zotero_summarizer.services._adapters import build_llm
from zotero_summarizer.services.llm.thinking import apply_effort_openai, effort_to_anthropic_budget


def resolve_api_key(provider: ProviderConfig) -> str:
    """Read the provider's API key from the env var named by ``api_key_env``.

    The single key-resolution point for the services layer (client building AND
    model listing). Raises ``APIError(missing_api_key)`` when unset so callers
    return a clean 400 rather than handing an empty key to the SDK.
    """
    api_key = os.getenv(provider.api_key_env, "").strip()
    if not api_key:
        raise APIError(
            error="missing_api_key",
            message=f"Environment variable {provider.api_key_env} is not set",
            status_code=400,
            details={"provider": provider.name, "api_key_env": provider.api_key_env},
        )
    return api_key


def _override_thinking(extra_body: dict | None, enable: bool) -> dict | None:
    """Return ``extra_body`` with ``chat_template_kwargs.enable_thinking`` set to
    ``enable`` — but ONLY when the provider already advertises
    ``chat_template_kwargs`` (i.e. the endpoint is a reasoning model that honors
    the flag: ollama qwen3, kather sota, vLLM). A provider with no
    ``chat_template_kwargs`` (e.g. real OpenAI) is left untouched so it never
    receives an unknown ``extra_body`` key it would reject. This is what lets
    deep_review run the DIGEST with thinking ON (quality) and the trivial
    verification calls with thinking OFF (speed) on the same provider."""
    if not extra_body or "chat_template_kwargs" not in extra_body:
        return extra_body
    ctk = dict(extra_body["chat_template_kwargs"])
    ctk["enable_thinking"] = enable
    return {**extra_body, "chat_template_kwargs": ctk}


def build_client_for_provider(
    provider: ProviderConfig, model: str, *, enable_thinking: bool | None = None
) -> LLMClient:
    """Construct an ``LLMClient`` for ``provider`` serving ``model``.

    ``enable_thinking`` (when not ``None``) forces the reasoning flag on/off for
    THIS client, overriding the provider's base ``extra_body`` — used by
    deep_review to think on the digest but not on the trivial calls. It is a
    no-op for providers that don't advertise ``chat_template_kwargs`` (see
    :func:`_override_thinking`). Raises ``APIError`` when the provider's
    ``api_key_env`` is unset or the provider ``type`` is unsupported.
    """
    api_key = resolve_api_key(provider)

    if provider.type == ProviderType.openai:
        # First fold the provider's configured thinking_effort into extra_body
        # (reasoning_effort or enable_thinking, per dialect), THEN apply any
        # per-call override on top so deep_review's digest can still force
        # thinking on for chat_template providers.
        extra_body = apply_effort_openai(provider.thinking_effort, provider.extra_body)
        if enable_thinking is not None:
            extra_body = _override_thinking(extra_body, enable_thinking)
        return build_llm(
            provider.base_url,
            model,
            api_key,
            max_tokens=provider.max_tokens,
            temperature=provider.temperature,
            extra_body=extra_body,
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
