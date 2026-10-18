"""Opt-in installed SDK proof; use setup_request_transport_bootstrap.py, not stubs."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
from threading import Event
from types import SimpleNamespace

import httpx
import pytest

from tests.test_setup_load_ownership16 import boundary, eligible, facts, routing  # noqa: F401
from zotero_summarizer.models.providers import resolve_stage
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

    for name, function in (("onprem", LLM.__init__),
                           ("langchain-openai", ChatOpenAI._generate),
                           ("openai", OpenAI.__init__)):
        assert str(Path(function.__code__.co_filename).resolve()) == identity[name]["path"]


@pytest.fixture
def wire(tmp_path, monkeypatch, actual_sdk_boundary):
    # No user paths, keyring, endpoint or OnPrem defaults are used.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(factory, "resolve_api_key", lambda _provider: "synthetic-test-key")
    monkeypatch.setattr(factory, "settings", lambda: SimpleNamespace(summary_timeout_seconds=17))
    from onprem.llm import LLM  # bootstrap verifies installed source before collection
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

    actual_http_client = _adapters._setup_http_client

    def scoped_http_client():
        http = actual_http_client(transport=httpx.MockTransport(respond))
        state.transports.append(http)
        return http

    monkeypatch.setattr(_adapters, "_setup_http_client", scoped_http_client)
    with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False) as ordinary_http:
        def construct(**kwargs):
            state.options.append(dict(kwargs))
            kwargs.setdefault("http_client", ordinary_http)
            assert kwargs["model_url"] == "http://localhost:11434/v1"
            inner = LLM(**kwargs, model_download_path=str(tmp_path / "models"),
                        vectordb_path=str(tmp_path / "vectors"))
            assert type(inner.llm) is ChatOpenAI
            assert type(inner.llm.root_client) is OpenAI
            return inner

        monkeypatch.setattr(_adapters, "_load_onprem", lambda: (construct, None))
        try:
            yield state
        finally:
            for transport in state.transports:
                transport.close()


def completion(content, reason="stop"):
    return httpx.Response(200, json={
        "id": "synthetic-completion", "object": "chat.completion", "created": 0, "model": "synthetic",
        "choices": [{"index": 0, "finish_reason": reason, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    })


def probe(attempt):
    resolved = resolve_stage(routing(), "feed")
    return la.run_setup_load(None, resolved, lambda submit: oc.probe_provider(
        resolved.provider, resolved.model, before_prompt=submit, request_observer=attempt),
        attempt=attempt, policy=eligible(resolved), facts=facts)


def test_observed_installed_client_has_one_send_terminal_receipt_and_no_cache(boundary, wire):
    result = probe(la.SetupAttempt())
    assert result["status"] == "operational" and result["ownership"] == "completed"
    assert result["request_completion"] == {"expected": 1, "started": 1, "terminal": 1, "invalid_events": False}
    assert len(wire.requests) == 1 and wire.requests[0].method == "POST"
    assert wire.requests[0].url.path == "/v1/chat/completions"
    assert wire.options[0]["max_retries"] == 0 and wire.options[0]["cache"] is False
    assert wire.transports[0].is_closed
    assert wire.transports[0].follow_redirects is False
    assert la.local_load_hold() is None


def test_installed_sdk_5xx_is_one_send_not_hidden_transport_retry(boundary, wire):
    wire.status = 500
    result = probe(la.SetupAttempt())
    assert len(wire.requests) == 1
    assert result["status"] == "fail" and result["ownership"] == "unresolved"
    assert result["request_completion"]["terminal"] == 0 and la.local_load_hold()


def test_generation_redirect_is_one_post_and_cannot_authorize_readiness(boundary, wire):
    wire.responder = lambda _request: httpx.Response(307, headers={"Location": "/v1/redirected"})
    result = probe(la.SetupAttempt())
    assert len(wire.requests) == 1 and wire.requests[0].url.path == "/v1/chat/completions"
    assert result["status"] == "fail" and result["ownership"] == "unresolved"
    assert wire.transports[0].is_closed and la.local_load_hold()


@pytest.mark.parametrize("reason,content", [(None, "ok"), ("unknown", "ok"), ("stop", ""), ("length", "{")])
def test_terminal_metadata_and_output_contracts_remain_distinct(boundary, wire, reason, content):
    wire.reason, wire.content = reason, content
    result = probe(la.SetupAttempt())
    assert result["status"] == "fail" and len(wire.requests) == 1
    if reason in {"stop", "length"}:
        assert result["ownership"] == "completed" and la.local_load_hold() is None
    else:
        assert result["ownership"] == "unresolved" and la.local_load_hold()
    if reason == "length":
        assert result["reason_code"] == "request_output_truncated"
        assert "output token limit" in result["detail"]


def test_inline_start_observer_exception_prevents_installed_sdk_send(boundary, wire):
    attempt = la.SetupAttempt()

    class RefusingObserver:
        def request_expected(self):
            attempt.request_expected()

        def request_started(self, _run_id):
            raise RuntimeError("Synthetic inline observer refusal")

        def request_finished(self, *_args):
            pytest.fail("Refused observer cannot receive a completion")

    resolved = resolve_stage(routing(), "feed")

    def load(_submit):
        client = factory.build_client_for_stage(resolved, request_observer=RefusingObserver())
        try:
            client.prompt("Synthetic probe")
            return {"status": "operational"}
        finally:
            client.close()

    result = la.run_setup_load(None, resolved, load, attempt=attempt, policy=eligible(resolved), facts=facts)
    assert result["status"] == "fail" and "inline observer refusal" in result["detail"]
    assert wire.requests == [] and result["ownership"] == "not_submitted"
    assert la.local_load_hold() is None


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
    resolved = resolve_stage(routing(), "feed")

    def load(_submit):
        client = factory.build_client_for_stage(resolved, request_observer=attempt)
        try:
            if pydantic:
                client.pydantic_prompt(prompt, Result, **kwargs)
            else:
                client.prompt(prompt, **kwargs)
            return {"status": "operational"}
        finally:
            client.close()

    result = la.run_setup_load(None, resolved, load, attempt=attempt, policy=eligible(resolved), facts=facts)
    assert result["status"] == "fail" and wire.requests == []
    assert result["ownership"] == "not_submitted" and la.local_load_hold() is None


def test_ordinary_client_defaults_and_uninstrumented_hold_are_preserved(boundary, wire):
    resolved = resolve_stage(routing(), "feed")

    def load(submit):
        client = factory.build_client_for_stage(resolved)
        submit()
        assert client.prompt("Synthetic ordinary string") == "ok"
        return {"status": "operational"}

    result = la.run_setup_load(None, resolved, load, policy=eligible(resolved), facts=facts)
    assert "max_retries" not in wire.options[0] and "cache" not in wire.options[0]
    assert result["status"] == "fail" and result["ownership"] == "unresolved"
    assert len(wire.requests) == 1 and la.local_load_hold()


def test_concurrent_abandonment_keeps_hold_and_prevents_next_actual_send(boundary, wire):
    entered, release = Event(), Event()
    attempt = la.SetupAttempt()
    resolved = resolve_stage(routing(), "feed")

    def delayed(_request):
        entered.set()
        assert release.wait(10)
        return completion("ok")

    wire.responder = delayed

    def load(_submit):
        client = factory.build_client_for_stage(resolved, request_observer=attempt)
        try:
            client.prompt("First synthetic request")
            client.prompt("Must not be dispatched after abandonment")
            return {"status": "operational"}
        finally:
            client.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(la.run_setup_load, None, resolved, load, attempt=attempt,
                              policy=eligible(resolved), facts=facts)
        try:
            assert entered.wait(10)
            attempt.abandon("cancelled")
            assert not boundary.worker.acquire(blocking=False)
            assert la.local_load_hold() == "waiter_cancelled"
        finally:
            release.set()
        result = pending.result(timeout=10)
    assert result["status"] == "fail" and result["ownership"] == "unresolved"
    assert len(wire.requests) == 1 and la.local_load_hold() == "waiter_cancelled"


def test_observed_weight_url_refused_before_onprem_constructor(boundary, monkeypatch):
    constructed = []
    monkeypatch.setattr(_adapters, "_load_onprem", lambda: constructed.append(True))
    with pytest.raises(ValueError, match="HTTP API"):
        _adapters.build_llm("http://localhost:11434/model.gguf", "synthetic", "test", request_observer=la.SetupAttempt())
    assert constructed == []


@pytest.mark.parametrize("repair", [False, True])
def test_real_dry_refine_repair_triage_all_use_the_same_observed_client(boundary, wire, monkeypatch, repair):
    from tests.test_setup_load_admission16 import draft
    from zotero_summarizer.models import GoalsConfig, RefinedSummary, TriageResult
    from zotero_summarizer.services.setup import doctor, doctor_dry_run

    config = GoalsConfig.model_validate(draft())
    config.llm_routing = routing()
    config.llm_routing.providers[0].structured_output = True
    monkeypatch.setattr(doctor_dry_run, "read_config", lambda *_args: config)
    assess = la.assess_setup_load
    monkeypatch.setattr(la, "assess_setup_load", lambda _settings, resolved, **_kwargs:
                        assess(None, resolved, policy=eligible(resolved), facts=facts))
    refined = RefinedSummary(executive_summary="Synthetic transport fixture, not scientific evidence")
    triage = TriageResult(score=3, rationale="Synthetic transport result")
    outputs = (["not-json"] if repair else []) + [refined.model_dump_json(), triage.model_dump_json()]
    wire.responder = lambda _request: completion(outputs.pop(0))

    row = doctor._dry_run(SimpleNamespace(config_path=None, calibration_path=None), attempt=la.SetupAttempt())

    count = 3 if repair else 2
    assert row["status"] == "ready" and outputs == []
    assert len(wire.requests) == count and len(wire.options) == 1
    receipt = row["load_receipts"][0]
    assert receipt["ownership"] == "completed"
    assert receipt["request_completion"] == {
        "expected": count, "started": count, "terminal": count, "invalid_events": False,
    }
    assert receipt["admission"]["policy_ref"] == "SYNTHETIC-not-measured"
    payloads = [json.loads(request.content) for request in wire.requests]
    assert payloads[-1]["response_format"]["json_schema"]["name"] == "TriageResult"
    assert all("response_format" not in payload for payload in payloads[:-1])
    assert wire.transports[0].is_closed
    assert la.local_load_hold() is None
