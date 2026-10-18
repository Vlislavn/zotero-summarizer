"""Effective routing and invalidation own cache admission, not builder completion."""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Event
from types import SimpleNamespace

import pytest

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models.providers import LLMRoutingConfig
from zotero_summarizer.runtime import RuntimeState
from zotero_summarizer.services.llm import factory

BARRIER_CAP_SECONDS = float(os.getenv("ZS_TEST_BARRIER_CAP_SECONDS", "5"))
assert 0 < BARRIER_CAP_SECONDS <= 30


@pytest.fixture
def cached_runtime(monkeypatch):
    routing = LLMRoutingConfig.model_validate({
        "providers": [
            {"name": "local", "base_url": "http://127.0.0.1:9998/v1", "api_key_env": "SYNTHETIC_KEY_NAME"},
            {"name": "hosted", "type": "anthropic", "api_key_env": "SYNTHETIC_HOSTED_KEY_NAME"},
        ],
        "default": {"provider": "local", "model": "base-model"},
        "deep_review": {"provider": "hosted", "model": "review-model"},
    })
    config = SimpleNamespace(llm_enabled=True, llm_routing=routing)
    app = RuntimeState(app_state=SimpleNamespace(config=config))
    builds = []

    def stage(resolved):
        client = object()
        builds.append((resolved, None, client))
        return client

    def provider(profile, model, *, enable_thinking):
        client = object()
        builds.append(((profile, model), enable_thinking, client))
        return client

    monkeypatch.setattr(factory, "build_client_for_stage", stage)
    monkeypatch.setattr(factory, "build_client_for_provider", provider)
    return app, config, builds


def test_clean_effective_routing_reuses_each_stage_and_thinking_variant(cached_runtime):
    app, config, builds = cached_runtime
    clients = {}
    for stage in ("feed", "backlog", "deep_review"):
        for thinking in (None, False, True):
            key = stage, thinking
            clients[key] = app.resolve_stage_client(stage, enable_thinking=thinking)
            assert app.resolve_stage_client(stage, enable_thinking=thinking) is clients[key]
    assert len({id(client) for client in clients.values()}) == len(builds) == 9
    assert builds[0][0].provider.name == "local"
    assert builds[6][0].provider.name == "hosted"
    assert builds[6][0].model == "review-model"
    assert [row[1] for row in builds[:3]] == [None, False, True]
    assert "SYNTHETIC_KEY_NAME" not in repr(app._stage_clients)
    config.unrelated_display_setting = "not a model boundary"
    assert app.resolve_stage_client("feed") is clients[("feed", None)]


@pytest.mark.parametrize("field,value", [
    ("base_url", "http://127.0.0.1:9997/v1"),
    ("api_key_env", "CHANGED_SYNTHETIC_KEY_NAME"),
    ("max_tokens", 8192),
    ("temperature", 0.4),
    ("thinking_effort", "off"),
    ("structured_output", True),
    ("num_ctx", 4096),
    ("extra_body", {"chat_template_kwargs": {"enable_thinking": False}}),
])
def test_changed_effective_provider_settings_cannot_hit_old_client(cached_runtime, field, value):
    app, config, builds = cached_runtime
    first = app.resolve_stage_client("feed")
    setattr(config.llm_routing.providers[0], field, value)
    second = app.resolve_stage_client("feed")
    assert second is not first
    assert app.resolve_stage_client("feed") is second
    assert len(builds) == 2
    assert getattr(builds[0][0].provider, field) != getattr(builds[1][0].provider, field)


def test_changed_default_does_not_replace_an_unchanged_stage_override(cached_runtime):
    app, config, builds = cached_runtime
    review = app.resolve_stage_client("deep_review")
    feed = app.resolve_stage_client("feed")
    config.llm_routing.default.model = "new-default-model"
    assert app.resolve_stage_client("feed") is not feed
    assert app.resolve_stage_client("deep_review") is review
    assert len(builds) == 3


def test_invalidation_fences_an_inflight_build_even_with_unchanged_routing(cached_runtime, monkeypatch):
    app, config, _ = cached_runtime
    entered, release = Event(), Event()
    first, second = object(), object()
    calls = []

    def build(resolved):
        calls.append(resolved)
        if len(calls) == 1:
            entered.set()
            assert release.wait(BARRIER_CAP_SECONDS), "test-owned builder was not released"
            return first
        return second

    monkeypatch.setattr(factory, "build_client_for_stage", build)
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(app.resolve_stage_client, "feed")
        try:
            assert entered.wait(BARRIER_CAP_SECONDS), "test-owned builder was not entered"
            app.invalidate_stage_clients()  # Same metadata, e.g. credential replacement.
            assert app.resolve_stage_client("feed") is second
        finally:
            release.set()
        assert old.result(timeout=BARRIER_CAP_SECONDS) is first
    assert app.resolve_stage_client("feed") is second
    assert len(calls) == 2


def test_builder_uses_an_owned_snapshot_not_mutable_current_provider(cached_runtime, monkeypatch):
    app, config, _ = cached_runtime
    observed = []

    def build(resolved):
        config.llm_routing.providers[0].max_tokens = 8192
        observed.append(resolved.provider.max_tokens)
        return object()

    monkeypatch.setattr(factory, "build_client_for_stage", build)
    original = config.llm_routing.providers[0].max_tokens
    first = app.resolve_stage_client("feed")
    assert observed == [original]
    assert app.resolve_stage_client("feed") is not first
    assert observed == [original, 8192]


def test_failed_construction_is_not_cached_and_disabled_ai_never_builds(cached_runtime, monkeypatch):
    app, config, _ = cached_runtime
    calls = []
    client = object()

    def build(resolved):
        calls.append(resolved)
        if len(calls) == 1:
            raise ValueError("synthetic constructor failure")
        return client

    monkeypatch.setattr(factory, "build_client_for_stage", build)
    with pytest.raises(ValueError, match="synthetic constructor failure"):
        app.resolve_stage_client("feed")
    assert app.resolve_stage_client("feed") is client
    assert app.resolve_stage_client("feed") is client
    config.llm_enabled = False
    with pytest.raises(APIError) as error:
        app.resolve_stage_client("feed")
    assert error.value.error == "llm_disabled"
    assert len(calls) == 2


def test_processing_boundary_metadata_never_constructs_a_client(cached_runtime, monkeypatch):
    from zotero_summarizer.services.library import _related_assessment as semantic

    app, _, builds = cached_runtime
    monkeypatch.setattr(semantic, "state", lambda: app)
    monkeypatch.setattr(semantic, "offline_requested", lambda: False)
    boundary = semantic.model_boundary()
    assert boundary["provider"] == "hosted"
    assert boundary["model"] == "review-model"
    assert boundary["enabled"] is True
    assert builds == []
    assert app._stage_clients == {}


def test_consented_identity_rejects_a_different_actual_snapshot_even_if_metadata_returns_to_consent(cached_runtime, monkeypatch):
    from zotero_summarizer.services.library import _related_assessment as semantic

    app, config_b, _ = cached_runtime
    routing_a = config_b.llm_routing.model_copy(deep=True)
    routing_a.deep_review.provider, routing_a.deep_review.model = "local", "base-model"
    config_a = SimpleNamespace(llm_enabled=True, llm_routing=routing_a)
    monkeypatch.setattr(semantic, "state", lambda: app)
    monkeypatch.setattr(semantic, "offline_requested", lambda: False)
    real_boundary = semantic.model_boundary
    consent_b = real_boundary()
    reads, payloads, builds = [], [], []

    def observed_boundary():
        reads.append(True)
        if len(reads) == 1:
            observed = real_boundary()
            app.app_state = SimpleNamespace(config=config_a)
            app.invalidate_stage_clients()
            return observed
        app.app_state = SimpleNamespace(config=config_b)
        app.invalidate_stage_clients()
        return real_boundary()

    def record_private_payload(*, prompt, pydantic_model):
        payloads.append(prompt)
        return pydantic_model.model_validate({"items": [{
            "item_key": "GOOD0001", "relevance": "relevant", "preference": "compatible",
            "sentence": "Synthetic metadata relationship.", "used_context_ids": ["private-seed"],
            "seed_quote": {"field": "title", "excerpt": "Seed"},
            "candidate_quote": {"field": "title", "excerpt": "Candidate"},
        }]})

    def build(resolved):
        builds.append(resolved)
        return SimpleNamespace(pydantic_prompt=record_private_payload)

    monkeypatch.setattr(factory, "build_client_for_stage", build)
    monkeypatch.setattr(semantic, "model_boundary", observed_boundary)
    seed = {"item_key": "SEED0001", "title": "Seed", "abstract": "Seed metadata",
            "comments": {"records": [{"id": "private-seed", "text": "PRIVATE SYNTHETIC CONTEXT"}]}}
    candidate = {"item_key": "GOOD0001", "title": "Candidate", "abstract": "Candidate metadata",
                 "comments": {"records": []}}

    with pytest.raises(APIError) as error:
        semantic.assess(seed, [candidate], consent_b)

    assert error.value.error == "processing_boundary_changed"
    assert builds == payloads == []
    app.app_state = SimpleNamespace(config=config_b)
    assert real_boundary() == consent_b
