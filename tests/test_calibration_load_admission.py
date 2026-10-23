"""Real calibration/factory admission with inert lower adapters; no model or network."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models.providers import LLMRoutingConfig, ProviderConfig
from zotero_summarizer.services import _common
from zotero_summarizer.services.llm import factory
from zotero_summarizer.services.setup import calibration
from zotero_summarizer.services.setup.bootstrap import _default_goals_config
from zotero_summarizer.settings import Settings


@pytest.fixture
def calibration_trial(tmp_path, monkeypatch):
    settings = Settings.load(project_root=tmp_path)
    directory = settings.paper_render_dir / "SYNTHETIC1"
    directory.mkdir(parents=True)
    (directory / "paper_read.json").write_text(json.dumps({"title": "Synthetic", "qa_text": "Fixture original"}))
    seen = []

    def observe(event):
        seen.append(event)
        (settings.data_dir / "calibration-admission-observed.json").write_text(json.dumps({"events": seen}))

    def configure(kind, host, *, cached=False):
        provider = ProviderConfig(name="fixture", type=kind, base_url=f"https://{host}:8080/v1",
                                  api_key_env="UNUSED_SYNTHETIC_KEY")
        config = _default_goals_config()
        config.llm_routing = LLMRoutingConfig(providers=[provider], default={"provider": "fixture", "model": "fixture-model"})
        monkeypatch.setattr(_common, "read_config", lambda *_a, **_k: config)
        monkeypatch.setattr(factory, "resolve_api_key", lambda _p: observe("credential") or "synthetic")

        def prompt(_text):
            observe("prompt")
            return "Inert synthetic response"

        client = SimpleNamespace(prompt=prompt)
        monkeypatch.setattr(factory, "build_llm", lambda *_a, **_k: observe("adapter") or client)
        from zotero_summarizer.integrations import llm_anthropic
        monkeypatch.setattr(llm_anthropic, "AnthropicLLMClient", lambda **_k: observe("adapter") or client)
        monkeypatch.setattr(calibration, "memory_preflight", lambda: observe("preflight"))
        monkeypatch.setattr(calibration, "tier2_calibrate", lambda *_a, **_k: {"status": "calibrated"})
        if cached:
            settings.calibration_path.parent.mkdir(parents=True, exist_ok=True)
            settings.calibration_path.write_text(json.dumps({"version": 1, "tier1": {"profile": "full"}, "entries": {}}))
        return settings, provider, seen

    return configure


@pytest.mark.parametrize("kind", ["openai", "anthropic"])
@pytest.mark.parametrize("host", ["localhost", "127.0.0.2", "[::1]"])
@pytest.mark.parametrize("cached", [False, True])
def test_local_calibration_refuses_before_credentials_adapter_prompt_or_writes(calibration_trial, kind, host, cached):
    settings, provider, seen = calibration_trial(kind, host, cached=cached)
    assert provider.is_local
    before = settings.calibration_path.read_bytes() if cached else None

    with pytest.raises(APIError) as error:
        calibration.run_full_calibration(settings, item_keys=["SYNTHETIC1"])

    assert error.value.error == "effective_envelope_unknown"
    assert error.value.status_code == 409
    assert error.value.details == {"attempted": False, "hardware_safe": "unknown"}
    assert seen == []
    after = settings.calibration_path.read_bytes() if settings.calibration_path.exists() else None
    assert after == before


@pytest.mark.parametrize("kind", ["openai", "anthropic"])
def test_hosted_calibration_keeps_ordinary_adapter_and_skips_local_probe(calibration_trial, kind):
    settings, provider, seen = calibration_trial(kind, "example.test")
    assert not provider.is_local

    result = calibration.run_full_calibration(settings, item_keys=["SYNTHETIC1"])

    assert result["tier1"]["status"] == "calibrated"
    assert result["tier2"]["status"] == "calibrated"
    assert seen == ["credential", "adapter"]
    assert settings.calibration_path.is_file()


@pytest.mark.parametrize("kind", ["openai", "anthropic"])
def test_ordinary_local_factory_is_not_blocked_by_calibration_guard(calibration_trial, kind):
    _settings, provider, seen = calibration_trial(kind, "localhost")

    client = factory.build_client_for_provider(provider, "fixture-model")
    client.prompt("Synthetic ordinary application call")

    assert seen == ["credential", "adapter", "prompt"]


def test_missing_paper_still_refuses_before_client_io(calibration_trial):
    settings, _provider, seen = calibration_trial("openai", "localhost")

    with pytest.raises(RuntimeError):
        calibration.run_full_calibration(settings, item_keys=["MISSING1"])

    assert seen == [] and not settings.calibration_path.exists()


@pytest.mark.parametrize("kind", ["openai", "anthropic"])
def test_later_digest_construction_rechecks_admission(calibration_trial, monkeypatch, kind):
    from zotero_summarizer.services.setup import load_admission

    settings, provider, seen = calibration_trial(kind, "localhost")
    real_refusal = load_admission.refused_local_load
    checks = []

    def first_only_permitted(actual_provider, model):
        checks.append((actual_provider.name, model))
        return None if len(checks) == 1 else real_refusal(actual_provider, model)

    monkeypatch.setattr(load_admission, "refused_local_load", first_only_permitted)
    with pytest.raises(APIError) as error:
        calibration.run_full_calibration(settings, item_keys=["SYNTHETIC1"])

    assert error.value.error == "effective_envelope_unknown"
    assert checks == [(provider.name, "fixture-model"), (provider.name, "fixture-model")]
    assert seen == ["credential", "adapter", "prompt", "preflight"]


def test_http_calibration_job_exposes_admission_refusal_not_ready(calibration_trial, monkeypatch):
    from zotero_summarizer.api.routes import setup
    from zotero_summarizer.services.setup import calibration_job

    settings, _provider, seen = calibration_trial("openai", "localhost")
    monkeypatch.setattr(setup, "get_settings", lambda: settings)
    monkeypatch.setattr(calibration_job, "_JOB", {"status": "idle"})
    monkeypatch.setattr(calibration_job.threading, "Thread", lambda *, target, **_k: SimpleNamespace(start=target))

    result = asyncio.run(setup.calibrate(setup.CalibrateRequest(papers=1)))

    assert result["status"] == "error"
    assert result["result"] is None and seen == []
    assert not settings.calibration_path.exists()
