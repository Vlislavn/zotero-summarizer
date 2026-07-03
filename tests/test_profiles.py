"""Deployment profiles: preset → routing/depth, stage-cost aggregation, recommendation."""
from __future__ import annotations

import pytest

from zotero_summarizer.services.setup.bootstrap import _default_goals_config
from zotero_summarizer.services.setup.profiles import (
    PROFILES,
    StageCost,
    apply_profile,
    measure_stage_costs,
    recommend_profile,
    summarize_costs,
)


def _two_provider_config():
    """A config with a local 'default' + a remote API provider in the routing registry."""
    cfg = _default_goals_config()
    routing = cfg.llm_routing.model_dump(mode="python")
    routing["providers"].append({"name": "remote", "type": "openai",
                                 "base_url": "https://remote.example/v1", "api_key_env": "CUSTOM_API_KEY"})
    from zotero_summarizer.models.providers import LLMRoutingConfig
    return cfg.model_copy(update={"llm_routing": LLMRoutingConfig.model_validate(routing)})


def _cost(stage, provider, is_local, secs, ptok=100, ctok=100):
    return StageCost(stage=stage, provider=provider, model="m", is_local=is_local,
                     approx_prompt_tokens=ptok, approx_completion_tokens=ctok, secs=secs)


def test_apply_hybrid_routes_deep_review_remote_and_deep():
    cfg = _two_provider_config()
    out = apply_profile(cfg, "hybrid", local_provider="default", api_provider="remote",
                        local_model="gpt-oss:20b", api_model="GPT-OSS-120B")
    assert out.llm_routing.feed.provider == "default"
    assert out.llm_routing.deep_review.provider == "remote"
    assert out.llm_routing.deep_review.model == "GPT-OSS-120B"
    # deep tier on the remote deep-review provider → NOT lean (full/deep review)
    assert out.llm_routing.provider_by_name("remote").lean_deep_review is False
    # remote deep review fans out its within-paper sub-calls (not throttled by the local knob)
    assert out.llm_routing.provider_by_name("remote").max_sub_concurrency == 4


def test_apply_local_routes_everything_local_and_superficial():
    cfg = _two_provider_config()
    out = apply_profile(cfg, "local", local_provider="default", api_provider="remote",
                        local_model="gpt-oss:20b", api_model="GPT-OSS-120B")
    assert out.llm_routing.deep_review.provider == "default"
    # local deep review defaults to SUPERFICIAL → lean tier on the local provider
    assert out.llm_routing.provider_by_name("default").lean_deep_review is True


def test_apply_local_with_deep_override_is_not_lean():
    cfg = _two_provider_config()
    out = apply_profile(cfg, "local", local_provider="default", api_provider="remote",
                        local_model="gpt-oss:20b", api_model="GPT-OSS-120B", local_depth="deep")
    assert out.llm_routing.provider_by_name("default").lean_deep_review is False


def test_apply_unknown_profile_raises():
    with pytest.raises(ValueError):
        apply_profile(_two_provider_config(), "bogus", local_provider="default", api_provider="remote",
                      local_model="m", api_model="m")


def test_measure_skips_none_pairs():
    def run(stage, provider, _model):
        if stage == "deep_review" and provider == "local":
            return None  # caller skipped a local deep gen
        return (1000, 200, 1.0)
    costs = measure_stage_costs([("local", "m", True), ("remote", "m", False)], run=run)
    pairs = {(c.stage, c.provider) for c in costs}
    assert ("deep_review", "local") not in pairs
    assert ("deep_review", "remote") in pairs and ("feed", "local") in pairs


def test_summarize_heaviest_stage():
    costs = [_cost("feed", "local", True, 0.5, 50, 20), _cost("deep_review", "remote", False, 14.0, 15000, 800)]
    s = summarize_costs(costs)
    assert s["heaviest_by_tokens"]["stage"] == "deep_review"
    assert s["heaviest_by_secs"]["stage"] == "deep_review"


def test_recommend_hybrid_when_remote_deep_faster():
    costs = [_cost("deep_review", "local", True, 90.0), _cost("deep_review", "remote", False, 14.0)]
    assert recommend_profile(costs)["profile"] == "hybrid"


def test_recommend_hybrid_when_local_deep_unmeasured():
    costs = [_cost("feed", "local", True, 0.5), _cost("deep_review", "remote", False, 14.0)]
    assert recommend_profile(costs)["profile"] == "hybrid"


def test_recommend_local_when_no_remote():
    costs = [_cost("deep_review", "local", True, 30.0)]
    assert recommend_profile(costs)["profile"] == "local"


def test_set_profile_roundtrip_preserves_api_deep_model(tmp_path):
    """local→hybrid must restore the remote model, not pin the local model onto the API
    provider (regression: the round-trip corrupted deep_review to remote/qwen3:8b)."""
    from types import SimpleNamespace

    from zotero_summarizer.models.providers import LLMRoutingConfig
    from zotero_summarizer.services._common import read_config, write_user_config
    from zotero_summarizer.services.setup.profiles import detect_profile, set_profile

    cfg = _two_provider_config()
    routing = cfg.llm_routing.model_dump(mode="python")
    routing["deep_review"] = {"provider": "remote", "model": "GPT-OSS-120B"}
    cfg = cfg.model_copy(update={"llm_routing": LLMRoutingConfig.model_validate(routing)})
    s = SimpleNamespace(config_path=tmp_path / "goals.yaml", calibration_path=tmp_path / "cal.json")
    write_user_config(s.config_path, cfg)

    set_profile(s, "local")  # remembers remote/GPT-OSS-120B, routes deep_review local
    assert read_config(s.config_path).llm_routing.deep_review.provider == "default"
    set_profile(s, "hybrid")  # must recall the remote model
    out = read_config(s.config_path)
    assert out.llm_routing.deep_review.provider == "remote"
    assert out.llm_routing.deep_review.model == "GPT-OSS-120B"
    assert detect_profile(out) == "hybrid"


def test_set_profile_hybrid_without_known_api_model_raises(tmp_path):
    from types import SimpleNamespace

    from zotero_summarizer.services._common import write_user_config
    from zotero_summarizer.services.setup.profiles import set_profile

    cfg = _two_provider_config()  # deep_review inherits default (local) — no API model ever set
    s = SimpleNamespace(config_path=tmp_path / "goals.yaml", calibration_path=tmp_path / "cal.json")
    write_user_config(s.config_path, cfg)
    with pytest.raises(ValueError):
        set_profile(s, "hybrid")


def test_profiles_have_required_shape():
    for name, p in PROFILES.items():
        assert set(p["stages"]) == {"feed", "backlog", "deep_review"}
        assert p["deep_review_depth"] in {"superficial", "deep"}
        assert p["label"] and p["description"]
