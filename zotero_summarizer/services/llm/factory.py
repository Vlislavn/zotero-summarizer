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


def build_client_for_provider(provider: ProviderConfig, model: str) -> LLMClient:
    """Construct an ``LLMClient`` for ``provider`` serving ``model``.

    Raises ``APIError`` when the provider's ``api_key_env`` is unset or the
    provider ``type`` is unsupported.
    """
    api_key = resolve_api_key(provider)

    if provider.type == ProviderType.openai:
        return build_llm(
            provider.base_url,
            model,
            api_key,
            max_tokens=provider.max_tokens,
            extra_body=provider.extra_body,
        )

    if provider.type == ProviderType.anthropic:
        from zotero_summarizer.integrations.llm_anthropic import AnthropicLLMClient

        return AnthropicLLMClient(
            model=model,
            api_key=api_key,
            max_tokens=provider.max_tokens,
            base_url=provider.base_url,
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
