"""Compatibility metadata entry: custom secret destinations use task approval too."""
from __future__ import annotations

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations import llm_models
from zotero_summarizer.models.providers import ProviderConfig, ProviderType
from zotero_summarizer.services.llm import connection_approval, connection_state
from zotero_summarizer.services.llm.factory import resolve_api_key


def _discovery_api_key(provider: ProviderConfig) -> str | None:
    if provider.auth_mode == 'none':
        return None
    if provider.is_local:
        return 'local'  # metadata only; never send the stored local inference key
    connection_approval.require_approval(provider)
    return resolve_api_key(provider)


def list_models_for_provider(provider: ProviderConfig) -> list[str]:
    """Approval refusal is needs-approval, not a failed endpoint/model judgment."""
    options = {}
    if connection_approval.requires_approval(provider):
        # Capture auth under the same owner as app credential/config/profile writes.
        with connection_state.reading():
            api_key = _discovery_api_key(provider)
            revision = connection_state.revision()
        options = {'owned': True, 'before_send': lambda _request: connection_state.require_revision(revision)}
    else:
        api_key = _discovery_api_key(provider)
    try:
        if provider.type == ProviderType.openai:
            ids = llm_models.list_openai_models(provider.base_url or '', api_key, **options)
        elif provider.type == ProviderType.anthropic:
            ids = llm_models.list_anthropic_models(api_key, provider.base_url, **options)
        else:
            raise APIError('unknown_provider_type', 'Unsupported provider type', status_code=400)
    except llm_models.ModelListError as exc:
        raise APIError('provider_unreachable', str(exc), status_code=502,
                       details={'provider': provider.name}) from exc
    return sorted(set(ids))
