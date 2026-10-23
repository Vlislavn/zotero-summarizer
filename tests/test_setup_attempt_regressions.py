"""Synthetic cancellation, persisted identity and credential-rotation regressions."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from zotero_summarizer.models.providers import DefaultModelConfig, LLMRoutingConfig, ProviderConfig, StageModelConfig
from zotero_summarizer.services.llm import operational_check
from zotero_summarizer.services.setup import doctor
from zotero_summarizer.services.setup.bootstrap import bootstrap_phase0
from zotero_summarizer.services.setup.load_attempt import SetupAttempt, _WORKER
from zotero_summarizer.settings import Settings


def _settings(tmp_path):
    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    return settings


def _ready(settings):
    return {"status": "ready", "ready": True, "verification_revision": doctor.ADMISSION_REVISION,
            "config_signature": doctor._config_signature(settings),
            "checks": [doctor._row(key, "ready", "fixture") for key in doctor._CHECKS]}


def test_cancel_inventory_fences_next_provider_and_keeps_exclusion(tmp_path, monkeypatch):
    from zotero_summarizer.services.llm import model_list

    settings = _settings(tmp_path)
    first = ProviderConfig(name="first", base_url="https://first.test/v1", api_key_env="FIXTURE_KEY")
    second = ProviderConfig(name="second", base_url="https://second.test/v1", api_key_env="FIXTURE_KEY")
    routing = LLMRoutingConfig(providers=[first, second], default=DefaultModelConfig(provider="first", model="fixture"),
                               deep_review=StageModelConfig(provider="second", model="fixture"))
    monkeypatch.setattr(doctor, "read_config", lambda *_a: SimpleNamespace(llm_enabled=True, llm_routing=routing))
    entered, release = Event(), Event()
    seen = []

    def listing(provider):
        seen.append(provider.name)
        entered.set()
        assert release.wait(2)
        return ["fixture"]

    monkeypatch.setattr(model_list, "list_models_for_provider", listing)
    attempt = SetupAttempt()
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(doctor.run_doctor, settings, check_ids=["runtime_model"], attempt=attempt)
        assert entered.wait(2)
        doctor.cancel_doctor(attempt.run_id)
        acquired = _WORKER.acquire(blocking=False)
        if acquired:
            _WORKER.release()
        release.set()
        assert result.result(timeout=2)["ready"] is False
    assert seen == ["first"]
    assert not acquired, "inventory callback must retain setup exclusion"


def test_config_change_retains_active_cancel_identity(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    entered, release = Event(), Event()

    def pending(_settings):
        entered.set()
        assert release.wait(2)
        return doctor._row("environment", "ready", "fixture")

    monkeypatch.setitem(doctor._RUNNERS, "environment", pending)
    attempt = SetupAttempt()
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(doctor.run_doctor, settings, check_ids=["environment"], attempt=attempt)
        assert entered.wait(2)
        try:
            with settings.config_path.open("a") as stream:
                stream.write("\n# synthetic configuration change\n")
            status = doctor.doctor_status(settings)
            assert status["ready"] is False
            assert status["status"] == "running"
            assert status["run_id"] == attempt.run_id
            assert doctor.cancel_doctor(status["run_id"])["status"] == "cancelling"
        finally:
            release.set()
        assert result.result(timeout=2)["ready"] is False


def test_new_run_does_not_persist_predecessor_receipt(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    previous = _ready(settings)
    previous["attempt"] = {"run_id": "predecessor", "ownership": "returned", "finished": True}
    doctor._save(settings, previous)
    entered, release = Event(), Event()

    def pending(_settings):
        entered.set()
        assert release.wait(2)
        return doctor._row("dry_run", "ready", "fixture")

    monkeypatch.setitem(doctor._RUNNERS, "dry_run", pending)
    attempt = SetupAttempt()
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(doctor.run_doctor, settings, check_ids=["dry_run"], attempt=attempt)
        assert entered.wait(2)
        try:
            persisted = json.loads((settings.data_dir / "setup_doctor.json").read_text())
            assert persisted["attempt"]["run_id"] == attempt.run_id
            # Simulate a new process: no in-memory attempt/lock survives restart.
            monkeypatch.setattr(doctor, "_ACTIVE", None)
            monkeypatch.setattr(doctor, "_LOCK", SimpleNamespace(locked=lambda: False, release=doctor._LOCK.release))
            recovered = doctor.doctor_status(settings)
            assert recovered["ready"] is False
            assert recovered["attempt"]["ownership"] == "unresolved"
            assert recovered["attempt"]["hold"] is True
        finally:
            release.set()
        result.result(timeout=2)


@pytest.mark.parametrize("replacement", ["replacement-fixture", ""])
def test_effective_environment_credential_changes_invalidate_ready(tmp_path, monkeypatch, replacement):
    from zotero_summarizer.services.llm import credentials

    settings = _settings(tmp_path)
    config = doctor.read_config(settings.config_path, settings.calibration_path)
    config.llm_routing.providers[0].base_url = "https://example.test/v1"
    monkeypatch.setattr(doctor, "read_config", lambda *_a: config)
    name = config.llm_routing.providers[0].api_key_env
    monkeypatch.setenv(name, "original-fixture")
    assert credentials.get_api_key(name) == ("original-fixture", "environment")
    doctor._save(settings, _ready(settings))
    assert doctor.doctor_status(settings)["ready"] is True

    monkeypatch.setenv(name, replacement)

    assert doctor.doctor_status(settings)["ready"] is False
    assert "original-fixture" not in (settings.data_dir / "setup_doctor.json").read_text()


def test_keyring_replacement_and_credential_source_change_invalidate_ready(tmp_path, monkeypatch):
    from zotero_summarizer.services.llm import credentials

    settings = _settings(tmp_path)
    config = doctor.read_config(settings.config_path, settings.calibration_path)
    config.llm_routing.providers[0].base_url = "https://example.test/v1"
    monkeypatch.setattr(doctor, "read_config", lambda *_a: config)
    name = config.llm_routing.providers[0].api_key_env
    monkeypatch.setenv(name, "environment-fixture")
    monkeypatch.setattr(credentials.keyring, "get_password", lambda *_a: "keyring-fixture")
    doctor._save(settings, _ready(settings))
    monkeypatch.setattr(credentials.keyring, "get_password", lambda *_a: "replacement-fixture")
    assert doctor.doctor_status(settings)["ready"] is False
    doctor._save(settings, _ready(settings))
    monkeypatch.setenv(name, "replacement-fixture")
    monkeypatch.setattr(credentials.keyring, "get_password", lambda *_a: None)
    assert doctor.doctor_status(settings)["ready"] is False
    assert "replacement-fixture" not in (settings.data_dir / "setup_doctor.json").read_text()


def test_empty_check_selection_rechecks_instead_of_refreshing_old_success(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    doctor._save(settings, _ready(settings))
    seen = []

    def check(key):
        def run(_settings):
            seen.append(key)
            return doctor._row(key, "needs_action", "fixture failure")
        return run

    monkeypatch.setattr(doctor, "_RUNNERS", {key: check(key) for key in doctor._CHECKS})

    result = doctor.run_doctor(settings, check_ids=[])

    assert seen == list(doctor._CHECKS)
    assert result["ready"] is False


@pytest.mark.parametrize("endpoint", ["http://[::1", "http://[not-ipv6]/v1"])
def test_malformed_endpoint_is_failed_probe_not_uncaught_error(monkeypatch, endpoint):
    provider = ProviderConfig(name="fixture", base_url=endpoint, api_key_env="FIXTURE_KEY")
    monkeypatch.setattr(operational_check, "build_client_for_provider",
                        lambda *_a, **_k: pytest.fail("malformed URL must not construct an adapter"))

    result = operational_check.probe_provider(provider, "fixture")

    assert result["status"] == "fail"
    assert result["attempted"] is False


def test_probe_worker_owns_exclusion_past_executor_shutdown(monkeypatch):
    if not hasattr(asyncio.constants, "THREAD_JOIN_TIMEOUT"):
        pytest.skip("bounded default-executor shutdown was added in Python 3.12")
    monkeypatch.setattr(asyncio.constants, "THREAD_JOIN_TIMEOUT", 0.01)
    monkeypatch.setattr(operational_check, "_PROBE_TIMEOUT_SECS", 0.01)
    provider = ProviderConfig(name="fixture", base_url="https://example.test/v1", api_key_env="FIXTURE_KEY")
    routing = LLMRoutingConfig(providers=[provider], default=DefaultModelConfig(provider="fixture", model="fixture"))
    entered, release, returned = Event(), Event(), Event()

    def prompt(_prompt):
        entered.set()
        try:
            assert release.wait(2)
            return "fixture"
        finally:
            returned.set()

    monkeypatch.setattr(operational_check, "build_client_for_provider",
                        lambda *_a, **_k: SimpleNamespace(prompt=prompt, close=lambda: None))
    attempt = SetupAttempt()
    try:
        attempt.run(lambda: asyncio.run(operational_check.check_routing_stages(routing)), timeout=0.1)
        assert entered.is_set() and not returned.is_set()
        acquired = _WORKER.acquire(blocking=False)
        if acquired:
            _WORKER.release()
        assert not acquired, "the waiter or executor must not release an active provider callback"
    finally:
        release.set()
        assert returned.wait(2)
        assert _WORKER.acquire(timeout=2)
        _WORKER.release()
