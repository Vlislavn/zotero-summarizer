from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_setup_doctor import _settings
from zotero_summarizer.models.providers import DefaultModelConfig, LLMRoutingConfig, ProviderConfig, StageModelConfig
from zotero_summarizer.services.setup import doctor
from zotero_summarizer.services.llm import factory, model_list, operational_check


def _routing(other_first):
    ollama = ProviderConfig(name="ollama-peer", base_url="http://localhost:11434/v1", api_key_env="OLLAMA_API_KEY")
    other = ProviderConfig(name="custom-peer", base_url="http://localhost:8899/v1", api_key_env="CUSTOM_TEST_API_KEY")
    first, second = (other, ollama) if other_first else (ollama, other)
    routing = LLMRoutingConfig(providers=[ollama, other],
        default=DefaultModelConfig(provider=first.name, model=first.name + "-model"),
        backlog=StageModelConfig(provider=second.name, model=second.name + "-model"))
    return SimpleNamespace(llm_enabled=True, llm_routing=routing)


@pytest.mark.parametrize("other_first", [False, True])
def test_other_local_runtime_failure_is_not_reclassified_as_ollama(tmp_path, monkeypatch, other_first):
    config = _routing(other_first)
    monkeypatch.setattr(doctor, "read_config", lambda *_args: config)
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: "/test/ollama")
    failure = RuntimeError("custom runtime refused")

    def listed(provider):
        if provider.name == "custom-peer":
            raise failure
        return [provider.name + "-model"]

    inference = Mock(side_effect=AssertionError("Inventory must not construct an inference client"))
    monkeypatch.setattr(factory, "build_client_for_provider", inference)
    monkeypatch.setattr(model_list, "list_models_for_provider", listed)
    row = doctor._runtime_model(_settings(tmp_path))
    assert row["id"] == "runtime_model" and row["status"] == "needs_action"
    assert row["detail"] == "custom-peer/custom-peer-model at http://localhost:8899/v1: RuntimeError: custom runtime refused"
    assert row["recovery"]["action"] == "open_ai_settings" and "command" not in row["recovery"]
    inference.assert_not_called()


@pytest.mark.parametrize("other_first", [False, True])
def test_missing_other_local_model_never_suggests_ollama_pull(tmp_path, monkeypatch, other_first):
    config = _routing(other_first)
    monkeypatch.setattr(doctor, "read_config", lambda *_args: config)
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: "/test/ollama")
    monkeypatch.setattr(model_list, "list_models_for_provider",
                        lambda p: [] if p.name == "custom-peer" else [p.name + "-model"])
    row = doctor._runtime_model(_settings(tmp_path))
    assert row["status"] == "needs_action"
    assert row["detail"].split(", ") == ["custom-peer/custom-peer-model at http://localhost:8899/v1"] * (
        2 if other_first else 1)
    assert row["recovery"]["action"] == "open_ai_settings" and "command" not in row["recovery"]


@pytest.mark.parametrize("other_first", [False, True])
def test_non_ollama_chat_failure_keeps_native_recovery(tmp_path, monkeypatch, other_first):
    config = _routing(other_first)
    monkeypatch.setattr(doctor, "read_config", lambda *_args: config)
    stages = [{"stage": "feed", "provider": "custom-peer", "model": "custom-peer-model",
               "base_url": "http://localhost:8899/v1", "status": "fail",
               "detail": "model does not support chat", "reason_code": "inference_failed"}]

    async def checked(*_args, **_kwargs):
        return {"stages": stages}

    monkeypatch.setattr(operational_check, "check_routing_stages", checked)
    row = doctor._llm_inference(_settings(tmp_path))
    assert row["id"] == "llm_inference" and row["status"] == "needs_action"
    assert row["load_receipts"] == stages
    assert row["recovery"]["action"] == "open_ai_settings" and "command" not in row["recovery"]


def test_ml_only_runtime_still_makes_no_inventory_call(tmp_path, monkeypatch):
    config = _routing(False)
    config.llm_enabled = False
    monkeypatch.setattr(doctor, "read_config", lambda *_args: config)
    inventory = Mock(side_effect=AssertionError("disabled runtime must not be inspected"))
    monkeypatch.setattr(model_list, "list_models_for_provider", inventory)
    assert doctor._runtime_model(_settings(tmp_path))["status"] == "ready"
    inventory.assert_not_called()
