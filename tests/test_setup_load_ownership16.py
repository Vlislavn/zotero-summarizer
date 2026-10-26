"""Current attempt/persistence controls; scripted events are NOT native transport proof."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from threading import Event, Lock
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from tests.test_setup_load_admission16 import known_observation, policy_for, write_policy
from zotero_summarizer.models.providers import (
    DefaultModelConfig, LLMRoutingConfig, ProviderConfig, StageModelConfig, resolve_stage,
)
from zotero_summarizer.services.llm import operational_check as oc
from zotero_summarizer.services.setup import doctor, doctor_requests, load_admission
from zotero_summarizer.services.setup import load_attempt as la
from zotero_summarizer.services.setup.bootstrap import _bootstrap_goals
from zotero_summarizer.settings import Settings


def routing(local=True):
    return LLMRoutingConfig(providers=[ProviderConfig(name="hosted",
        base_url="http://localhost:11434/v1" if local else "https://synthetic.invalid/v1",
        api_key_env="TEST_API_KEY", num_ctx=1024)],
        default=DefaultModelConfig(provider="hosted", model="synthetic"))


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    settings = Settings.load(project_root=tmp_path)
    _bootstrap_goals(settings)  # No dotenv/database bootstrap, including inside isolated SDK proof.
    worker = Lock()
    monkeypatch.setattr(la, "_WORKER", worker)
    monkeypatch.setattr(doctor, "_ACTIVE", None)
    resolved = resolve_stage(routing(), "feed")
    settings = write_policy(tmp_path, policy_for(resolved))
    monkeypatch.setattr(load_admission, "_observe_executing_runtime",
                        lambda _provider, _model: known_observation(resolved))

    def ticket(attempt, *, local=True, model="synthetic"):
        target = resolve_stage(routing(local), "feed")
        return attempt.bind_client(settings, target.provider, model)()

    def start(request, *, model="synthetic", local=True):
        native_id = uuid4()
        request.native_started(native_id)
        endpoint = routing(local).providers[0].base_url + "/chat/completions"
        request.transport_started(httpx.Request("POST", endpoint, json={"model": model}))
        return native_id

    state = SimpleNamespace(settings=settings, worker=worker, ticket=ticket, start=start,
                            held=lambda: doctor_requests.local_request_fence(settings))
    yield state
    assert worker.acquire(timeout=5), "owned callback must be reaped"
    worker.release()


def completed(boundary, attempt, *, local=True):
    request = boundary.ticket(attempt, local=local)
    native_id = boundary.start(request, local=local)
    request.terminal("synthetic", "stop", native_id=native_id, native_required=True)
    return request


def test_timeout_keeps_callback_lock_and_post_exit_sticky_hold(boundary):
    attempt = la.SetupAttempt()
    entered, release, returned = Event(), Event(), Event()

    def load():
        request = boundary.ticket(attempt)
        boundary.start(request)
        entered.set()
        assert release.wait(5)
        returned.set()  # Callback return, deliberately NO observed terminal response.
        return {"status": "operational"}

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(attempt.run, load, timeout=5)
        try:
            assert entered.wait(5)
            attempt.abandon("timeout")
            assert not boundary.worker.acquire(blocking=False)
            refused = la.SetupAttempt().run(lambda: pytest.fail("overlap"), timeout=5)
            assert refused["reason_code"] == "setup_load_busy"
        finally:
            release.set()
        assert pending.result(timeout=5)["ownership"] == "unresolved"
    assert returned.is_set() and boundary.held()
    with pytest.raises(RuntimeError, match="Unresolved"):
        boundary.ticket(la.SetupAttempt())
    hosted = la.SetupAttempt()
    assert hosted.run(lambda: (completed(boundary, hosted, local=False) and {"status": "operational"}),
                      timeout=5)["status"] == "operational"
    assert boundary.held()  # independent hosted success cannot erase predecessor


def test_cancel_before_acceptance_is_not_submitted(boundary):
    attempt = la.SetupAttempt()
    receipt = attempt.abandon("cancelled")
    result = attempt.run(lambda: pytest.fail("constructed"), timeout=5)
    assert receipt["ownership"] == result["ownership"] == "not_submitted"
    assert not result["callback_started"] and not boundary.held()


def test_cancel_after_submission_does_not_unlock_worker(boundary):
    attempt = la.SetupAttempt()
    entered, release = Event(), Event()

    def load():
        boundary.start(boundary.ticket(attempt))
        entered.set()
        assert release.wait(5)
        return {"status": "operational"}

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(attempt.run, load, timeout=5)
        try:
            assert entered.wait(5)
            receipt = attempt.abandon("cancelled")
            assert receipt["ownership"] == "unresolved" and receipt["hold"]
            assert not boundary.worker.acquire(blocking=False)
        finally:
            release.set()
        assert pending.result(timeout=5)["status"] == "fail"
    assert boundary.held()


def test_cancel_during_construction_before_submit_has_no_false_hold(boundary):
    attempt = la.SetupAttempt()
    entered, release = Event(), Event()

    def load():
        entered.set()
        assert release.wait(5)
        boundary.ticket(attempt)
        pytest.fail("cancelled attempt submitted")

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(attempt.run, load, timeout=5)
        try:
            assert entered.wait(5)
            attempt.abandon("cancelled")
        finally:
            release.set()
        assert pending.result(timeout=5)["ownership"] == "not_submitted"
    assert not boundary.held()


def test_constructor_failure_not_submitted_but_normal_return_finality_unproven(boundary):
    attempt = la.SetupAttempt()
    def broken():
        raise ValueError("Synthetic config failure")
    result = attempt.run(broken, timeout=5)
    assert result["status"] == "fail" and result["ownership"] == "not_submitted" and not boundary.held()
    attempt = la.SetupAttempt()
    def load():
        boundary.start(boundary.ticket(attempt))
        return {"status": "operational"}
    result = attempt.run(load, timeout=5)
    assert result["status"] == "fail" and result["ownership"] == "unresolved" and boundary.held()


def test_unique_callbacks_resample_not_outer_catalog(boundary, monkeypatch):
    routes = routing()
    routes.deep_review = StageModelConfig(model="second")
    second = resolve_stage(routes, "deep_review")
    observed, constructions = [], []
    available = iter([12, 1])

    def fresh(provider, model):
        value = next(available)
        observed.append((provider.name, model, value))
        target = second if model == "second" else resolve_stage(routes, "feed")
        return known_observation(target, value)

    def construct(provider, model, *, verification):
        assert verification and provider.is_local
        constructions.append(model)
        write_policy(boundary.settings.project_root, policy_for(second))
        raise ValueError("Synthetic pre-submission construction failure")

    monkeypatch.setattr(load_admission, "_observe_executing_runtime", fresh)
    monkeypatch.setattr(oc, "build_client_for_provider", construct)
    result = asyncio.run(oc.check_routing_stages(routes))
    assert observed == [("hosted", "synthetic", 12), ("hosted", "second", 1)]
    assert constructions == ["synthetic"]
    assert result["stages"][2]["reason_code"] == "available_memory_insufficient"
    assert result["stages"][2]["attempted"] is False and not boundary.held()


def test_callback_identity_is_frozen_against_original_routing_mutation(boundary, monkeypatch):
    routes = routing(False)
    seen = []
    def probe(provider, model):
        seen.append((model, provider.base_url))
        routes.default.model = "changed-after-capture"
        routes.providers[0].base_url = "http://localhost:11434/v1"
        return {"status": "operational", "detail": ""}
    monkeypatch.setattr(oc, "probe_provider", probe)
    result = asyncio.run(oc.check_routing_stages(routes))
    assert seen == [("synthetic", "https://synthetic.invalid/v1")]
    assert all(row["model"] == "synthetic" for row in result["stages"])


def test_previous_unique_probe_cannot_label_unaccepted_next_probe_attempted(boundary, monkeypatch):
    attempt = la.SetupAttempt()
    completed(boundary, attempt, local=False)
    monkeypatch.setattr(oc, "build_client_for_provider", lambda *_a, **_k: (_ for _ in ()).throw(ValueError()))
    with attempt.scope():
        result = oc.probe_provider(routing(False).providers[0], "not-yet-accepted")
    assert result["attempted"] is False and result["ownership"] == "not_submitted"
    assert result["request_completion"]["expected"] == 0 and not boundary.held()


def test_api_cancellation_retains_dry_callback_ownership_and_hold(monkeypatch, boundary):
    from zotero_summarizer.api.routes import setup as api
    entered, release, finished = Event(), Event(), Event()
    attempt_seen = []
    monkeypatch.setattr(api, "get_settings", lambda: boundary.settings)
    def dry(_settings):
        attempt = la.current_attempt()
        attempt_seen.append(attempt)
        boundary.start(boundary.ticket(attempt))
        entered.set()
        assert release.wait(5)
        return doctor._row("dry_run", "ready", "Synthetic callback return, no terminal")
    monkeypatch.setitem(doctor._RUNNERS, "dry_run", dry)
    original_run = doctor.run_doctor
    def run(*args, **kwargs):
        try:
            return original_run(*args, **kwargs)
        finally:
            finished.set()
    monkeypatch.setattr(doctor, "run_doctor", run)
    async def scenario():
        task = asyncio.create_task(api.run_doctor(api.DoctorRequest(check_ids=["dry_run"])))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert attempt_seen[0].is_abandoned() and boundary.held()
            assert not boundary.worker.acquire(blocking=False)
        finally:
            release.set()
            with suppress(asyncio.CancelledError):
                await task
            assert await asyncio.to_thread(finished.wait, 5)
            assert await asyncio.to_thread(boundary.worker.acquire, timeout=5)
            boundary.worker.release()
    asyncio.run(scenario())
    assert not doctor.doctor_status(boundary.settings)["ready"] and boundary.held()


def test_sticky_hold_reconciles_persisted_ready_and_modes_but_not_hosted(monkeypatch, boundary):
    from zotero_summarizer.services._common import read_config, write_user_config
    original = la.SetupAttempt()
    boundary.start(boundary.ticket(original))
    monkeypatch.setattr(doctor, "_RUNNERS", {key: lambda _, key=key: doctor._row(key, "ready", "synthetic")
                                           for key in doctor._CHECKS})
    result = doctor.run_doctor(boundary.settings)
    assert not result["ready"] and result["request_hold"]
    assert result["modes"].get("local_inference") != "ready"
    assert next(row for row in result["checks"] if row["id"] == "local_profile")["status"] == "ready"
    config = read_config(boundary.settings.config_path)
    config.llm_routing = routing(False)
    write_user_config(boundary.settings.config_path, config)
    assert doctor.run_doctor(boundary.settings)["ready"]
    assert doctor.doctor_status(boundary.settings)["request_hold"] and boundary.held()


def test_doctor_abandonment_leaves_remaining_checks_unattempted(monkeypatch, boundary):
    attempt = la.SetupAttempt()
    def abandon(_settings):
        attempt.abandon("cancelled")
        return doctor._row("llm_inference", "needs_action", "synthetic cancelled")
    monkeypatch.setitem(doctor._RUNNERS, "llm_inference", abandon)
    monkeypatch.setitem(doctor._RUNNERS, "dry_run", lambda _: pytest.fail("remainder loaded"))
    result = doctor.run_doctor(boundary.settings, check_ids=["llm_inference", "dry_run"], attempt=attempt)
    dry = next(row for row in result["checks"] if row["id"] == "dry_run")
    assert dry["status"] == "needs_action" and result["attempt"]["ownership"] == "not_submitted"
    assert not boundary.held()


def test_nonsetup_timeout_preserves_default_sibling_continuation(monkeypatch, boundary):
    routes = routing(False)
    routes.deep_review = StageModelConfig(model="second")
    calls = []
    async def controlled_wait(work, *, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            work.close()
            raise asyncio.TimeoutError
        return await work
    monkeypatch.setattr(oc.asyncio, "wait_for", controlled_wait)
    monkeypatch.setattr(oc, "probe_provider", lambda p, m: {"status": "operational", "detail": ""})
    result = asyncio.run(oc.check_routing_stages(routes))
    assert [row["status"] for row in result["stages"]] == ["fail", "fail", "operational"]
    assert calls == [oc._PROBE_TIMEOUT_SECS, oc._PROBE_TIMEOUT_SECS]


@pytest.mark.parametrize("failure", ["missing", "yaml", "schema"])
def test_doctor_after_hold_reports_expected_config_failure_not_escape(boundary, failure):
    boundary.start(boundary.ticket(la.SetupAttempt()))
    if failure == "missing":
        boundary.settings.config_path.unlink()
    else:
        boundary.settings.config_path.write_text("llm: [" if failure == "yaml" else "research_goals: []")
    result = doctor.run_doctor(boundary.settings, check_ids=["llm_inference", "dry_run"])
    assert not result["ready"] and result["request_hold"]
    assert all(row["status"] == "needs_action" for row in result["checks"]
               if row["id"] in {"llm_inference", "dry_run"})


@pytest.mark.parametrize("failure", ["yaml", "schema", "environment"])
def test_hold_helper_invalid_current_config_cannot_leave_cached_ready(monkeypatch, boundary, failure):
    boundary.start(boundary.ticket(la.SetupAttempt()))
    if failure == "environment":
        monkeypatch.setattr(doctor_requests, "read_config", lambda *_a: (_ for _ in ()).throw(ValueError()))
    else:
        boundary.settings.config_path.write_text("llm: [" if failure == "yaml" else "research_goals: []")
    payload = {"ready": True, "request_attempts": doctor_requests.request_attempts(boundary.settings),
               "checks": [doctor._row(key, "ready", "cached") for key in doctor._CHECKS]}
    doctor_requests.withhold_local_ready(boundary.settings, payload)
    assert not payload["ready"] and payload["request_hold"]
    for row in payload["checks"]:
        assert row["status"] == ("needs_action" if row["id"] in {"llm_inference", "dry_run"} else "ready")


@pytest.mark.parametrize("reason,submitted", [
    pytest.param("timeout", False, id="timeout-unaccepted"),
    pytest.param("timeout", True, id="timeout-submitted"),
    pytest.param("cancelled", False, id="cancelled-unaccepted"),
    pytest.param("cancelled", True, id="cancelled-submitted"),
])
def test_hosted_abandonment_diagnostic_is_locality_neutral(boundary, reason, submitted):
    attempt = la.SetupAttempt()
    if submitted:
        boundary.start(boundary.ticket(attempt, local=False), local=False)
    result = attempt.abandon(reason)
    assert result["reason_code"] == f"waiter_{reason}"
    assert result["ownership"] == ("unresolved" if submitted else "not_submitted")
    assert attempt.is_abandoned() and not boundary.held()
    stage = {"stage": "feed", "provider": "hosted", "model": "synthetic", "status": "fail", "detail": "Abandoned", **result}
    row = load_admission.verification_row([stage])
    assert row["status"] == "needs_action" and row["load_receipts"] == [stage]
    assert row["recovery"]["action"] == "open_ai_settings"


def test_local_unknown_refusal_uses_neutral_header_without_changing_receipt(monkeypatch, boundary):
    monkeypatch.setattr(load_admission, "_observe_executing_runtime", lambda *_a: {"effective_envelope": None})
    result = oc.probe_provider(routing().providers[0], "synthetic")
    row = load_admission.verification_row([{**result, "stage": "feed", "provider": "hosted", "model": "synthetic"}])
    assert result["reason_code"] == "effective_envelope_unknown" and result["admission"]["status"] == "unknown"
    assert result["ownership"] == "not_submitted" and not result["attempted"] and not boundary.held()
    assert row["status"] == "needs_action" and row["recovery"]["action"] == "open_ai_settings"


def test_hosted_operational_check_row_preserves_success_contract(boundary):
    attempt = la.SetupAttempt()
    completed(boundary, attempt, local=False)
    stage = {"status": "operational", "detail": "Synthetic native-event scope", "stage": "feed",
             "provider": "hosted", "model": "synthetic", "ownership": attempt.receipt()["ownership"]}
    row = load_admission.verification_row([stage])
    assert row["status"] == "ready" and row["load_receipts"] == [stage]
    assert stage["ownership"] == "completed" and not boundary.held()


def test_hold_helper_unexpected_programmer_failure_stays_loud(monkeypatch, boundary):
    boundary.start(boundary.ticket(la.SetupAttempt()))
    monkeypatch.setattr(doctor_requests, "read_config", lambda *_a: (_ for _ in ()).throw(RuntimeError("programmer defect")))
    payload = {"request_attempts": doctor_requests.request_attempts(boundary.settings)}
    with pytest.raises(RuntimeError, match="programmer defect"):
        doctor_requests.withhold_local_ready(boundary.settings, payload)
