"""Scripted current-owner inventory controls; not SDK/TCP or capacity qualification."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import httpx
import pytest

from tests.test_setup_load_ownership16 import boundary, completed  # noqa: F401
from zotero_summarizer.integrations.llm import InstrumentedLLMClient
from zotero_summarizer.integrations.llm_callbacks import CompletionGuard
from zotero_summarizer.services.setup import load_attempt as la


def run_observed(boundary, callback, attempt=None):
    attempt = attempt or la.SetupAttempt()
    return attempt.run(callback, timeout=5)


@pytest.mark.parametrize("count", [1, 2])
def test_all_observed_requests_finish_without_false_global_idle_requirement(boundary, count):
    attempt = la.SetupAttempt()
    def load():
        for _ in range(count):
            completed(boundary, attempt)
        return {"status": "operational"}
    result = run_observed(boundary, load, attempt)
    assert result["status"] == "operational" and result["ownership"] == "completed"
    inventory = result["request_completion"]
    assert [inventory[key] for key in ("expected", "started", "terminal")] == [count] * 3
    assert inventory["coherent"] and inventory["successful"] and not inventory["invalid_events"]
    assert not boundary.held()


@pytest.mark.parametrize("observed", [0, 1])
def test_cached_or_unobserved_subset_cannot_certify_another_expected_request(boundary, observed):
    attempt = la.SetupAttempt()
    class CachedResult:
        def prompt(self, _prompt, **_kwargs):
            return "Unobserved text; NOT positive cache-only/no-send proof"
    def load():
        if observed:
            completed(boundary, attempt)
        guard = CompletionGuard(expected_model="synthetic", request_owner=lambda: boundary.ticket(attempt))
        client = InstrumentedLLMClient(CachedResult(), verification_guard=guard)
        client.prompt("Synthetic invocation")
        return {"status": "operational"}
    result = run_observed(boundary, load, attempt)
    inventory = result["request_completion"]
    assert result["status"] == "fail" and result["ownership"] == "unknown"
    assert inventory["expected"] == observed + 1 and inventory["started"] == inventory["terminal"] == observed
    assert inventory["uncertainty"] and inventory["fence"] and not inventory["hold"]
    assert boundary.held()  # uncertainty fence, NOT a fabricated live request
    with pytest.raises(RuntimeError):
        boundary.ticket(la.SetupAttempt())


def test_two_starts_with_one_terminal_remain_unresolved(boundary):
    attempt = la.SetupAttempt()
    def load():
        completed(boundary, attempt)
        boundary.start(boundary.ticket(attempt))
        return {"status": "operational"}
    result = run_observed(boundary, load, attempt)
    assert result["status"] == "fail" and result["ownership"] == "unresolved"
    assert result["request_completion"]["started"] == 2 and result["request_completion"]["terminal"] == 1
    assert boundary.held()


@pytest.mark.parametrize("reason", [None, "unknown", {}, []])
def test_unknown_terminal_metadata_never_authorizes_success(boundary, reason):
    attempt = la.SetupAttempt()
    def load():
        request = boundary.ticket(attempt)
        native_id = boundary.start(request)
        request.terminal("synthetic", reason, native_id=native_id, native_required=True)
        return {"status": "operational"}
    result = run_observed(boundary, load, attempt)
    assert result["status"] == "fail" and result["ownership"] == "unresolved"
    assert result["request_completion"]["invalid_events"] and boundary.held()


@pytest.mark.parametrize("event", ["duplicate_start", "duplicate_end", "orphan_end"])
def test_duplicate_or_orphan_events_cannot_authorize_success(boundary, event):
    attempt = la.SetupAttempt()
    def load():
        request = completed(boundary, attempt)
        if event == "duplicate_start":
            boundary.start(request)
        elif event == "duplicate_end":
            request.terminal("synthetic", "stop")
        else:
            orphan = boundary.ticket(attempt)
            orphan.terminal("synthetic", "stop", native_id=uuid4(), native_required=True)
        return {"status": "operational"}
    result = run_observed(boundary, load, attempt)
    assert result["status"] == "fail" and result["request_completion"]["invalid_events"]
    assert result["request_completion"]["fence"] and boundary.held()
    assert result["ownership"] == ("unknown" if event == "orphan_end" else "unresolved")


def test_run_ids_cannot_be_reused_by_the_next_unique_callback(boundary):
    attempt = la.SetupAttempt()
    native_id = uuid4()
    request = boundary.ticket(attempt)
    request.native_started(native_id)
    request.transport_started(httpx.Request("POST", "http://localhost:11434/v1/chat/completions", json={"model": "synthetic"}))
    request.terminal("synthetic", "stop", native_id=native_id, native_required=True)
    def load():
        second = boundary.ticket(attempt)
        second.native_started(native_id)  # inline rejection BEFORE any second send
        pytest.fail("reused native callback identity reached transport")
    result = run_observed(boundary, load, attempt)
    assert result["status"] == "fail" and result["request_completion"]["invalid_events"]
    assert result["request_completion"]["expected"] == 1
    assert result["request_completion"]["started"] == result["request_completion"]["terminal"] == 0
    assert attempt.receipt()["request_completion"]["started"] == attempt.receipt()["request_completion"]["terminal"] == 1
    assert attempt.requests.receipts()[-1]["not_submitted"] is True
    assert not result["request_completion"]["fence"] and not boundary.held()


def test_terminal_length_proves_only_termination_and_never_readiness(boundary):
    attempt = la.SetupAttempt()
    def load():
        request = boundary.ticket(attempt)
        native_id = boundary.start(request)
        request.terminal("synthetic", "length", native_id=native_id, native_required=True)
        return {"status": "operational"}  # swallowing truncation cannot publish Ready
    result = run_observed(boundary, load, attempt)
    assert result["status"] == "fail" and result["reason_code"] == "request_output_truncated"
    assert result["ownership"] == "completed" and not boundary.held()


def test_completed_request_with_pipeline_failure_is_not_operational(boundary):
    attempt = la.SetupAttempt()
    def load():
        completed(boundary, attempt)
        raise ValueError("Synthetic parse failure")
    result = run_observed(boundary, load, attempt)
    assert result["status"] == "fail" and result["ownership"] == "completed" and not boundary.held()


def test_base_exception_still_finalizes_callback_and_holds_unresolved_submission(boundary):
    class Stopped(BaseException):
        pass
    attempt = la.SetupAttempt()
    def load():
        boundary.start(boundary.ticket(attempt))
        raise Stopped()
    with pytest.raises(Stopped):
        run_observed(boundary, load, attempt)
    assert attempt.receipt()["callback_state"] == "returned" and boundary.held()


@pytest.mark.parametrize("finish_before_cancel", [False, True])
def test_abandonment_keeps_lock_hold_and_fences_the_next_send(boundary, finish_before_cancel):
    attempt = la.SetupAttempt()
    entered, release = Event(), Event()
    dispatched = []
    def load():
        request = boundary.ticket(attempt)
        native_id = boundary.start(request)
        dispatched.append(native_id)
        if finish_before_cancel:
            request.terminal("synthetic", "stop", native_id=native_id, native_required=True)
        entered.set()
        assert release.wait(5)
        if not finish_before_cancel:
            request.terminal("synthetic", "stop", native_id=native_id, native_required=True)
        boundary.ticket(attempt)  # cancellation forbids next expected request
        dispatched.append(uuid4())
        return {"status": "operational"}
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(run_observed, boundary, load, attempt)
        try:
            assert entered.wait(5)
            attempt.abandon("cancelled")
            assert not boundary.worker.acquire(blocking=False)
            assert boundary.held() is (not finish_before_cancel)
            result = pending.result(timeout=5)  # waiter returns BEFORE the held callback
            assert result["ownership"] == ("completed" if finish_before_cancel else "unresolved")
        finally:
            release.set()
        assert boundary.worker.acquire(timeout=5)
        boundary.worker.release()
    assert len(dispatched) == 1 and result["status"] == "fail"
    assert attempt.receipt()["ownership"] == "completed" and not boundary.held()
    assert attempt.is_abandoned()  # exact late reconciliation is NOT permission to resume


@pytest.mark.parametrize("foreign", ["model", "native_run_id"])
def test_foreign_terminal_cannot_reconcile_the_owned_request(boundary, foreign):
    attempt = la.SetupAttempt()
    request = boundary.ticket(attempt)
    native_id = boundary.start(request)
    with pytest.raises(ValueError):
        request.terminal("other-model" if foreign == "model" else "synthetic", "stop",
                         native_id=uuid4() if foreign == "native_run_id" else native_id, native_required=True)
    assert attempt.receipt()["ownership"] == "unresolved" and boundary.held()
    with pytest.raises(ValueError):
        request.terminal("synthetic", "stop", native_id=native_id, native_required=True)
    assert boundary.held()  # invalid evidence is sticky, not repaired by a later plausible event


def test_exact_late_terminal_survives_successor_config_and_credential_publication(boundary):
    from zotero_summarizer.services.setup import doctor, doctor_requests
    original = la.SetupAttempt()
    request = boundary.ticket(original)
    native_id = boundary.start(request)
    original.abandon("timeout")
    doctor._invalidate_doctor_unlocked(boundary.settings)
    successor = la.SetupAttempt()
    doctor._save(boundary.settings, {"run_id": successor.run_id, "ready": True, "status": "ready",
                                    "checks": [doctor._row(key, "ready", "synthetic") for key in doctor._CHECKS]})
    held = doctor.doctor_status(boundary.settings)
    assert not held["ready"] and held["request_hold"]
    assert original.run_id in held["request_attempts"]
    request.terminal("synthetic", "stop", native_id=native_id, native_required=True)
    assert not boundary.held() and original.is_abandoned()
    from zotero_summarizer.services._common import read_json_or_empty
    saved = read_json_or_empty(boundary.settings.data_dir / "setup_doctor.json")
    assert saved["run_id"] == successor.run_id and not saved["ready"]  # reconciliation cannot publish Ready
    assert doctor_requests.request_attempts(boundary.settings)[original.run_id]["completion"]["coherent"]


def test_cancel_at_owned_transport_boundary_is_positive_no_dispatch_proof(boundary):
    attempt = la.SetupAttempt()
    request = boundary.ticket(attempt)
    request.native_started(uuid4())
    attempt.abandon("cancelled")
    with pytest.raises(RuntimeError):
        request.transport_started(httpx.Request("POST", "http://localhost:11434/v1/chat/completions", json={"model": "synthetic"}))
    row = attempt.requests.receipts()[0]
    assert row["not_submitted"] and not row["started"]
    assert attempt.receipt()["ownership"] == "not_submitted" and not boundary.held()


def test_malformed_persisted_request_state_fences_without_inventing_live_work(boundary):
    from zotero_summarizer.services._common import write_json_atomic
    from zotero_summarizer.services.setup import doctor_requests
    write_json_atomic(boundary.settings.data_dir / "setup_doctor.json", {"request_attempts": {"bad": {}}})
    payload = {"ready": True, "request_attempts": doctor_requests.request_attempts(boundary.settings), "checks": []}
    doctor_requests.withhold_local_ready(boundary.settings, payload)
    assert not payload["ready"] and payload["request_fence"] and payload["request_hold"] is None
    assert payload["request_state"] == "invalid"
    with pytest.raises(RuntimeError):
        boundary.ticket(la.SetupAttempt())


@pytest.mark.parametrize("corruption", ["summary", "identity", "success_without_terminal", "reason_type"])
def test_persisted_request_semantics_cannot_forge_observed_completion(boundary, corruption):
    from zotero_summarizer.services._common import read_json_or_empty, write_json_atomic
    from zotero_summarizer.services.setup import doctor_requests

    attempt = la.SetupAttempt()
    completed(boundary, attempt)
    path = boundary.settings.data_dir / "setup_doctor.json"
    payload = read_json_or_empty(path)
    saved = payload["request_attempts"][attempt.run_id]
    if corruption == "summary":
        saved["completion"]["successful"] = False
    elif corruption == "identity":
        saved["requests"][0]["identity"] = ""
    elif corruption == "success_without_terminal":
        saved["requests"][0]["terminal"] = False
        saved["completion"]["terminal"] = 0
    else:
        saved["requests"][0]["reason_code"] = {}
    write_json_atomic(path, payload)
    projection = {"ready": True, "checks": [], "request_attempts": doctor_requests.request_attempts(boundary.settings)}
    doctor_requests.withhold_local_ready(boundary.settings, projection)
    assert projection["request_state"] == "invalid" and projection["request_fence"]
    assert not projection["ready"] and projection["request_hold"] is None
    with pytest.raises(RuntimeError):
        boundary.ticket(la.SetupAttempt())
