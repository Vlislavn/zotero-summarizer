"""Public synthetic oracles; no live runtime, credentials, hardware or model loads."""
import asyncio
import json
from threading import Event

import pytest

from zotero_summarizer.models.providers import ProviderConfig
from zotero_summarizer.services.llm import operational_check
from zotero_summarizer.services.setup import doctor
from zotero_summarizer.services.setup.bootstrap import bootstrap_phase0
from zotero_summarizer.settings import Settings


_LOCAL_HOSTS = ["localhost", "127.0.0.1", "127.0.0.2", "127.1.2.3", "127.255.255.254",
                "[::1]", "[::ffff:127.0.0.2]", "[::ffff:7f00:2]", "0.0.0.0", "[::]",
                "127.1", "2130706433", "0177.0.0.1", "0x7f000001",
                "localhost.", "model.localhost", "model.localhost."]


@pytest.mark.parametrize("host", _LOCAL_HOSTS)
@pytest.mark.parametrize("kind", ["openai", "anthropic"])
def test_loopback_verification_refuses_before_client_construction(monkeypatch, kind, host):
    provider = ProviderConfig(name="local", type=kind, base_url=f"http://{host}:8080/v1",
                              api_key_env="UNUSED_LOCAL_KEY")
    monkeypatch.setattr(operational_check, "build_client_for_provider",
                        lambda *_a, **_k: pytest.fail("missing envelope must refuse before construction"))

    result = operational_check.probe_provider(provider, "public-model")

    assert provider.is_local
    assert result["status"] == "fail"
    assert result["reason_code"] == "effective_envelope_unknown"
    assert result["hardware_safe"] == "unknown"
    assert result["attempted"] is False


def test_hosted_probe_rejects_an_empty_real_response(monkeypatch):
    provider = ProviderConfig(name="remote", base_url="https://example.test/v1", api_key_env="PUBLIC_KEY")
    monkeypatch.setattr(operational_check, "build_client_for_provider",
                        lambda *_a, **_k: type("Client", (), {"prompt": lambda *_a: "  ", "close": lambda *_a: None})())

    result = operational_check.probe_provider(provider, "public-model")

    assert result["status"] == "fail"
    assert result["reason_code"] == "inference_failed"
    assert result["request_completion"]["expected"] == 0


def test_callback_lock_survives_waiter_cancellation_and_records_uncertainty():
    from uuid import uuid4
    import httpx
    from zotero_summarizer.services.setup.load_attempt import SetupAttempt

    entered, release = Event(), Event()
    attempt = SetupAttempt()

    def blocked():
        request = attempt.requests.expect("scripted", "fixture", "https://synthetic.invalid/v1/chat/completions", local=False)
        request.native_started(uuid4())
        request.transport_started(httpx.Request("POST", "https://synthetic.invalid/v1/chat/completions", json={"model": "fixture"}))
        entered.set()
        assert release.wait(3)
        return {"status": "ready"}

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as pool:
        waiting = pool.submit(attempt.run, blocked, timeout=2)
        try:
            assert entered.wait(2)
            attempt.abandon("cancelled")
            result = waiting.result(timeout=2)
            assert result["reason_code"] == "waiter_cancelled"
            assert result["ownership"] == "unresolved"
            other = SetupAttempt().run(lambda: pytest.fail("worker still owns boundary"), timeout=1)
            assert other["reason_code"] == "setup_load_busy"
        finally:
            release.set()
    from zotero_summarizer.services.setup.load_attempt import _WORKER

    assert _WORKER.acquire(timeout=2)
    _WORKER.release()
    assert attempt.receipt()["ownership"] == "unresolved"


def test_doctor_legacy_ready_requires_new_verification(tmp_path):
    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    signature = doctor._config_signature(settings)
    legacy = {"status": "ready", "ready": True, "config_signature": signature,
              "checks": [doctor._row(key, "ready", "legacy") for key in doctor._CHECKS]}
    (settings.data_dir / "setup_doctor.json").write_text(json.dumps(legacy))

    assert doctor.doctor_status(settings)["ready"] is False


def test_cancel_acknowledgement_cannot_race_ready_publication():
    from zotero_summarizer.services.setup.load_attempt import SetupAttempt

    cancelled = SetupAttempt()
    cancelled.abandon("cancelled")
    saved = []
    result = cancelled.publish({"ready": True, "status": "ready", "modes": {}}, saved.append)
    assert result["ready"] is False
    assert saved == [result]

    completed = SetupAttempt()
    completed.publish({"ready": True}, lambda _value: None)
    after = completed.abandon("cancelled")
    assert after["finished"] is True
    assert after["reason_code"] == ""  # caller must return 409, not acknowledge cancellation


def test_doctor_abandoned_run_never_publishes_ready(tmp_path, monkeypatch):
    from zotero_summarizer.services.setup.load_attempt import SetupAttempt

    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    attempt = SetupAttempt()

    def abandon(_settings):
        attempt.abandon("cancelled")
        return doctor._row("environment", "ready", "old result")

    monkeypatch.setattr(doctor, "_RUNNERS", {key: abandon for key in doctor._CHECKS})
    result = doctor.run_doctor(settings, attempt=attempt)

    assert result["ready"] is False
    assert result["status"] == "needs_action"
    assert doctor.doctor_status(settings)["ready"] is False


def test_cancel_api_forwards_actual_attempt_identity(monkeypatch):
    from zotero_summarizer.api.routes import setup

    seen = []
    monkeypatch.setattr(doctor, "cancel_doctor", lambda run_id: seen.append(run_id) or {"ready": False})
    result = asyncio.run(setup.cancel_doctor(setup.CancelDoctorRequest(run_id="captured-run")))

    assert seen == ["captured-run"]
    assert result == {"ready": False}


@pytest.mark.parametrize("observed,reason", [("different-model", "stop"), (None, "stop"), ("public-model", None)])
def test_verification_requires_observed_identity_and_terminal_metadata(observed, reason):
    from types import SimpleNamespace
    from zotero_summarizer.integrations.llm_callbacks import CompletionGuard

    guard = CompletionGuard(expected_model="public-model")
    generation = SimpleNamespace(generation_info={"finish_reason": reason},
                                 message=SimpleNamespace(response_metadata={"model_name": observed}))
    response = SimpleNamespace(generations=[[generation]], llm_output={})

    with pytest.raises(ValueError, match="identity or successful terminal"):
        guard.on_llm_end(response)
    assert guard.completed is False


def test_verification_accepts_real_metadata_not_prompt_text():
    from types import SimpleNamespace
    from zotero_summarizer.integrations.llm_callbacks import CompletionGuard

    guard = CompletionGuard(expected_model="public-model")
    generation = SimpleNamespace(generation_info={"finish_reason": "stop"},
                                 message=SimpleNamespace(response_metadata={"model_name": "public-model"}))
    guard.on_llm_end(SimpleNamespace(generations=[[generation]], llm_output={}))

    assert guard.completed is True


def test_setup_transport_has_no_hidden_retries_cache_proxy_or_redirects(monkeypatch):
    from zotero_summarizer.services import _adapters

    captured = {}
    monkeypatch.setattr(_adapters, "_load_onprem", lambda: (
        lambda **kwargs: captured.update(kwargs) or object(), None))
    client = _adapters.build_llm("https://example.test/v1", "public-model", "public-fixture",
                               verification=True, request_timeout_seconds=7)
    try:
        assert captured["max_retries"] == 0
        assert captured["cache"] is False
        assert captured["timeout"] == 7
        assert captured["http_client"].follow_redirects is False
        assert captured["http_client"]._trust_env is False
        assert captured["callbacks"][0].expected_model == "public-model"
    finally:
        client.close()
    assert captured["http_client"].is_closed


def test_verification_rejects_cached_or_unobserved_client_text():
    from types import SimpleNamespace
    from zotero_summarizer.integrations.llm import InstrumentedLLMClient
    from zotero_summarizer.integrations.llm_callbacks import CompletionGuard

    client = InstrumentedLLMClient(SimpleNamespace(prompt=lambda *_a, **_k: "ok"),
                                   verification_guard=CompletionGuard(expected_model="public-model"))

    with pytest.raises(ValueError, match="no observed completion"):
        client.prompt("any public fixture prompt")


def test_local_verification_never_resolves_a_hosted_secret(monkeypatch):
    from zotero_summarizer.api.errors import APIError
    from zotero_summarizer.services.llm import factory

    provider = ProviderConfig(name="local", base_url="http://localhost:8080/v1", api_key_env="HOSTED_KEY")
    monkeypatch.setattr(factory, "get_api_key", lambda *_a: pytest.fail("no secret lookup before admission"))

    with pytest.raises(APIError) as error:
        factory.build_client_for_provider(provider, "public-model", verification=True)
    assert error.value.error == "effective_envelope_unknown"


def test_ordinary_local_client_needs_no_key_when_none_is_configured(monkeypatch):
    from zotero_summarizer.services.llm import factory

    provider = ProviderConfig(name="local", base_url="http://127.0.0.2:8080/v1", api_key_env="UNCONFIGURED_KEY")
    monkeypatch.setattr(factory, "get_api_key", lambda _name: None)

    assert factory.resolve_api_key(provider) == "local"


def test_capability_rejection_precedes_credential_or_client_io(monkeypatch):
    provider = ProviderConfig(name="hosted", base_url="https://example.test/v1", api_key_env="PUBLIC_KEY", capabilities=[])
    monkeypatch.setattr(operational_check, "build_client_for_provider",
                        lambda *_a, **_k: pytest.fail("unsupported selection cannot construct a client"))

    result = operational_check.probe_provider(provider, "public-model")

    assert result["reason_code"] == "unsupported_capabilities"
    assert result["attempted"] is False


def test_abandonment_stops_later_requests_inside_a_dry_run():
    from types import SimpleNamespace
    from zotero_summarizer.integrations.llm import InstrumentedLLMClient
    from zotero_summarizer.services.setup.load_attempt import SetupAttempt

    attempt = SetupAttempt()
    client = InstrumentedLLMClient(SimpleNamespace(prompt=lambda *_a: "a real response"),
                                   before_request=attempt.require_active)
    assert client.prompt("first fixture") == "a real response"
    attempt.abandon("cancelled")

    with pytest.raises(RuntimeError, match="no further request"):
        client.prompt("later fixture")


def test_local_dry_run_never_constructs_a_client(tmp_path, monkeypatch):
    from zotero_summarizer.services.llm import factory

    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    monkeypatch.setattr(factory, "build_client_for_stage", lambda *_a, **_k: pytest.fail("local load forbidden"))

    result = doctor._dry_run(settings)

    assert result["status"] == "needs_action"
    assert result["load_receipts"][0]["attempted"] is False
    assert result["recovery"]["action"] == "open_ai_settings"


def test_local_status_is_configured_without_credentials(tmp_path, monkeypatch):
    from zotero_summarizer.services.setup import status

    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    config = doctor.read_config(settings.config_path)
    monkeypatch.setattr(status.credentials, "get_api_key", lambda *_a: pytest.fail("local status needs no key"))

    async def reachability():
        return {"stages": [{"stage": "deep_review", "reachable": True}]}

    monkeypatch.setattr(status.operational_check, "check_reachability", reachability)
    result = asyncio.run(status._llm_status(config))

    assert result.api_key_present is True
    assert result.reachable is True  # reachability alone does not satisfy Doctor
    assert doctor.doctor_status(settings)["ready"] is False


def test_redaction_preserves_structure_and_withholds_nested_receipt_secrets(tmp_path, monkeypatch):
    from zotero_summarizer.services.llm import credentials

    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    secret = 'quoted"\\\\secret\nvalue'
    monkeypatch.setattr(credentials, "get_api_key", lambda _name: (secret, "fixture"))
    rows = [{"id": "llm_inference", "status": "needs_action", "detail": secret,
             "load_receipts": [{"admission": {"detail": secret}}]}]

    doctor._redact_details(settings, rows)

    assert rows[0]["id"] == "llm_inference"
    assert rows[0]["detail"] == "[redacted]"
    assert rows[0]["load_receipts"][0]["admission"]["detail"] == "[redacted]"


def test_failed_redaction_is_needs_action_not_unredacted_success(tmp_path, monkeypatch):
    from zotero_summarizer.services.llm import credentials

    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    monkeypatch.setattr(credentials, "get_api_key", lambda _name: (_ for _ in ()).throw(RuntimeError("fixture failure")))
    rows = [doctor._row("llm_inference", "ready", "done", "private detail")]

    doctor._redact_details(settings, rows)

    assert rows[0]["status"] == "needs_action"
    assert "private detail" not in json.dumps(rows)


@pytest.mark.parametrize("response_model,stop,success", [("public-model", "end_turn", True),
                                                       ("other-model", "end_turn", False),
                                                       ("public-model", "max_tokens", False)])
def test_native_hosted_verification_observes_identity_and_owns_transport(monkeypatch, response_model, stop, success):
    import httpx
    anthropic = pytest.importorskip("anthropic")
    from zotero_summarizer.integrations import llm_requests
    from zotero_summarizer.integrations.llm_anthropic import AnthropicLLMClient

    captured, wire = {}, []
    native = anthropic.Anthropic
    http_client = llm_requests.verification_http_client

    def respond(request):
        wire.append(request)
        assert request.url.path == "/v1/messages"
        assert json.loads(request.content)["model"] == "public-model"
        return httpx.Response(200, json={"id": "msg_fixture", "type": "message", "role": "assistant",
            "model": response_model, "stop_reason": stop, "stop_sequence": None,
            "content": [{"type": "text", "text": "fixture response"}],
            "usage": {"input_tokens": 1, "output_tokens": 2}})

    def sdk(**kwargs):
        captured.update(kwargs)
        return native(**kwargs)

    monkeypatch.setattr(anthropic, "Anthropic", sdk)
    monkeypatch.setattr(llm_requests, "verification_http_client",
                        lambda hook: http_client(hook, transport=httpx.MockTransport(respond)))
    client = AnthropicLLMClient(model="public-model", api_key="fixture", verification=True)
    try:
        assert captured["max_retries"] == 0
        assert captured["http_client"].follow_redirects is False
        assert captured["http_client"]._trust_env is False
        if success:
            assert client.prompt("public fixture") == "fixture response"
        else:
            with pytest.raises(ValueError):
                client.prompt("public fixture")
    finally:
        client.close()
    assert captured["http_client"].is_closed and len(wire) == 1


@pytest.mark.parametrize("host", _LOCAL_HOSTS)
@pytest.mark.parametrize("kind", ["openai", "anthropic"])
def test_verification_factory_refuses_local_before_credentials_or_adapter(monkeypatch, kind, host):
    from zotero_summarizer.api.errors import APIError
    from zotero_summarizer.integrations import llm_anthropic
    from zotero_summarizer.services.llm import factory

    provider = ProviderConfig(name="local", type=kind, base_url=f"http://{host}:8080/v1", api_key_env="UNUSED_KEY")

    def forbidden(*_args, **_kwargs):
        pytest.fail("local verification must refuse before credential or adapter construction")

    monkeypatch.setattr(factory, "resolve_api_key", forbidden)
    monkeypatch.setattr(factory, "build_llm", forbidden)
    monkeypatch.setattr(llm_anthropic, "AnthropicLLMClient", forbidden)

    with pytest.raises(APIError) as error:
        factory.build_client_for_provider(provider, "public-model", verification=True)
    assert provider.is_local
    assert error.value.error == "effective_envelope_unknown"
    assert error.value.details["attempted"] is False
    assert error.value.details["hardware_safe"] == "unknown"


@pytest.mark.parametrize("host", ["192.0.2.2", "10.0.0.2", "[2001:db8::2]", "[::ffff:192.0.2.2]", "example.test",
                                 "localhost.example.test", "notlocalhost.test"])
@pytest.mark.parametrize("kind", ["openai", "anthropic"])
def test_remote_verification_retains_credential_and_adapter_path(monkeypatch, kind, host):
    from dataclasses import replace
    from tests._setup_request_events import observed_client
    from zotero_summarizer.integrations import llm_anthropic
    from zotero_summarizer.services._common import settings
    from zotero_summarizer.services.llm import factory

    provider = ProviderConfig(name="remote", type=kind, base_url=f"https://{host}:8080/v1", api_key_env="PUBLIC_KEY")
    calls = []

    def credentials(_provider):
        calls.append("credentials")
        return "public-fixture"

    def adapter(*_args, **kwargs):
        assert kwargs["verification"] is True
        calls.append("adapter")
        return observed_client(provider, "public-model", output="synthetic response",
                               owner=kwargs["request_owner"], before_request=kwargs["before_request"])

    monkeypatch.setattr(factory, "resolve_api_key", credentials)
    monkeypatch.setattr(factory, "build_llm", adapter)
    monkeypatch.setattr(llm_anthropic, "AnthropicLLMClient", adapter)
    configured = replace(settings(), summary_timeout_seconds=17)
    monkeypatch.setattr(factory, "settings", lambda: configured)

    result = operational_check.probe_provider(provider, "public-model")

    assert provider.is_local is False
    assert result["status"] == "operational"
    assert result["hardware_safe"] == "not_applicable"
    assert calls == ["credentials", "adapter"]


@pytest.mark.parametrize("revision", ["setup-verification-v1", "setup-verification-v2"])
def test_pre_correction_success_cannot_survive_loopback_classification_revision(tmp_path, revision):
    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    previous = {"ready": True, "status": "ready", "verification_revision": revision,
                "config_signature": doctor._config_signature(settings),
                "checks": [doctor._row(key, "ready", "previous") for key in doctor._CHECKS]}
    (settings.data_dir / "setup_doctor.json").write_text(json.dumps(previous))

    current = doctor.doctor_status(settings)

    assert current["ready"] is False
    assert current["status"] == "not_started"


@pytest.mark.parametrize("is_local", [True, False])
def test_verification_error_text_is_not_runtime_identity(is_local):
    from zotero_summarizer.services.setup.load_admission import verification_row

    row = verification_row([{"stage": "feed", "provider": "not-ollama", "model": "public-model",
                             "is_local": is_local, "status": "fail", "detail": "model does not support chat"}])

    assert row["status"] == "needs_action"
    assert row["recovery"]["label"] == "Retry inference"
    assert "command" not in row["recovery"]
