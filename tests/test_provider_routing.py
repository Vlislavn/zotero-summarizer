"""Per-stage LLM provider routing: schema + legacy migration, resolution,
factory dispatch, and the manual operational check. Pure (no OnPrem/network)."""
from __future__ import annotations

import asyncio
import types

import pytest

from zotero_summarizer.models.providers import (
    DefaultModelConfig,
    LLMRoutingConfig,
    ProviderConfig,
    ProviderType,
    StageModelConfig,
    resolve_stage,
)


def _routing(**stage_overrides) -> LLMRoutingConfig:
    return LLMRoutingConfig(
        providers=[
            ProviderConfig(name="local", base_url="http://localhost/v1", api_key_env="LOCAL_KEY"),
            ProviderConfig(name="claude", type=ProviderType.anthropic, api_key_env="ANTHROPIC_API_KEY"),
        ],
        default=DefaultModelConfig(provider="local", model="base-model"),
        **stage_overrides,
    )


# --- schema validation -----------------------------------------------------

def test_openai_provider_requires_base_url():
    with pytest.raises(ValueError, match="requires base_url"):
        ProviderConfig(name="x", type=ProviderType.openai, api_key_env="K")


def test_anthropic_provider_needs_no_base_url():
    p = ProviderConfig(name="c", type=ProviderType.anthropic, api_key_env="K")
    assert p.base_url is None


def test_temperature_defaults_zero_and_enforces_bounds():
    p = ProviderConfig(name="p", base_url="u", api_key_env="K")
    assert p.temperature == 0.0  # deterministic-triage default preserved
    assert ProviderConfig(name="p", base_url="u", api_key_env="K", temperature=0.7).temperature == 0.7
    with pytest.raises(ValueError):
        ProviderConfig(name="p", base_url="u", api_key_env="K", temperature=-0.1)
    with pytest.raises(ValueError):
        ProviderConfig(name="p", base_url="u", api_key_env="K", temperature=2.5)


def test_thinking_effort_defaults_none_and_accepts_levels():
    assert ProviderConfig(name="p", base_url="u", api_key_env="K").thinking_effort is None
    for level in ("off", "low", "medium", "high"):
        assert ProviderConfig(name="p", base_url="u", api_key_env="K", thinking_effort=level).thinking_effort == level
    with pytest.raises(ValueError):
        ProviderConfig(name="p", base_url="u", api_key_env="K", thinking_effort="extreme")


def test_routing_rejects_duplicate_provider_names():
    with pytest.raises(ValueError, match="unique"):
        LLMRoutingConfig(
            providers=[
                ProviderConfig(name="dup", base_url="u", api_key_env="K"),
                ProviderConfig(name="dup", base_url="u", api_key_env="K"),
            ],
            default=DefaultModelConfig(provider="dup", model="m"),
        )


def test_routing_rejects_dangling_default_and_stage_refs():
    with pytest.raises(ValueError, match="default.provider"):
        LLMRoutingConfig(
            providers=[ProviderConfig(name="local", base_url="u", api_key_env="K")],
            default=DefaultModelConfig(provider="ghost", model="m"),
        )
    with pytest.raises(ValueError, match="backlog.provider"):
        LLMRoutingConfig(
            providers=[ProviderConfig(name="local", base_url="u", api_key_env="K")],
            default=DefaultModelConfig(provider="local", model="m"),
            backlog=StageModelConfig(provider="ghost"),
        )


# --- local detection + conditional concurrency -----------------------------

def test_provider_is_local_detects_loopback():
    def mk(url):
        return ProviderConfig(name="p", base_url=url, api_key_env="K")
    assert mk("http://localhost:11434/v1").is_local
    assert mk("http://127.0.0.1:8080/v1").is_local
    assert mk("http://0.0.0.0:1234").is_local
    assert mk("http://[::1]:8000/v1").is_local
    assert mk("localhost:11434/v1").is_local  # scheme-less still resolves
    assert not mk("https://api.kather.ai/v1").is_local
    assert not mk("https://openrouter.ai/api/v1").is_local
    assert not mk("https://api.openai.com/v1").is_local
    # anthropic is always cloud, even with no base_url
    assert not ProviderConfig(name="c", type=ProviderType.anthropic, api_key_env="K").is_local


def test_lean_deep_review_defaults_false_and_is_independent_of_is_local():
    """The deep-review TIER flag defaults off and is orthogonal to is_local: a
    loopback provider (the MLX shape) is local but NOT lean unless explicitly
    flagged — this is the fix for the 2026-06-15 mis-tiering (loopback ≠ lean)."""
    mlx = ProviderConfig(name="mlx", base_url="http://127.0.0.1:8080/v1", api_key_env="K")
    assert mlx.is_local and mlx.lean_deep_review is False  # loopback but full tier
    ollama = ProviderConfig(
        name="default", base_url="http://localhost:11434/v1", api_key_env="K",
        lean_deep_review=True,
    )
    assert ollama.is_local and ollama.lean_deep_review is True
    # round-trips through the routing config + resolve_stage (deep_review inherits default)
    routing = LLMRoutingConfig(
        providers=[ollama], default=DefaultModelConfig(provider="default", model="qwen3:8b"),
    )
    assert resolve_stage(routing, "deep_review").provider.lean_deep_review is True


def test_effective_llm_concurrency_local_vs_remote(monkeypatch):
    import zotero_summarizer.services._common as common
    monkeypatch.setattr(common, "settings", lambda: types.SimpleNamespace(triage_job_concurrency=4))
    local = ProviderConfig(name="mlx", base_url="http://127.0.0.1:8080/v1", api_key_env="K")
    remote = ProviderConfig(name="k", base_url="https://api.kather.ai/v1", api_key_env="K")
    assert common.effective_llm_concurrency(local, 10) == 1     # local → serial
    assert common.effective_llm_concurrency(remote, 10) == 4    # remote → configured cap
    assert common.effective_llm_concurrency(remote, 2) == 2     # never more than the work
    assert common.effective_llm_concurrency(remote, 0) == 1     # floor at 1
    assert common.effective_llm_concurrency(None, 10) == 4      # defensive: None → remote


# --- resolution / inheritance ---------------------------------------------

def test_stage_inherits_default_when_unset():
    r = _routing()
    for stage in ("feed", "backlog", "deep_review"):
        resolved = resolve_stage(r, stage)
        assert resolved.provider.name == "local" and resolved.model == "base-model"


def test_stage_overrides_provider_and_model_independently():
    r = _routing(
        backlog=StageModelConfig(provider="claude", model="claude-opus-4-8"),
        deep_review=StageModelConfig(model="other-model"),  # provider inherited
    )
    backlog = resolve_stage(r, "backlog")
    assert backlog.provider.name == "claude" and backlog.model == "claude-opus-4-8"
    deep = resolve_stage(r, "deep_review")
    assert deep.provider.name == "local" and deep.model == "other-model"


def test_resolve_rejects_unknown_stage():
    with pytest.raises(ValueError, match="unknown stage"):
        resolve_stage(_routing(), "nope")


# --- legacy migration on GoalsConfig ---------------------------------------

def _minimal_goals(**llm_extra) -> dict:
    llm = {"draft_model": "d", "refine_model": "r", "api_base": "http://h/v1", "api_key_env": "OPENAI_API_KEY"}
    llm.update(llm_extra)
    return {
        "research_goals": ["g"], "triage_criteria": ["c"], "summary_structure": ["s"],
        "relevance_scale": {1: "a", 2: "b", 3: "c", 4: "d", 5: "e"},
        "llm": llm,
    }


def test_legacy_config_synthesizes_routing_default():
    from zotero_summarizer.models import GoalsConfig

    cfg = GoalsConfig.model_validate(_minimal_goals())
    r = cfg.llm_routing
    assert r is not None
    assert [p.name for p in r.providers] == ["default"]
    assert r.providers[0].base_url == "http://h/v1" and r.providers[0].api_key_env == "OPENAI_API_KEY"
    assert r.default.provider == "default" and r.default.model == "r"
    # round-trip is idempotent (re-validating the dump keeps one provider)
    again = GoalsConfig.model_validate(cfg.model_dump(mode="python"))
    assert [p.name for p in again.llm_routing.providers] == ["default"]


def test_explicit_routing_is_preserved_over_legacy_block():
    from zotero_summarizer.models import GoalsConfig

    payload = _minimal_goals()
    payload["llm_routing"] = {
        "providers": [{"name": "p1", "base_url": "http://p1/v1", "api_key_env": "K1"}],
        "default": {"provider": "p1", "model": "m1"},
    }
    cfg = GoalsConfig.model_validate(payload)
    assert [p.name for p in cfg.llm_routing.providers] == ["p1"]
    assert cfg.llm_routing.default.model == "m1"


# --- config persistence (PUT /api/config) ----------------------------------

def test_config_round_trips_provider_type_enum_through_yaml(tmp_path):
    """Regression: persisting a config with an anthropic provider must not choke
    on the ProviderType enum. ``write_config_atomic`` runs ``yaml.safe_dump``,
    which raises RepresenterError on enum objects left by mode="python"; the
    persisted payload must be JSON-mode so the enum becomes its string value."""
    from zotero_summarizer.models import GoalsConfig
    from zotero_summarizer.services._common import read_config, write_config_atomic

    payload = _minimal_goals()
    payload["llm_routing"] = {
        "providers": [
            {"name": "local", "base_url": "http://h/v1", "api_key_env": "K"},
            {"name": "claude", "type": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"},
        ],
        "default": {"provider": "local", "model": "m1"},
    }
    cfg = GoalsConfig.model_validate(payload)
    dumped = cfg.model_dump(mode="json")
    # JSON mode coerces the enum to a plain string (what yaml.safe_dump needs).
    assert dumped["llm_routing"]["providers"][1]["type"] == "anthropic"

    out_path = tmp_path / "goals.yaml"
    write_config_atomic(out_path, dumped)  # would raise RepresenterError pre-fix
    again = read_config(out_path)
    by_name = {p.name: p for p in again.llm_routing.providers}
    assert by_name["claude"].type is ProviderType.anthropic


def test_config_round_trips_temperature_and_thinking_effort(tmp_path):
    """A provider's temperature + thinking_effort must survive PUT /api/config's
    dump → yaml → reload cycle so the Settings knobs actually persist."""
    from zotero_summarizer.models import GoalsConfig
    from zotero_summarizer.services._common import read_config, write_config_atomic

    payload = _minimal_goals()
    payload["llm_routing"] = {
        "providers": [
            {"name": "local", "base_url": "http://h/v1", "api_key_env": "K",
             "temperature": 0.7, "thinking_effort": "high"},
        ],
        "default": {"provider": "local", "model": "m1"},
    }
    cfg = GoalsConfig.model_validate(payload)
    out_path = tmp_path / "goals.yaml"
    write_config_atomic(out_path, cfg.model_dump(mode="json"))
    again = read_config(out_path)
    p = again.llm_routing.providers[0]
    assert p.temperature == 0.7 and p.thinking_effort == "high"


# --- factory dispatch ------------------------------------------------------

def test_factory_openai_reuses_build_llm(monkeypatch):
    from zotero_summarizer.services.llm import factory

    monkeypatch.setenv("LOCAL_KEY", "secret")
    captured = {}
    monkeypatch.setattr(
        factory, "build_llm",
        lambda url, model, key, max_tokens, temperature, extra_body: captured.update(
            url=url, model=model, key=key, max_tokens=max_tokens,
            temperature=temperature, extra_body=extra_body) or "OPENAI_CLIENT",
    )
    # extra_body carries provider-specific kwargs (e.g. an MLX/vLLM model served
    # with reasoning disabled). With thinking_effort unset it must reach build_llm
    # untouched; temperature defaults to 0 (deterministic triage).
    extra = {"chat_template_kwargs": {"enable_thinking": False}}
    provider = ProviderConfig(
        name="mlx", base_url="http://localhost:8080/v1", api_key_env="LOCAL_KEY",
        max_tokens=8192, extra_body=extra,
    )
    client = factory.build_client_for_provider(provider, "m")
    assert client == "OPENAI_CLIENT"
    assert captured == {
        "url": "http://localhost:8080/v1", "model": "m", "key": "secret",
        "max_tokens": 8192, "temperature": 0.0, "extra_body": extra,
    }


def test_factory_openai_threads_temperature_and_effort(monkeypatch):
    # A per-provider temperature + thinking_effort must reach build_llm: temperature
    # directly, effort folded into extra_body (plain dialect → reasoning_effort).
    from zotero_summarizer.services.llm import factory

    monkeypatch.setenv("LOCAL_KEY", "secret")
    captured = {}
    monkeypatch.setattr(
        factory, "build_llm",
        lambda url, model, key, max_tokens, temperature, extra_body: captured.update(
            temperature=temperature, extra_body=extra_body) or "C",
    )
    provider = ProviderConfig(
        name="oa", base_url="https://api.openai.com/v1", api_key_env="LOCAL_KEY",
        temperature=0.5, thinking_effort="high",
    )
    factory.build_client_for_provider(provider, "o3")
    assert captured["temperature"] == 0.5
    assert captured["extra_body"] == {"reasoning_effort": "high"}


def test_factory_anthropic_threads_thinking_budget(monkeypatch):
    from zotero_summarizer.services.llm import factory
    from zotero_summarizer.integrations import llm_anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    seen = {}

    class _StubAnthropic:
        def __init__(self, *, model, api_key, max_tokens, base_url, thinking_budget=None):
            seen.update(thinking_budget=thinking_budget)

    monkeypatch.setattr(llm_anthropic, "AnthropicLLMClient", _StubAnthropic)
    provider = ProviderConfig(
        name="c", type=ProviderType.anthropic, api_key_env="ANTHROPIC_API_KEY",
        thinking_effort="medium",
    )
    factory.build_client_for_provider(provider, "claude-opus-4-8")
    assert seen["thinking_budget"] == 8192  # medium → budget tokens


def test_factory_enable_thinking_override(monkeypatch):
    # deep_review forces the DIGEST to reason (enable_thinking=True) while the
    # provider's base extra_body disables it for the fast trivial calls. The
    # override flips ONLY chat_template_kwargs.enable_thinking, leaving the rest.
    from zotero_summarizer.services.llm import factory

    monkeypatch.setenv("LOCAL_KEY", "secret")
    captured = {}
    monkeypatch.setattr(
        factory, "build_llm",
        lambda url, model, key, max_tokens, temperature, extra_body: captured.update(extra_body=extra_body) or "C",
    )
    base = {"chat_template_kwargs": {"enable_thinking": False}, "keep": 1}
    provider = ProviderConfig(name="p", base_url="http://localhost:8080/v1",
                              api_key_env="LOCAL_KEY", extra_body=base)
    factory.build_client_for_provider(provider, "m", enable_thinking=True)
    assert captured["extra_body"]["chat_template_kwargs"]["enable_thinking"] is True
    assert captured["extra_body"]["keep"] == 1            # other keys preserved
    assert base["chat_template_kwargs"]["enable_thinking"] is False  # source not mutated


def test_factory_enable_thinking_noop_without_chat_template_kwargs(monkeypatch):
    # A plain OpenAI provider (no chat_template_kwargs) must NOT receive an injected
    # key it would reject — the override is a no-op there.
    from zotero_summarizer.services.llm import factory

    monkeypatch.setenv("LOCAL_KEY", "secret")
    captured = {}
    monkeypatch.setattr(
        factory, "build_llm",
        lambda url, model, key, max_tokens, temperature, extra_body: captured.update(extra_body=extra_body) or "C",
    )
    provider = ProviderConfig(name="oa", base_url="https://api.openai.com/v1",
                              api_key_env="LOCAL_KEY", extra_body=None)
    factory.build_client_for_provider(provider, "m", enable_thinking=True)
    assert captured["extra_body"] is None  # nothing injected


def test_factory_anthropic_builds_native_adapter(monkeypatch):
    from zotero_summarizer.services.llm import factory
    from zotero_summarizer.integrations import llm_anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    seen = {}

    class _StubAnthropic:
        def __init__(self, *, model, api_key, max_tokens, base_url, thinking_budget=None):
            seen.update(model=model, api_key=api_key, max_tokens=max_tokens,
                        base_url=base_url, thinking_budget=thinking_budget)

    monkeypatch.setattr(llm_anthropic, "AnthropicLLMClient", _StubAnthropic)
    provider = ProviderConfig(name="c", type=ProviderType.anthropic, api_key_env="ANTHROPIC_API_KEY")
    client = factory.build_client_for_provider(provider, "claude-opus-4-8")
    assert isinstance(client, _StubAnthropic)
    assert seen["model"] == "claude-opus-4-8" and seen["api_key"] == "sk-test"
    assert seen["thinking_budget"] is None  # no thinking_effort set → thinking off


def test_factory_missing_key_raises_apierror(monkeypatch):
    from zotero_summarizer.api.errors import APIError
    from zotero_summarizer.services.llm import factory

    monkeypatch.delenv("LOCAL_KEY", raising=False)
    provider = ProviderConfig(name="local", base_url="http://localhost/v1", api_key_env="LOCAL_KEY")
    with pytest.raises(APIError) as ei:
        factory.build_client_for_provider(provider, "m")
    assert ei.value.error == "missing_api_key" and ei.value.status_code == 400


# --- operational check ------------------------------------------------------

def test_operational_check_reports_per_stage_status(monkeypatch):
    from zotero_summarizer.services.llm import operational_check

    routing = _routing(deep_review=StageModelConfig(provider="claude", model="claude-opus-4-8"))
    fake_state = types.SimpleNamespace(app_state=types.SimpleNamespace(config=types.SimpleNamespace(llm_routing=routing)))
    monkeypatch.setattr(operational_check, "state", lambda: fake_state)

    class _OkClient:
        def prompt(self, _p):
            return "ok"

    def _fake_build(provider, model):
        # The deep_review stage routes to the anthropic ("claude") provider —
        # simulate that endpoint being unreachable (the shared probe_provider now
        # builds per provider+model, not per resolved-stage object).
        if provider.type == ProviderType.anthropic:
            raise RuntimeError("connection refused")
        return _OkClient()

    monkeypatch.setattr(operational_check, "build_client_for_provider", _fake_build)

    result = asyncio.run(operational_check.check_stages())
    assert result["status"] == "degraded"
    by_stage = {row["stage"]: row for row in result["stages"]}
    assert by_stage["feed"]["status"] == "operational"
    assert by_stage["backlog"]["status"] == "operational"
    assert by_stage["deep_review"]["status"] == "fail"
    assert "connection refused" in by_stage["deep_review"]["detail"]


def test_reachability_check_reports_per_stage(monkeypatch):
    """Cheap reachability probe (GET /models): an unreachable stage comes back
    reachable=False with its base_url + detail so the deep-review banner can name
    the dead endpoint — the proactive half of the silent-empty-brief fix."""
    from zotero_summarizer.services.llm import model_list, operational_check

    routing = _routing(deep_review=StageModelConfig(provider="claude", model="claude-opus-4-8"))
    fake_state = types.SimpleNamespace(app_state=types.SimpleNamespace(config=types.SimpleNamespace(llm_routing=routing)))
    monkeypatch.setattr(operational_check, "state", lambda: fake_state)

    def _fake_list(provider):
        # deep_review routes to the anthropic provider — simulate it down.
        if provider.type == ProviderType.anthropic:
            raise RuntimeError("connection refused")
        return ["base-model"]

    monkeypatch.setattr(model_list, "list_models_for_provider", _fake_list)

    result = asyncio.run(operational_check.check_reachability())
    assert result["status"] == "degraded"
    by_stage = {row["stage"]: row for row in result["stages"]}
    assert by_stage["feed"]["reachable"] is True
    assert by_stage["feed"]["base_url"] == "http://localhost/v1"   # named for the banner
    assert by_stage["backlog"]["reachable"] is True
    assert by_stage["deep_review"]["reachable"] is False
    assert "connection refused" in by_stage["deep_review"]["detail"]

# Model listing (Settings model-picker) lives in test_model_listing.py.
