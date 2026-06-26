"""Model discovery for the Settings model-picker: list a provider's served model
ids (OpenAI-compatible /models or the Anthropic list), with transport failures
mapped to clean API errors. Split out of test_provider_routing.py to keep each
file's single responsibility (and under the 500-LOC limit)."""
from __future__ import annotations

import asyncio

import pytest

from zotero_summarizer.models.providers import ProviderConfig, ProviderType


def test_list_openai_models_parses_data_ids(monkeypatch):
    from zotero_summarizer.integrations import llm_models

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": "m2"}, {"id": "m1"}, {"no_id": True}]}

    captured = {}

    def _fake_get(url, headers, timeout):
        captured.update(url=url, headers=headers, timeout=timeout)
        return _Resp()

    monkeypatch.setattr(llm_models.httpx, "get", _fake_get)
    ids = llm_models.list_openai_models("http://localhost:11434/v1/", "secret")
    # base_url trailing slash normalized; auth header carries the key; rows
    # without an id are skipped.
    assert captured["url"] == "http://localhost:11434/v1/models"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert ids == ["m2", "m1"]  # parse order preserved (service sorts/dedupes)


def test_list_models_for_provider_openai_sorts_and_dedupes(monkeypatch):
    from zotero_summarizer.services.llm import model_list
    from zotero_summarizer.integrations import llm_models

    monkeypatch.setenv("LOCAL_KEY", "secret")
    seen = {}
    monkeypatch.setattr(
        llm_models, "list_openai_models",
        lambda base_url, api_key: seen.update(base_url=base_url, api_key=api_key) or ["b", "a", "a"],
    )
    provider = ProviderConfig(name="local", base_url="http://h/v1", api_key_env="LOCAL_KEY")
    out = model_list.list_models_for_provider(provider)
    assert out == ["a", "b"]  # sorted + de-duplicated
    assert seen == {"base_url": "http://h/v1", "api_key": "secret"}


def test_list_openai_models_raises_model_list_error_on_transport_failure(monkeypatch):
    import httpx

    from zotero_summarizer.integrations import llm_models

    def _boom(url, headers, timeout):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(llm_models.httpx, "get", _boom)
    with pytest.raises(llm_models.ModelListError) as ei:
        llm_models.list_openai_models("http://localhost:11434/v1", "secret")
    assert "Connection refused" in str(ei.value)


def test_list_models_for_provider_maps_unreachable_to_502(monkeypatch):
    from zotero_summarizer.api.errors import APIError
    from zotero_summarizer.integrations import llm_models
    from zotero_summarizer.services.llm import model_list

    monkeypatch.setenv("LOCAL_KEY", "secret")

    def _boom(base_url, api_key):
        raise llm_models.ModelListError("could not list models from http://h/v1/models: refused")

    monkeypatch.setattr(llm_models, "list_openai_models", _boom)
    provider = ProviderConfig(name="local", base_url="http://h/v1", api_key_env="LOCAL_KEY")
    with pytest.raises(APIError) as ei:
        model_list.list_models_for_provider(provider)
    # A bad URL/down endpoint surfaces as a clean 502 with the reason, not a 500.
    assert ei.value.error == "provider_unreachable" and ei.value.status_code == 502
    assert "refused" in ei.value.message


def test_list_models_for_provider_anthropic_dispatch(monkeypatch):
    from zotero_summarizer.services.llm import model_list
    from zotero_summarizer.integrations import llm_models

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    seen = {}
    monkeypatch.setattr(
        llm_models, "list_anthropic_models",
        lambda api_key, base_url: seen.update(api_key=api_key, base_url=base_url)
        or ["claude-opus-4-8", "claude-haiku-4-5"],
    )
    provider = ProviderConfig(name="claude", type=ProviderType.anthropic, api_key_env="ANTHROPIC_API_KEY")
    out = model_list.list_models_for_provider(provider)
    assert out == ["claude-haiku-4-5", "claude-opus-4-8"]
    assert seen == {"api_key": "sk-test", "base_url": None}


def test_list_models_for_provider_missing_key_raises_apierror(monkeypatch):
    from zotero_summarizer.api.errors import APIError
    from zotero_summarizer.services.llm import model_list

    monkeypatch.delenv("LOCAL_KEY", raising=False)
    provider = ProviderConfig(name="local", base_url="http://h/v1", api_key_env="LOCAL_KEY")
    with pytest.raises(APIError) as ei:
        model_list.list_models_for_provider(provider)
    assert ei.value.error == "missing_api_key" and ei.value.status_code == 400


def test_llm_models_route_returns_provider_shape(monkeypatch):
    from zotero_summarizer.api.routes import llm as llm_route
    from zotero_summarizer.services.llm import model_list

    monkeypatch.setattr(model_list, "list_models_for_provider", lambda provider: ["a", "b"])
    provider = ProviderConfig(name="claude", type=ProviderType.anthropic, api_key_env="ANTHROPIC_API_KEY")
    result = asyncio.run(llm_route.list_provider_models(provider))
    assert result == {"provider": "claude", "type": "anthropic", "models": ["a", "b"]}
