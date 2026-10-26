"""Default factory preservation through the existing deep_review facade only."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_deep_review import _fake_state
from zotero_summarizer.models import PaperDigest
from zotero_summarizer.models import providers
from zotero_summarizer.services.library import deep_review
from zotero_summarizer.services.setup.bootstrap import _default_goals_config
from zotero_summarizer.services.zotero import zotero as zotero_svc


KEYS = ["reader", "config", "extractor", "quality_enabled", "llm", "llm_digest", "llm_map",
        "prestige_scores", "prestige_floor_value", "lean_tier", "sub_concurrency", "response_format",
        "provenance", "_provider"]


@pytest.fixture
def context_io(monkeypatch):
    config = _default_goals_config()
    reader, extractor = object(), object()
    clients = [object(), object(), object()]
    provider = SimpleNamespace(name="owned-provider", thinking_on=True, lean_deep_review=True, structured_output=True)
    app = _fake_state(config, extractor=extractor, reader=reader)
    app.resolve_stage_provider = Mock(return_value=provider)
    client = Mock(side_effect=clients)
    monkeypatch.setattr(deep_review.factory, "resolve_stage_client", client)
    prestige = {"OWNED001": {"prestige": 1}}
    state, library = Mock(return_value=app), Mock(return_value=reader)
    loader, concurrency = Mock(return_value=(prestige, 2.0)), Mock(return_value=7)
    real_resolve, real_schema = providers.resolve_stage, deep_review.quality_review.build_response_format
    routing, schema = Mock(wraps=real_resolve), Mock(wraps=real_schema)
    trace = Mock()
    for name, callback in (("state", state), ("reader", library), ("resolve", routing),
                           ("provider", app.resolve_stage_provider), ("prestige", loader),
                           ("client", client), ("concurrency", concurrency), ("schema", schema)):
        trace.attach_mock(callback, name)
    monkeypatch.setattr(deep_review, "get_state", state)
    monkeypatch.setattr(zotero_svc, "get_library_reader", library)
    monkeypatch.setattr(providers, "resolve_stage", routing)
    monkeypatch.setattr(deep_review, "_load_prestige_context", loader)
    monkeypatch.setattr(deep_review, "deep_review_sub_concurrency", concurrency)
    monkeypatch.setattr(deep_review.quality_review, "build_response_format", schema)
    return SimpleNamespace(config=config, app=app, reader=reader, extractor=extractor, clients=clients,
                           provider=provider, prestige=prestige, state=state, library=library, loader=loader, client=client,
                           concurrency=concurrency, routing=routing, schema=schema, trace=trace,
                           real_resolve=real_resolve, real_schema=real_schema)


@pytest.mark.parametrize("strategy", ["rank", "prefix", "map_reduce"])
@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("reader_mode", ["default", "override", "falsey"])
@pytest.mark.parametrize("thinking,lean", [(False, False), (True, False), (True, True)])
def test_factory_preserves_exact_objects_keys_routing_and_callback_order(context_io, strategy, structured, reader_mode, thinking, lean):
    io = context_io
    io.config.quality_review.chunk_strategy = strategy
    io.provider.structured_output, io.provider.thinking_on, io.provider.lean_deep_review = structured, thinking, lean
    override = object() if reader_mode == "override" else (False if reader_mode == "falsey" else None)
    expected_route = io.real_resolve(io.config.llm_routing, "deep_review")
    ctx = deep_review._build_ctx(reader=override)
    assert list(ctx) == KEYS and "interpretation_schema" not in ctx and "interpretation_out" not in ctx
    assert ctx["reader"] is (override if reader_mode == "override" else io.reader)
    assert ctx["config"] is io.config and ctx["extractor"] is io.extractor
    assert ctx["quality_enabled"] is bool(io.config.quality_review.enabled and io.extractor is not None)
    assert ctx["llm"] is io.clients[0] and ctx["llm_digest"] is io.clients[1]
    assert ctx["llm_map"] is (io.clients[2] if strategy == "map_reduce" else None)
    assert ctx["prestige_scores"] is io.prestige and ctx["prestige_floor_value"] == 2.0
    assert ctx["lean_tier"] is lean and ctx["sub_concurrency"] == 7 and ctx["_provider"] is io.provider
    assert ctx["provenance"] == {"provider": "owned-provider", "model": expected_route.model,
                                  "prompt_schema_version": deep_review.REVIEW_CONTRACT_VERSION}
    io.state.assert_called_once_with()
    io.routing.assert_called_once_with(io.config.llm_routing, "deep_review")
    io.app.resolve_stage_provider.assert_called_once_with("deep_review")
    io.loader.assert_called_once_with()
    io.concurrency.assert_called_once_with(io.provider)
    calls = io.client.call_args_list
    assert calls[0].args == (io.app, "deep_review") and calls[0].kwargs == {"enable_thinking": False}
    assert calls[1].args == (io.app, "deep_review") and calls[1].kwargs == {"enable_thinking": thinking}
    if strategy == "map_reduce":
        assert len(calls) == 3 and calls[2].args == (io.app, "feed") and calls[2].kwargs == {"enable_thinking": False}
    else:
        assert len(calls) == 2
    if reader_mode == "override":
        io.library.assert_not_called()
    else:
        io.library.assert_called_once_with(io.app)
    expected_order = ["state"] + ([] if reader_mode == "override" else ["reader"])
    expected_order += ["resolve", "provider", "prestige", "client", "client"]
    expected_order += (["client"] if strategy == "map_reduce" else []) + ["concurrency"]
    if structured:
        expected_order += ["schema"]
        io.schema.assert_called_once_with(PaperDigest)
        assert ctx["response_format"] == io.real_schema(PaperDigest)
    else:
        io.schema.assert_not_called()
        assert ctx["response_format"] is None
    assert [call[0] for call in io.trace.mock_calls] == expected_order


@pytest.mark.parametrize("enabled,has_extractor", [(False, True), (True, False), (True, True)])
def test_quality_admission_does_not_change_default_client_resolution(context_io, enabled, has_extractor):
    io = context_io
    io.config.quality_review.enabled = enabled
    io.config.quality_review.chunk_strategy = "rank"
    io.app.pdf_extractor = io.extractor if has_extractor else None
    ctx = deep_review._build_ctx()
    assert ctx["quality_enabled"] is (enabled and has_extractor)
    assert ctx["extractor"] is io.app.pdf_extractor
    assert io.client.call_count == 2
    assert ctx["llm"] is io.clients[0] and ctx["llm_digest"] is io.clients[1]


@pytest.mark.parametrize("structured", [False, True])
def test_facade_values_patched_before_invocation_and_conditional_builder_lookup(context_io, monkeypatch, structured):
    io = context_io
    io.provider.structured_output = structured
    io.config.quality_review.chunk_strategy = "rank"
    model, response = object(), {"owned_schema": True}
    builder = Mock(return_value=response)
    module = SimpleNamespace(build_response_format=builder) if structured else SimpleNamespace()
    monkeypatch.setattr(deep_review, "quality_review", module)
    monkeypatch.setattr(deep_review, "PaperDigest", model)
    monkeypatch.setattr(deep_review, "REVIEW_CONTRACT_VERSION", 99)
    ctx = deep_review._build_ctx()
    assert ctx["provenance"]["prompt_schema_version"] == 99
    assert ctx["response_format"] is (response if structured else None)
    if structured:
        builder.assert_called_once_with(model)
    else:
        assert not hasattr(module, "build_response_format")
    io.schema.assert_not_called()
    io.state.assert_called_once_with()


def test_provider_optional_flags_and_name_keep_existing_defaults(context_io):
    io = context_io
    io.config.quality_review.chunk_strategy = "rank"
    provider = SimpleNamespace(thinking_on=False)
    io.app.resolve_stage_provider.return_value = provider
    expected_route = io.real_resolve(io.config.llm_routing, "deep_review")
    ctx = deep_review._build_ctx()
    assert ctx["_provider"] is provider and ctx["lean_tier"] is False and ctx["response_format"] is None
    assert ctx["provenance"]["provider"] == expected_route.provider.name
    assert io.client.call_args_list[1].kwargs == {"enable_thinking": False}
    io.concurrency.assert_called_once_with(provider)
    io.schema.assert_not_called()


@pytest.mark.parametrize("failure,expected", [
    ("state", ["state"]),
    ("reader", ["state", "reader"]),
    ("provider", ["state", "reader", "resolve", "provider"]),
    ("prestige", ["state", "reader", "resolve", "provider", "prestige"]),
    ("generic_client", ["state", "reader", "resolve", "provider", "prestige", "client"]),
    ("digest_client", ["state", "reader", "resolve", "provider", "prestige", "client", "client"]),
    ("map_client", ["state", "reader", "resolve", "provider", "prestige", "client", "client", "client"]),
    ("concurrency", ["state", "reader", "resolve", "provider", "prestige", "client", "client", "client", "concurrency"]),
    ("schema", ["state", "reader", "resolve", "provider", "prestige", "client", "client", "client", "concurrency", "schema"]),
])
def test_factory_failure_propagates_at_original_callback_position(context_io, failure, expected):
    io = context_io
    io.config.quality_review.chunk_strategy = "map_reduce"
    error = RuntimeError("owned factory failure")
    if failure.endswith("client"):
        replies = list(io.clients)
        replies[{"generic_client": 0, "digest_client": 1, "map_client": 2}[failure]] = error
        io.client.side_effect = replies
    else:
        callback = io.app.resolve_stage_provider if failure == "provider" else getattr(io, {"reader": "library", "prestige": "loader"}.get(failure, failure))
        callback.side_effect = error
    with pytest.raises(RuntimeError) as raised:
        deep_review._build_ctx()
    assert raised.value is error
    assert [call[0] for call in io.trace.mock_calls] == expected
