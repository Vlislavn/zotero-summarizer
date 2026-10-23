"""Declared route admission through the real feed chain; no live inference."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from zotero_summarizer.models import GoalsConfig, RefinedSummary, SummarizeRequest, TriageResult
from zotero_summarizer.models.providers import (
    DefaultModelConfig,
    LLMRoutingConfig,
    ProviderConfig,
    ProviderType,
    ResolvedStage,
    StageModelConfig,
    resolve_stage,
)
from zotero_summarizer.runtime import RuntimeState
from zotero_summarizer.services.llm import factory
from zotero_summarizer.services.triage import summarization


def _provider(**overrides):
    return ProviderConfig(**{
        "name": "selected", "base_url": "https://selected.example/v1",
        "api_key_env": "SYNTHETIC_KEY", **overrides,
    })


def _routing(provider, **overrides):
    return LLMRoutingConfig(
        providers=[provider, _provider(name="other")],
        default=DefaultModelConfig(provider="other", model="default-model"),
        **{"feed": StageModelConfig(provider=provider.name, model="feed-model"), **overrides},
    )


def _wire_feed(monkeypatch, provider):
    from zotero_summarizer.integrations import llm_anthropic

    config = GoalsConfig(
        relevance_scale={i: str(i) for i in range(1, 6)},
        llm={"draft_model": "legacy", "refine_model": "legacy",
             "api_base": "https://legacy.example/v1", "api_key_env": "LEGACY_KEY"},
        llm_routing=_routing(provider),
    )
    app = RuntimeState(app_state=SimpleNamespace(config=config))
    client = Mock()
    client.prompt.return_value = RefinedSummary(executive_summary="Synthetic overview").model_dump_json()
    client.pydantic_prompt.return_value = TriageResult(score=3, rationale="Synthetic assessment")
    key = Mock(return_value=("synthetic-secret", "test"))
    openai = Mock(return_value=client)
    anthropic = Mock(return_value=client)
    corpus = Mock(return_value=summarization.corpus.empty_corpus_match_result())
    monkeypatch.setattr(summarization, "state", lambda: app)
    monkeypatch.setattr(summarization.corpus, "run_corpus_match", corpus)
    monkeypatch.setattr(factory, "get_api_key", key)
    monkeypatch.setattr(factory, "build_llm", openai)
    monkeypatch.setattr(llm_anthropic, "AnthropicLLMClient", anthropic)
    monkeypatch.setattr(factory, "settings", lambda: SimpleNamespace(summary_timeout_seconds=17))
    return SimpleNamespace(app=app, client=client, key=key, openai=openai,
                           anthropic=anthropic, corpus=corpus)


def _run_feed():
    return summarization.run_abstract_pipeline(SummarizeRequest(
        title="Synthetic route fixture", abstract="Synthetic abstract without artifacts.",
    ))


@pytest.mark.parametrize("kind", [ProviderType.openai, ProviderType.anthropic])
@pytest.mark.parametrize("capabilities", [[], ["native_json_schema"]], ids=["empty", "no-text"])
def test_production_feed_rejects_incompatible_before_credentials_or_transport(
    monkeypatch, kind, capabilities,
):
    io = _wire_feed(monkeypatch, _provider(type=kind, capabilities=capabilities))

    with pytest.raises(ValueError) as failure:
        _run_feed()

    assert str(failure.value) == "provider 'selected' lacks required capabilities: text"
    io.key.assert_not_called()
    io.openai.assert_not_called()
    io.anthropic.assert_not_called()
    io.client.prompt.assert_not_called()
    io.client.pydantic_prompt.assert_not_called()
    io.corpus.assert_not_called()
    assert io.app._stage_clients == {}


@pytest.mark.parametrize("kind", [ProviderType.openai, ProviderType.anthropic])
@pytest.mark.parametrize("declaration", [{}, {"capabilities": None}, {"capabilities": ["text"]},
                                        {"capabilities": ["text", "native_json_schema"]}],
                         ids=["legacy", "null", "text", "declared-schema-no-wire-toggle"])
def test_production_feed_compatible_and_legacy_preserve_route_and_wire(monkeypatch, kind, declaration):
    provider = _provider(type=kind, max_tokens=8192, temperature=0.25, **declaration)
    io = _wire_feed(monkeypatch, provider)

    first, second = _run_feed(), _run_feed()

    assert first.executive_summary == second.executive_summary == "Synthetic overview"
    assert first.should_deep_read == second.should_deep_read == "No"
    assert io.client.prompt.call_count == io.client.pydantic_prompt.call_count == 2
    io.key.assert_called_once_with("SYNTHETIC_KEY")
    assert io.app._stage_clients == {("feed", None): io.client}
    if kind == ProviderType.openai:
        io.anthropic.assert_not_called()
        io.openai.assert_called_once_with(
            "https://selected.example/v1", "feed-model", "synthetic-secret",
            max_tokens=8192, temperature=0.25, extra_body=None,
            request_timeout_seconds=17, structured_output=False,
        )
    else:
        io.openai.assert_not_called()
        io.anthropic.assert_called_once_with(
            model="feed-model", api_key="synthetic-secret", max_tokens=8192,
            base_url="https://selected.example/v1", thinking_budget=None,
        )


def test_feed_capability_change_observed_after_existing_cache_invalidation(monkeypatch):
    io = _wire_feed(monkeypatch, _provider(capabilities=["text"]))
    _run_feed()
    io.app.app_state.config.llm_routing = _routing(_provider(capabilities=[]))
    io.app.invalidate_stage_clients()
    io.key.reset_mock()
    io.openai.reset_mock()
    io.client.reset_mock()

    with pytest.raises(ValueError) as failure:
        _run_feed()

    assert str(failure.value) == "provider 'selected' lacks required capabilities: text"
    io.key.assert_not_called()
    io.openai.assert_not_called()
    io.client.prompt.assert_not_called()
    assert io.app._stage_clients == {}


def test_resolution_retains_feed_requirements_and_independent_inheritance():
    provider = _provider(capabilities=["text"])
    routing = _routing(provider, feed=StageModelConfig(provider="selected"))

    resolved = resolve_stage(routing, "feed")

    assert resolved.provider is provider
    assert resolved.model == "default-model"  # no cross-provider model guessing
    assert resolved.required_capabilities == frozenset({"text"})
    for stage in ("backlog", "deep_review"):
        other = resolve_stage(routing, stage)
        assert other.provider.name == "other" and other.model == "default-model"
        assert other.required_capabilities == frozenset()


def test_stage_factory_rechecks_retained_requirement_before_credentials(monkeypatch):
    provider = _provider(capabilities=["text"])
    resolved = resolve_stage(_routing(provider), "feed")
    provider.capabilities = frozenset()
    credentials = Mock()
    transport = Mock()
    monkeypatch.setattr(factory, "resolve_api_key", credentials)
    monkeypatch.setattr(factory, "build_llm", transport)

    with pytest.raises(ValueError) as failure:
        factory.build_client_for_stage(resolved)

    assert str(failure.value) == "provider 'selected' lacks required capabilities: text"
    credentials.assert_not_called()
    transport.assert_not_called()


@pytest.mark.parametrize("kind,structured,declaration,expected", [
    ("openai", False, None, {"text"}),
    ("openai", True, None, {"text"}),
    ("openai", True, ["text"], {"text"}),
    ("openai", False, ["text", "native_json_schema"], {"text"}),
    ("openai", True, ["text", "native_json_schema"], {"text", "native_json_schema"}),
    ("anthropic", False, None, {"text"}),
    ("anthropic", True, ["text"], {"text"}),
    ("anthropic", False, ["text", "native_json_schema"], {"text", "native_json_schema"}),
    ("openai", True, [], set()),
])
def test_native_schema_requires_declaration_and_enabled_adapter(kind, structured, declaration, expected):
    from zotero_summarizer.models.providers import LLMCapability

    provider = _provider(type=kind, structured_output=structured, capabilities=declaration)

    assert provider.available_capabilities == frozenset(expected)
    resolved = ResolvedStage(stage="feed", provider=provider, model="feed-model",
                             required_capabilities=frozenset({LLMCapability.native_json_schema}))
    if "native_json_schema" in expected:
        assert resolved.require_capabilities() is None
    else:
        with pytest.raises(ValueError) as failure:
            resolved.require_capabilities()
        assert str(failure.value) == "provider 'selected' lacks required capabilities: native_json_schema"


def test_native_requirement_is_rejected_before_factory_credentials(monkeypatch):
    from zotero_summarizer.models.providers import LLMCapability

    provider = _provider(structured_output=True)  # opt-in wire alone is not a declaration
    resolved = ResolvedStage(stage="feed", provider=provider, model="feed-model",
                             required_capabilities=frozenset({LLMCapability.native_json_schema}))
    credentials = Mock()
    transport = Mock()
    monkeypatch.setattr(factory, "resolve_api_key", credentials)
    monkeypatch.setattr(factory, "build_llm", transport)

    with pytest.raises(ValueError) as failure:
        factory.build_client_for_stage(resolved)

    assert str(failure.value) == "provider 'selected' lacks required capabilities: native_json_schema"
    credentials.assert_not_called()
    transport.assert_not_called()


@pytest.mark.parametrize("kind", [ProviderType.openai, ProviderType.anthropic])
def test_declared_native_schema_uses_existing_factory_path(monkeypatch, kind):
    from zotero_summarizer.models.providers import LLMCapability

    provider = _provider(type=kind, structured_output=True,
                         capabilities=["text", "native_json_schema"])
    io = _wire_feed(monkeypatch, provider)
    resolved = ResolvedStage(stage="feed", provider=provider, model="feed-model",
                             required_capabilities=frozenset({LLMCapability.native_json_schema}))

    assert factory.build_client_for_stage(resolved) is io.client

    io.key.assert_called_once_with("SYNTHETIC_KEY")
    if kind == ProviderType.openai:
        io.anthropic.assert_not_called()
        io.openai.assert_called_once_with(
            "https://selected.example/v1", "feed-model", "synthetic-secret",
            max_tokens=4096, temperature=0.0, extra_body=None,
            request_timeout_seconds=17, structured_output=True,
        )
    else:
        io.openai.assert_not_called()
        io.anthropic.assert_called_once_with(
            model="feed-model", api_key="synthetic-secret", max_tokens=4096,
            base_url="https://selected.example/v1", thinking_budget=None,
        )


@pytest.mark.parametrize("entry", ["_probe_stage", "_probe_stage_bounded", "_reach_stage",
                                   "_reach_stage_bounded", "check_routing_stages"])
def test_unsupported_feed_diagnostics_are_structured_without_io(monkeypatch, entry):
    import asyncio
    from zotero_summarizer.services.llm import model_list, operational_check

    io = _wire_feed(monkeypatch, _provider(capabilities=[]))
    listing, probe = Mock(), Mock(return_value={"status": "operational", "detail": ""})
    monkeypatch.setattr(model_list, "list_models_for_provider", listing)
    routing = io.app.app_state.config.llm_routing
    if entry == "check_routing_stages":
        report = asyncio.run(operational_check.check_routing_stages(
            routing, stages=("feed",), probe=probe,
        ))
        assert report["status"] == "degraded"
        assert len(report["stages"]) == 1
        row = report["stages"][0]
    else:
        result = getattr(operational_check, entry)(routing, "feed")
        row = asyncio.run(result) if entry.endswith("bounded") else result

    assert row == {
        "stage": "feed", "provider": "selected", "type": "openai", "model": "feed-model",
        "base_url": "https://selected.example/v1", "status": "fail", "reachable": False,
        "reason_code": "unsupported_capabilities",
        "detail": "provider 'selected' lacks required capabilities: text",
        "required_capabilities": ["text"], "declared_capabilities": [],
    }
    io.key.assert_not_called()
    io.openai.assert_not_called()
    io.anthropic.assert_not_called()
    listing.assert_not_called()
    probe.assert_not_called()


@pytest.mark.parametrize("inference", [False, True], ids=["reachability", "inference"])
def test_disabled_diagnostics_do_not_become_capability_errors(monkeypatch, inference):
    from zotero_summarizer.services.llm import operational_check

    io = _wire_feed(monkeypatch, _provider(capabilities=[]))

    report = operational_check._disabled_stages(io.app.app_state.config.llm_routing,
                                               inference=inference)

    assert report["status"] == "disabled"
    row = report["stages"][0]
    assert row["stage"] == "feed" and row["enabled"] is False
    assert row["detail"] == "AI features are disabled by choice"
    assert row["reason_code"] == "llm_disabled"
    if inference:
        assert row["status"] == "disabled" and "reachable" not in row
    else:
        assert row["reachable"] is False and "status" not in row
    io.key.assert_not_called()
    io.openai.assert_not_called()
    io.anthropic.assert_not_called()


def test_diagnostics_do_not_swallow_unrelated_resolution_errors(monkeypatch):
    from zotero_summarizer.services.llm import operational_check

    problem = ValueError("Synthetic unrelated selection failure")
    monkeypatch.setattr(operational_check, "resolve_stage", Mock(side_effect=problem))

    with pytest.raises(ValueError) as failure:
        operational_check._stage_skeleton(_routing(_provider()), "feed")

    assert failure.value is problem


@pytest.mark.parametrize("unknown", ["vision", "tool_calls", "reasoning_controls", "context"])
def test_unimplemented_capabilities_are_not_admitted(unknown):
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as failure:
        _provider(capabilities=[unknown])

    assert failure.value.errors()[0]["type"] == "enum"


def test_legacy_provider_and_resolved_stage_serialization_are_unchanged():
    expected = {
        "name": "selected", "type": "openai", "base_url": "https://selected.example/v1",
        "api_key_env": "SYNTHETIC_KEY", "extra_body": None, "max_tokens": 4096,
        "temperature": 0.0, "thinking_effort": None, "lean_deep_review": False,
        "max_sub_concurrency": None, "num_ctx": None, "keep_alive": None,
        "structured_output": False,
    }
    provider = _provider()
    resolved = resolve_stage(_routing(provider, deep_review=StageModelConfig(provider="selected")),
                             "deep_review")

    assert provider.model_dump(mode="json") == expected
    assert "capabilities" not in provider.model_dump(mode="python")
    assert ProviderConfig.model_validate_json(provider.model_dump_json()).model_dump(mode="json") == expected
    assert resolved.model_dump(mode="json") == {
        "stage": "deep_review", "provider": expected, "model": "default-model",
    }
    assert resolved.model_dump(mode="python")["provider"] == provider.model_dump(mode="python")
    routing_dump = _routing(provider).model_dump(mode="json")
    assert routing_dump["providers"][0] == expected


@pytest.mark.parametrize("declaration", [[], ["text"], ["text", "native_json_schema"]])
def test_explicit_declaration_survives_nested_json_round_trip(declaration):
    routing = _routing(_provider(capabilities=declaration))

    payload = routing.model_dump_json()
    restored = LLMRoutingConfig.model_validate_json(payload)

    assert restored == routing
    assert restored.providers[0].capabilities == frozenset(declaration)
    assert set(restored.model_dump(mode="json")["providers"][0]["capabilities"]) == set(declaration)
