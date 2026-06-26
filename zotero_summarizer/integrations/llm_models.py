"""List the models a provider serves, for the Settings model-picker.

Two tiny read-only calls, one per provider ``type``:

- ``list_openai_models`` — HTTP ``GET {base_url}/models`` (the OpenAI-compatible
  catalogue endpoint that Ollama / vLLM / OpenRouter / OpenAI all expose),
  returning the ``data[].id`` strings.
- ``list_anthropic_models`` — the native Anthropic ``client.models.list()``.

Layering: external I/O only, so this lives in ``integrations/`` and imports
nothing from ``services/``/``api/``. The API *key itself* is passed in already
resolved (the env-var lookup is the caller's job in the services layer), exactly
like ``llm_anthropic`` takes ``api_key``.
"""
from __future__ import annotations

import httpx

# Catalogue listing is interactive (a user is waiting on the Settings page), so
# keep the timeout short — a slow/unreachable endpoint should fail fast, not
# hang the picker. The probe-style operational check uses a longer budget.
_LIST_TIMEOUT_SECS = 10.0


class ModelListError(RuntimeError):
    """A provider could not be reached / refused the model-list request.

    The lib-specific transport failure (httpx / anthropic SDK) is translated to
    this single domain error here, at the boundary that owns those libs, so the
    services layer can map it to a clean HTTP error without importing them. Carries
    a human-readable reason for the Settings model-picker to display inline."""


def list_openai_models(base_url: str, api_key: str) -> list[str]:
    """Return model ids from an OpenAI-compatible ``GET {base_url}/models``.

    Raises :class:`ModelListError` if the endpoint is unreachable, times out, or
    returns a non-2xx status (e.g. 401 bad key, 404 wrong base URL)."""
    url = f"{base_url.rstrip('/')}/models"
    try:
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_LIST_TIMEOUT_SECS,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise ModelListError(f"could not list models from {url}: {exc}") from exc
    data = resp.json().get("data", [])
    return [str(m["id"]) for m in data if isinstance(m, dict) and m.get("id")]


def list_anthropic_models(api_key: str, base_url: str | None = None) -> list[str]:
    """Return model ids from the native Anthropic models endpoint.

    Raises :class:`ModelListError` on any Anthropic API/connection failure."""
    import anthropic  # lazy: optional dependency (mirrors llm_anthropic)

    client = anthropic.Anthropic(api_key=api_key, base_url=(base_url or None))
    try:
        page = client.models.list(limit=1000)
        return [str(m.id) for m in page if getattr(m, "id", None)]
    except anthropic.APIError as exc:
        raise ModelListError(f"could not list Anthropic models: {exc}") from exc
