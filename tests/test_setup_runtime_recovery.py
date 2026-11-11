"""Runtime inventory recovery uses the failing route, never a healthy sibling."""
from types import SimpleNamespace

import pytest

from zotero_summarizer.models.providers import (
    DefaultModelConfig, LLMRoutingConfig, ProviderConfig, StageModelConfig,
)
from zotero_summarizer.services.llm import factory, model_list
from zotero_summarizer.services.setup import doctor


_BACKENDS = [("vllm", "http://127.0.0.1:8000/v1"),
             ("mlx", "http://127.0.0.1:8080/v1"),
             ("lm-studio", "http://127.0.0.1:1234/v1")]


def _provider(name, endpoint):
    return ProviderConfig(name=name, base_url=endpoint, api_key_env="UNUSED_FIXTURE_KEY")


def _settings(monkeypatch, first, second):
    routing = LLMRoutingConfig(
        providers=[first, second],
        default=DefaultModelConfig(provider=first.name, model=f"{first.name}-model"),
        deep_review=StageModelConfig(provider=second.name, model=f"{second.name}-model"),
    )
    config = SimpleNamespace(llm_enabled=True, llm_routing=routing)
    monkeypatch.setattr(doctor, "read_config", lambda *_args: config)
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: "/fixture/ollama")
    monkeypatch.setattr(factory, "build_client_for_provider",
                        lambda *_a, **_k: pytest.fail("inventory must not start inference"))
    return SimpleNamespace(config_path=None, calibration_path=None)


@pytest.mark.parametrize("name,endpoint", _BACKENDS)
@pytest.mark.parametrize("failure", ["unavailable", "missing_model"])
def test_healthy_ollama_does_not_own_another_runtime_recovery(monkeypatch, name, endpoint, failure):
    ollama = _provider("ollama", "http://localhost:11434/v1")
    failing = _provider(name, endpoint)
    settings = _settings(monkeypatch, ollama, failing)
    listed = []

    def models(provider):
        listed.append(provider.name)
        if provider.name == failing.name:
            if failure == "unavailable":
                raise OSError("synthetic endpoint unavailable")
            return []
        return ["ollama-model"]

    monkeypatch.setattr(model_list, "list_models_for_provider", models)

    row = doctor._runtime_model(settings)

    assert listed == ["ollama", name]  # repeated stages still share one inventory call
    assert row["status"] == "needs_action"
    assert name in row["detail"] and endpoint in row["detail"]
    assert "command" not in row["recovery"]
    assert row["recovery"]["action"] == "open_ai_settings"
    if failure == "unavailable":
        assert name in row["message"]
        assert "Ollama" not in row["message"]
    else:
        assert name in row["recovery"]["label"]


@pytest.mark.parametrize("failure,command", [("unavailable", "ollama serve"),
                                              ("missing_model", "ollama pull ollama-model"),
                                              ("not_installed", "brew install ollama")])
def test_recovery_keeps_actual_ollama_install_start_and_model_commands(monkeypatch, failure, command):
    healthy = _provider("vllm", "http://127.0.0.1:8000/v1")
    ollama = _provider("ollama", "http://127.0.0.2:11434/v1")
    settings = _settings(monkeypatch, healthy, ollama)
    if failure == "not_installed":
        monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)

    def models(provider):
        if provider.name == "ollama":
            if failure == "unavailable":
                raise OSError("synthetic Ollama unavailable")
            return []
        return ["vllm-model"]

    monkeypatch.setattr(model_list, "list_models_for_provider", models)

    row = doctor._runtime_model(settings)

    assert row["status"] == "needs_action"
    assert "ollama" in row["detail"] and ollama.base_url in row["detail"]
    assert row["recovery"]["command"] == command  # displayed only, never executed


@pytest.mark.parametrize("endpoint", ["http://127.0.0.1:8000/v1", "https://192.0.2.2:11434/v1"])
def test_provider_name_alone_never_selects_ollama_commands(monkeypatch, endpoint):
    misleading_name = _provider("ollama", endpoint)
    sibling = _provider("vllm", "http://127.0.0.1:8080/v1")
    settings = _settings(monkeypatch, sibling, misleading_name)
    monkeypatch.setattr(model_list, "list_models_for_provider",
                        lambda provider: ["vllm-model"] if provider.name == "vllm" else [])

    row = doctor._runtime_model(settings)

    assert row["status"] == "needs_action"
    assert "command" not in row["recovery"]
    assert row["recovery"]["action"] == "open_ai_settings"
