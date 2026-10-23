"""Synthetic admission/event controls; request completion is not capacity evidence."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest

from tests.test_setup_load_ownership16 import boundary, eligible, facts, routing  # noqa: F401
from zotero_summarizer.models.providers import resolve_stage
from zotero_summarizer.integrations.llm import InstrumentedLLMClient
from zotero_summarizer.services.setup import load_attempt as la


def completed_request(attempt, reason="stop", *, run_id=None):
    run_id = run_id or uuid4()
    attempt.request_expected()
    attempt.request_started(run_id)
    attempt.request_finished(run_id, reason)
    return run_id


def run_observed(load, attempt=None):
    resolved = resolve_stage(routing(), "feed")
    return la.run_setup_load(None, resolved, load, attempt=attempt,
                             policy=eligible(resolved), facts=facts)


@pytest.mark.parametrize("count", [1, 2])
def test_all_observed_requests_finish_without_false_global_idle_requirement(boundary, count):
    attempt = la.SetupAttempt()

    def load(_submit):
        for _ in range(count):
            completed_request(attempt)
        return {"status": "operational", "detail": "Synthetic terminal responses"}

    result = run_observed(load, attempt)
    assert result["status"] == "operational" and result["ownership"] == "completed"
    assert result["request_completion"] == {
        "expected": count, "started": count, "terminal": count, "invalid_events": False,
    }
    assert la.local_load_hold() is None


@pytest.mark.parametrize("observed", [0, 1])
def test_cached_or_unobserved_subset_cannot_certify_another_expected_request(boundary, observed):
    attempt = la.SetupAttempt()

    class CachedResult:
        def prompt(self, _prompt, **_kwargs):
            return "Cached text without request events"

    def load(_submit):
        if observed:
            completed_request(attempt)
        client = InstrumentedLLMClient(CachedResult(), request_observer=attempt)
        assert client.prompt("Synthetic cache control")
        return {"status": "operational"}

    result = run_observed(load, attempt)
    assert result["status"] == "fail"
    assert result["request_completion"]["expected"] == observed + 1
    assert result["request_completion"]["started"] == observed
    assert result["ownership"] == ("unresolved" if observed else "not_submitted")
    assert bool(la.local_load_hold()) is bool(observed)


def test_two_starts_with_one_terminal_remain_unresolved(boundary):
    attempt = la.SetupAttempt()

    def load(_submit):
        completed_request(attempt)
        attempt.request_expected()
        attempt.request_started(uuid4())
        return {"status": "operational"}

    result = run_observed(load, attempt)
    assert result["status"] == "fail" and result["reason_code"] == "local_load_hold"
    assert result["request_completion"]["started"] == 2
    assert result["request_completion"]["terminal"] == 1


@pytest.mark.parametrize("reason", [None, "unknown", {}, []])
def test_unknown_terminal_metadata_never_authorizes_success(boundary, reason):
    attempt = la.SetupAttempt()
    result = run_observed(lambda _: (completed_request(attempt, reason) or {"status": "operational"}), attempt)
    assert result["status"] == "fail" and result["ownership"] == "unresolved"
    assert result["request_completion"]["invalid_events"] is True
    assert la.local_load_hold()


@pytest.mark.parametrize("event", ["duplicate_start", "duplicate_end", "orphan_end"])
def test_duplicate_or_orphan_events_cannot_authorize_success(boundary, event):
    attempt = la.SetupAttempt()

    def load(_submit):
        run_id = completed_request(attempt)
        if event == "duplicate_start":
            attempt.request_expected()
            attempt.request_started(run_id)
        else:
            attempt.request_finished(run_id if event == "duplicate_end" else uuid4(), "stop")
        return {"status": "operational"}

    result = run_observed(load, attempt)
    assert result["status"] == "fail" and result["request_completion"]["invalid_events"]
    assert result["ownership"] == "unresolved" and la.local_load_hold()


def test_run_ids_cannot_be_reused_by_the_next_unique_callback(boundary):
    attempt = la.SetupAttempt()
    run_id = uuid4()

    def load(_submit):
        completed_request(attempt, run_id=run_id)
        return {"status": "operational"}

    assert run_observed(load, attempt)["status"] == "operational"
    repeated = run_observed(load, attempt)
    assert repeated["status"] == "fail" and repeated["ownership"] == "not_submitted"
    assert repeated["request_completion"]["invalid_events"]
    assert la.local_load_hold() is None  # no second send was established


def test_terminal_length_proves_only_termination_and_never_readiness(boundary):
    attempt = la.SetupAttempt()

    def load(_submit):
        completed_request(attempt, "length")
        return {"status": "operational"}  # even a caller swallowing truncation cannot publish Ready

    result = run_observed(load, attempt)
    assert result["status"] == "fail" and result["reason_code"] == "request_output_truncated"
    assert result["ownership"] == "completed" and la.local_load_hold() is None


def test_completed_request_with_pipeline_failure_is_not_operational(boundary):
    attempt = la.SetupAttempt()

    def load(_submit):
        completed_request(attempt)
        raise ValueError("Synthetic parse failure after terminal response")

    result = run_observed(load, attempt)
    assert result["status"] == "fail" and result["ownership"] == "completed"
    assert la.local_load_hold() is None


def test_base_exception_still_finalizes_callback_and_holds_unresolved_submission(boundary):
    class Stopped(BaseException):
        pass

    attempt = la.SetupAttempt()

    def load(_submit):
        attempt.request_expected()
        attempt.request_started(uuid4())
        raise Stopped()

    with pytest.raises(Stopped):
        run_observed(load, attempt)
    assert attempt.receipt()["callback_state"] == "returned"
    assert la.local_load_hold() == "operation_finality_unproven"


@pytest.mark.parametrize("finish_before_cancel", [False, True])
def test_abandonment_keeps_lock_hold_and_fences_the_next_send(boundary, finish_before_cancel):
    attempt = la.SetupAttempt()
    entered, release = Event(), Event()
    dispatched = []
    run_id = uuid4()

    def load(_submit):
        attempt.request_expected()
        attempt.request_started(run_id)
        dispatched.append(run_id)
        if finish_before_cancel:
            attempt.request_finished(run_id, "stop")
        entered.set()
        assert release.wait(5)
        if not finish_before_cancel:
            attempt.request_finished(run_id, "stop")  # late terminal cannot clear HOLD
        attempt.request_expected()  # must fail before any next transport start
        dispatched.append(uuid4())
        return {"status": "operational"}

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(run_observed, load, attempt)
        try:
            assert entered.wait(5)
            attempt.abandon("cancelled")
            assert not boundary.worker.acquire(blocking=False)
            assert la.local_load_hold() == "waiter_cancelled"
        finally:
            release.set()
        result = pending.result(timeout=5)
    assert len(dispatched) == 1
    assert result["status"] == "fail" and result["ownership"] == "unresolved"
    assert la.local_load_hold() == "waiter_cancelled"
