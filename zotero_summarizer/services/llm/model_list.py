"""List the models a configured provider serves (Settings model-picker).

Resolves the provider's API key (env var named by ``api_key_env``) and dispatches
on ``type`` to the matching ``integrations.llm_models`` call, returning a sorted,
de-duplicated list of model ids. Surfaced at ``POST /api/admin/llm-models``.

Unlike the operational check, this takes the provider profile from the *request*
(not the saved config), so the user can pick a model while still editing the
provider — no "save first" round-trip.
"""
from __future__ import annotations

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations import llm_models
from zotero_summarizer.models.providers import ProviderConfig, ProviderType
from zotero_summarizer.services.llm.factory import resolve_api_key


def list_models_for_provider(provider: ProviderConfig) -> list[str]:
    """Return the sorted, unique model ids ``provider`` serves.

    Raises ``APIError(missing_api_key)`` when the key env var is unset,
    ``APIError(unknown_provider_type)`` for an unsupported type, and
    ``APIError(provider_unreachable, 502)`` when the provider can't be reached or
    rejects the request — the picker shows that reason inline rather than a bare
    500, so the user can fix the URL/key/endpoint.
    """
    api_key = resolve_api_key(provider)

    try:
        if provider.type == ProviderType.openai:
            # ProviderConfig's validator guarantees base_url for openai providers.
            ids = llm_models.list_openai_models(provider.base_url or "", api_key)
        elif provider.type == ProviderType.anthropic:
            ids = llm_models.list_anthropic_models(api_key, provider.base_url)
        else:
            raise APIError(
                error="unknown_provider_type",
                message=f"Unsupported provider type {provider.type!r}",
                status_code=400,
                details={"provider": provider.name},
            )
    except llm_models.ModelListError as exc:
        raise APIError(
            error="provider_unreachable",
            message=str(exc),
            status_code=502,
            details={"provider": provider.name},
        ) from exc

    return sorted(set(ids))
