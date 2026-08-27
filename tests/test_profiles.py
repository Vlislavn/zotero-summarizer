from __future__ import annotations

from types import SimpleNamespace

import pytest

from zotero_summarizer.models.providers import LLMRoutingConfig
from zotero_summarizer.services._common import write_user_config
from zotero_summarizer.services.setup.bootstrap import _default_goals_config
from zotero_summarizer.services.setup.profiles import (
    LOCAL_PROFILE_VERSION,
    LOCAL_PROFILES,
    apply_local_profile,
    local_profile_catalog,
    set_local_profile,
)


def _two_provider_config():
    config = _default_goals_config()
    raw = config.llm_routing.model_dump(mode="python")
    raw["providers"].append({
        "name": "remote", "type": "openai", "base_url": "https://remote.example/v1",
        "api_key_env": "CUSTOM_API_KEY",
    })
    return config.model_copy(update={"llm_routing": LLMRoutingConfig.model_validate(raw)})


def test_local_profile_resolves_every_stage_without_losing_remote_provider():
    result = apply_local_profile(_two_provider_config(), "light")
    assert result.llm_routing.default.model == "qwen3:8b"
    assert result.llm_routing.provider_by_name("remote")
    for stage in ("feed", "backlog", "deep_review"):
        selected = getattr(result.llm_routing, stage)
        assert (selected.provider, selected.model) == ("local", "qwen3:8b")


def test_existing_profile_requires_explicit_model():
    with pytest.raises(ValueError, match="needs a model"):
        apply_local_profile(_two_provider_config(), "existing")


def test_catalog_blocks_unsafe_profile_and_names_download(monkeypatch, tmp_path):
    from zotero_summarizer.services.setup import profiles

    monkeypatch.setattr(profiles, "hardware_snapshot", lambda _s: {"memory_gb": 16, "disk_free_gb": 10})
    catalog = local_profile_catalog(SimpleNamespace(data_dir=tmp_path))
    assert catalog["version"] == LOCAL_PROFILE_VERSION
    rows = {row["id"]: row for row in catalog["profiles"]}
    assert rows["light"]["compatible"] and rows["light"]["pull_command"] == "ollama pull qwen3:8b"
    assert rows["light"]["provider"]["max_tokens"] == 8192
    assert not rows["balanced"]["compatible"]
    assert "32 GB memory" in rows["balanced"]["compatibility_detail"]


def test_set_local_profile_refuses_unsafe_hardware(monkeypatch, tmp_path):
    from zotero_summarizer.services.setup import profiles

    settings = SimpleNamespace(
        config_path=tmp_path / "goals.yaml", calibration_path=tmp_path / "cal.json",
        data_dir=tmp_path,
    )
    write_user_config(settings.config_path, _default_goals_config())
    monkeypatch.setattr(profiles, "hardware_snapshot", lambda _s: {"memory_gb": 8, "disk_free_gb": 5})
    with pytest.raises(ValueError, match="needs"):
        set_local_profile(settings, "light")


def test_profile_definitions_are_complete():
    assert set(LOCAL_PROFILES) == {"light", "balanced", "existing"}
    assert all(profile["label"] and profile["features"] for profile in LOCAL_PROFILES.values())
