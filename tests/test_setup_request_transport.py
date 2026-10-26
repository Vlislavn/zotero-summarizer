"""Opt-in installed SDK proof. MockTransport and owned TCP are distinct controls."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sys
from threading import Event
from types import SimpleNamespace

import httpx
import pytest

from tests.test_setup_load_ownership16 import boundary, routing  # noqa: F401
from zotero_summarizer.models.providers import resolve_stage
from zotero_summarizer.integrations import llm_requests
from zotero_summarizer.services import _adapters
from zotero_summarizer.services.llm import factory, operational_check as oc
from zotero_summarizer.services.setup import load_attempt as la


@pytest.fixture(autouse=True)
def actual_sdk_boundary():
    admitted = os.getenv("ZS_TEST_SETUP_NATIVE_SDK")
    if admitted is None:
        pytest.skip("Actual SDK prerequisite: run tests/setup_request_transport_bootstrap.py")
    assert admitted == "1", "Invalid actual-SDK prerequisite"
    identity = getattr(sys, "_zs_setup_sdk_identity", None)
    assert sys.flags.isolated and identity, "Actual SDK requires the isolated bootstrap"
    from onprem.llm import LLM
    from langchain_openai import ChatOpenAI
    from openai import OpenAI
    for name, function in (("onprem", LLM.__init__), ("langchain-openai", ChatOpenAI._generate),
                           ("openai", OpenAI.__init__)):
        path = Path(function.__code__.co_filename).resolve()
        assert str(path) == identity[name]["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == identity[name]["sha256"]


def completion(content, reason="stop"):
    return httpx.Response(200, json={
        "id": "synthetic-completion", "object": "chat.completion", "created": 0, "model": "synthetic",
        "choices": [{"index": 0, "finish_reason": reason, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    })


@pytest.fixture
def wire(tmp_path, monkeypatch, actual_sdk_boundary):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(factory, "resolve_api_key", lambda _provider: "synthetic-test-key")
    from onprem.llm import LLM
    from langchain_openai import ChatOpenAI
    from openai import OpenAI
    state = SimpleNamespace(requests=[], options=[], transports=[], status=200, reason="stop", content="ok", responder=None)
    def respond(request):
        state.requests.append(request)
        if state.responder is not None:
            return state.responder(request)
        if state.status != 200:
            return httpx.Response(state.status, json={"error": {"message": "Synthetic failure", "type": "server_error"}})
        return completion(state.content, state.reason)
    actual_http_client = llm_requests.verification_http_client
    def scoped_http_client(hook, **kwargs):
        http = actual_http_client(hook, transport=httpx.MockTransport(respond))
        state.transports.append(http)
        return http
    monkeypatch.setattr(llm_requests, "verification_http_client", scoped_http_client)
    with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False) as ordinary_http:
        def construct(**kwargs):
            state.options.append(dict(kwargs))
            kwargs.setdefault("http_client", ordinary_http)
            inner = LLM(**kwargs, model_download_path=str(tmp_path / "models"),
                        vectordb_path=str(tmp_path / "vectors"))
            assert type(inner.llm) is ChatOpenAI and type(inner.llm.root_client) is OpenAI
            return inner
        monkeypatch.setattr(_adapters, "_load_onprem", lambda: (construct, None))
        try:
            yield state
        finally:
            for transport in state.transports:
                transport.close()


def probe(attempt):
    resolved = resolve_stage(routing(), "feed")
    return attempt.run(lambda: oc.probe_provider(resolved.provider, resolved.model), timeout=oc._LOCAL_PROBE_TIMEOUT_SECS)


def test_observed_installed_client_has_one_send_terminal_receipt_and_no_cache(boundary, wire):
    result = probe(la.SetupAttempt())
    inventory = result["request_completion"]
    assert result["status"] == "operational" and result["ownership"] == "completed"
    assert [inventory[key] for key in ("expected", "started", "terminal")] == [1, 1, 1]
    assert inventory["successful"] and not inventory["invalid_events"]
    assert len(wire.requests) == 1 and wire.requests[0].method == "POST"
    assert wire.requests[0].url.path == "/v1/chat/completions"
    assert wire.options[0]["max_retries"] == 0 and wire.options[0]["cache"] is False
    assert wire.transports[0].is_closed and not wire.transports[0].follow_redirects
    assert not boundary.held()


def test_installed_sdk_5xx_is_one_send_not_hidden_transport_retry(boundary, wire):
    wire.status = 500
    result = probe(la.SetupAttempt())
    assert len(wire.requests) == 1
    assert result["status"] == "fail" and result["ownership"] == "unresolved"
    assert result["request_completion"]["terminal"] == 0 and boundary.held()


def test_generation_redirect_is_one_post_and_cannot_authorize_readiness(boundary, wire):
    wire.responder = lambda _request: httpx.Response(307, headers={"Location": "/v1/redirected"})
    result = probe(la.SetupAttempt())
    assert len(wire.requests) == 1 and wire.requests[0].url.path == "/v1/chat/completions"
    assert result["status"] == "fail" and result["ownership"] == "unresolved"
    assert wire.transports[0].is_closed and boundary.held()


@pytest.mark.parametrize("reason,content", [(None, "ok"), ("unknown", "ok"), ("stop", ""), ("length", "{")])
def test_terminal_metadata_and_output_contracts_remain_distinct(boundary, wire, reason, content):
    wire.reason, wire.content = reason, content
    result = probe(la.SetupAttempt())
    assert result["status"] == "fail" and len(wire.requests) == 1
    if reason in {"stop", "length"}:
        assert result["ownership"] == "completed" and not boundary.held()
    else:
        assert result["ownership"] == "unresolved" and boundary.held()
    if reason == "length":
        assert result["reason_code"] == "request_output_truncated"
        assert not result["request_completion"]["successful"]


def test_inline_start_observer_exception_prevents_installed_sdk_send(boundary, wire, monkeypatch):
    attempt = la.SetupAttempt()
    original = attempt.requests.expect
    def expected(*args, **kwargs):
        request = original(*args, **kwargs)
        def refuse(_native_id):
            request.refuse_before_dispatch()
            raise RuntimeError("Synthetic inline refusal")
        request.native_started = refuse
        return request
    monkeypatch.setattr(attempt.requests, "expect", expected)
    result = probe(attempt)
    assert result["status"] == "fail" and wire.requests == []
    assert result["ownership"] == "not_submitted" and not boundary.held()


@pytest.mark.parametrize("prompt,kwargs,pydantic", [
    ([{"role": "user", "content": "not the string path"}], {}, False),
    ("Synthetic", {"attempt_fix": True}, True),
    ("Synthetic", {"fix_llm": object()}, True),
    ("Synthetic", {"response_format": object()}, False),
])
def test_unsupported_implicit_paths_refused_before_dispatch(boundary, wire, prompt, kwargs, pydantic):
    from pydantic import BaseModel
    class Result(BaseModel):
        value: int
    attempt = la.SetupAttempt()
    def load():
        client = factory.build_client_for_stage(resolve_stage(routing(), "feed"), verification=True)
        try:
            if pydantic:
                client.pydantic_prompt(prompt, Result, **kwargs)
            else:
                client.prompt(prompt, **kwargs)
            return {"status": "operational"}
        finally:
            client.close()
    result = attempt.run(load, timeout=oc._LOCAL_PROBE_TIMEOUT_SECS)
    assert result["status"] == "fail" and wire.requests == []
    assert result["ownership"] == "not_submitted" and not boundary.held()


def test_ordinary_client_defaults_and_uninstrumented_hold_are_preserved(boundary, wire):
    attempt = la.SetupAttempt()
    def load():
        client = factory.build_client_for_stage(resolve_stage(routing(), "feed"))
        boundary.ticket(attempt)  # expected only; ordinary client has NO owned start/terminal hooks
        try:
            assert client.prompt("Synthetic ordinary string") == "ok"
            return {"status": "operational"}
        finally:
            client.close()
    result = attempt.run(load, timeout=oc._LOCAL_PROBE_TIMEOUT_SECS)
    assert "max_retries" not in wire.options[0] and "cache" not in wire.options[0]
    assert result["status"] == "fail" and result["ownership"] == "unknown"
    assert len(wire.requests) == 1 and boundary.held()
    assert result["request_completion"]["uncertainty"] and not result["request_completion"]["hold"]


def test_concurrent_abandonment_keeps_hold_and_prevents_next_actual_send(boundary, wire):
    entered, release = Event(), Event()
    attempt = la.SetupAttempt()
    def delayed(_request):
        entered.set()
        assert release.wait(oc._LOCAL_PROBE_TIMEOUT_SECS)
        return completion("ok")
    wire.responder = delayed
    def load():
        client = factory.build_client_for_stage(resolve_stage(routing(), "feed"), verification=True)
        try:
            client.prompt("First synthetic request")
            client.prompt("Must not dispatch after abandonment")
            return {"status": "operational"}
        finally:
            client.close()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(attempt.run, load, timeout=oc._LOCAL_PROBE_TIMEOUT_SECS)
        try:
            assert entered.wait(oc._LOCAL_PROBE_TIMEOUT_SECS)
            attempt.abandon("cancelled")
            assert not boundary.worker.acquire(blocking=False) and boundary.held()
            result = pending.result(timeout=oc._LOCAL_PROBE_TIMEOUT_SECS)
            assert result["ownership"] == "unresolved"  # waiter receipt is not retroactively rewritten
        finally:
            release.set()
        assert boundary.worker.acquire(timeout=oc._LOCAL_PROBE_TIMEOUT_SECS)
        boundary.worker.release()
    assert result["status"] == "fail" and attempt.receipt()["ownership"] == "completed"
    assert len(wire.requests) == 1 and not boundary.held()  # exact late response reconciles ONLY this request


def test_observed_weight_url_refused_before_onprem_constructor(boundary, monkeypatch):
    constructed = []
    monkeypatch.setattr(_adapters, "_load_onprem", lambda: constructed.append(True))
    with pytest.raises(ValueError, match="HTTP API"):
        _adapters.build_llm("http://localhost:11434/model.gguf", "synthetic", "test", verification=True)
    assert constructed == []


@pytest.mark.parametrize("repair", [False, True])
def test_real_dry_refine_repair_triage_all_use_the_same_observed_client(boundary, wire, monkeypatch, repair):
    from tests.test_setup_load_admission16 import draft
    from zotero_summarizer.models import GoalsConfig, RefinedSummary, TriageResult
    from zotero_summarizer.services.setup import doctor_dry_run
    config = GoalsConfig.model_validate(draft())
    config.llm_routing = routing()
    config.llm_routing.providers[0].structured_output = True
    # Bind approval to the exact structured-output identity, not the earlier provider snapshot.
    from tests.test_setup_load_admission16 import known_observation, policy_for, write_policy
    from zotero_summarizer.services.setup import load_admission
    resolved = resolve_stage(config.llm_routing, "feed")
    write_policy(boundary.settings.project_root, policy_for(resolved))
    monkeypatch.setattr(load_admission, "_observe_executing_runtime", lambda *_a: known_observation(resolved))
    monkeypatch.setattr(doctor_dry_run, "read_config", lambda *_args: config)
    refined = RefinedSummary(executive_summary="Synthetic transport fixture, not scientific evidence")
    triage = TriageResult(score=3, rationale="Synthetic transport result")
    outputs = (["not-json"] if repair else []) + [refined.model_dump_json(), triage.model_dump_json()]
    wire.responder = lambda _request: completion(outputs.pop(0))
    attempt = la.SetupAttempt()
    with attempt.scope():
        row = doctor_dry_run._dry_run(boundary.settings)
    count = 3 if repair else 2
    assert row["status"] == "ready" and outputs == []
    assert len(wire.requests) == count and len(wire.options) == 1
    receipt = row["load_receipts"][0]
    assert receipt["ownership"] == "completed" and receipt["request_completion"]["successful"]
    assert [receipt["request_completion"][key] for key in ("expected", "started", "terminal")] == [count] * 3
    payloads = [json.loads(request.content) for request in wire.requests]
    assert payloads[-1]["response_format"]["json_schema"]["name"] == "TriageResult"
    assert all("response_format" not in payload for payload in payloads[:-1])
    assert wire.transports[0].is_closed and not boundary.held()


@pytest.mark.parametrize("mode", ["complete", "disconnect"])
def test_owned_tcp_installed_native_completion_and_uncertainty(tmp_path, monkeypatch, boundary, mode):
    from tests._setup_owned_loopback import owned_sdk_wire
    with owned_sdk_wire(tmp_path, monkeypatch, mode) as tcp:
        attempt = la.SetupAttempt()
        result = attempt.run(lambda: oc.probe_provider(tcp.provider, tcp.model), timeout=tcp.budget)
        assert tcp.observations == [("POST", "/v1/chat/completions", "synthetic")]
        assert len(tcp.options) == 1 and tcp.options[0]["max_retries"] == 0 and tcp.options[0]["cache"] is False
        assert tcp.options[0]["http_client"].is_closed
        inventory = result["request_completion"]
        assert inventory["expected"] == inventory["started"] == 1
        assert inventory["terminal"] == (1 if mode == "complete" else 0)
        assert result["status"] == ("operational" if mode == "complete" else "fail")
        assert result["ownership"] == ("completed" if mode == "complete" else "unresolved")
        assert boundary.held() is (mode == "disconnect")
        with pytest.raises(AssertionError):
            tcp.permit.resolve("localhost", tcp.permit.address[1])
        with pytest.raises(AssertionError):
            tcp.permit.connect(None, ("127.0.0.2", tcp.permit.address[1]))


@pytest.mark.parametrize("prefix", ["", "/proxy", "/v1"])
def test_native_anthropic_endpoint_identity_matches_installed_sdk_prefix_semantics(prefix):
    from zotero_summarizer.integrations.llm_anthropic import AnthropicLLMClient

    client = AnthropicLLMClient(model="synthetic", api_key="synthetic-test-key",
                                base_url="http://127.0.0.1:1" + prefix, verification=True)
    try:
        request = client._request_owner()
        assert request._endpoint == str(client._client._prepare_url("/v1/messages"))
    finally:
        client.close()


@pytest.mark.parametrize("mode", ["complete", "disconnect"])
def test_owned_tcp_installed_anthropic_completion_and_uncertainty(tmp_path, monkeypatch, boundary, mode):
    from tests._setup_owned_loopback import owned_sdk_wire

    with owned_sdk_wire(tmp_path, monkeypatch, mode, kind="anthropic") as tcp:
        attempt = la.SetupAttempt()
        result = attempt.run(lambda: oc.probe_provider(tcp.provider, tcp.model), timeout=tcp.budget)
        assert tcp.observations == [("POST", "/v1/messages", "synthetic")]
        assert len(tcp.options) == 1 and tcp.options[0]["max_retries"] == 0
        assert tcp.options[0]["http_client"].is_closed
        inventory = result["request_completion"]
        assert inventory["expected"] == inventory["started"] == 1
        assert inventory["terminal"] == (1 if mode == "complete" else 0)
        assert result["status"] == ("operational" if mode == "complete" else "fail")
        assert result["ownership"] == ("completed" if mode == "complete" else "unresolved")
        assert boundary.held() is (mode == "disconnect")


@pytest.mark.parametrize("mode", ["waiter_late", "sdk_timeout"])
def test_owned_tcp_waiter_deadline_and_native_read_timeout_are_distinct(tmp_path, monkeypatch, boundary, mode):
    from tests import setup_request_transport_bootstrap as bootstrap
    from tests._setup_owned_loopback import owned_sdk_wire

    with owned_sdk_wire(tmp_path, monkeypatch, mode) as tcp:
        attempt = la.SetupAttempt()
        with attempt.scope():
            client = factory.build_client_for_provider(tcp.provider, tcp.model, verification=True)
        timing = bootstrap.timing_seconds(os.getenv(bootstrap.TIMING_ENV))

        def invoke():
            try:
                client.prompt("One actual owned request")
                return {"status": "operational"}
            finally:
                client.close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(attempt.run, invoke, timeout=timing if mode == "waiter_late" else tcp.budget)
            try:
                assert tcp.received.wait(tcp.budget)
                result = pending.result(timeout=tcp.budget)
                assert result["status"] == "fail" and result["ownership"] == "unresolved"
                assert result["request_completion"]["started"] == 1 and result["request_completion"]["terminal"] == 0
                assert boundary.held() and tcp.observations == [("POST", "/v1/chat/completions", "synthetic")]
                if mode == "waiter_late":
                    assert attempt.is_abandoned() and result["reason_code"] == "waiter_timeout"
                    assert not boundary.worker.acquire(blocking=False)
                    refused = la.SetupAttempt().run(lambda: pytest.fail("Unsafe successor"), timeout=tcp.budget)
                    assert refused["reason_code"] == "setup_load_busy"
                    tcp.release.set()
                else:
                    assert not attempt.is_abandoned()
            finally:
                if mode == "waiter_late":
                    tcp.release.set()
                assert boundary.worker.acquire(timeout=tcp.budget)
                boundary.worker.release()
                client.close()
        assert result["ownership"] == "unresolved"  # immutable original waiter/callback result
        assert (attempt.receipt()["ownership"] == "completed") is (mode == "waiter_late")
        assert boundary.held() is (mode == "sdk_timeout")
        assert len(tcp.observations) == 1
