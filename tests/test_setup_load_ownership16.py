"""Event-controlled setup ownership; every held callback is released and reaped."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from threading import Event

import pytest

from zotero_summarizer.models.providers import (
    DefaultModelConfig, LLMRoutingConfig, ProviderConfig, StageModelConfig, resolve_stage,
)
from zotero_summarizer.services.llm import operational_check as oc
from zotero_summarizer.services.setup import load_attempt as la
from zotero_summarizer.services.setup.load_admission import load_identity


@pytest.fixture(autouse=True)
def boundary(monkeypatch):
    # Private synthetic isolation, not a product reset API.
    state = la._LoadBoundary()
    monkeypatch.setattr(la, "_BOUNDARY", state)
    yield state
    assert state.worker.acquire(blocking=False), "owned callback must be reaped"
    state.worker.release()


def routing(local=True):
    return LLMRoutingConfig(providers=[ProviderConfig(name="hosted",
        base_url="http://localhost:11434/v1" if local else "https://synthetic.invalid/v1",
        api_key_env="TEST_API_KEY", num_ctx=1024)],
        default=DefaultModelConfig(provider="hosted", model="synthetic"))


def eligible(resolved):
    return dict(version=1, policy_ref="SYNTHETIC-not-measured", identity=load_identity(resolved),
        envelope=dict(context_tokens=1024, placement="cpu", memory_domain="host_available_bytes"),
        budget_bytes=10, reserve_bytes=2)


def facts():
    return dict(available_bytes=12, envelope=dict(
        context_tokens=1024, placement="cpu", memory_domain="host_available_bytes"))


def test_timeout_keeps_callback_lock_and_post_exit_sticky_hold(boundary, monkeypatch):
    routes = routing()
    routes.deep_review = StageModelConfig(model="unattempted")
    resolved = resolve_stage(routes, "feed")
    entered, release, exited = Event(), Event(), Event()
    attempt = la.SetupAttempt()
    constructions = []

    def load(submit):
        constructions.append(resolved.model)
        submit()
        entered.set()
        assert release.wait(5)
        return {"status": "operational", "detail": "synthetic response"}

    def probe(item):
        try:
            return la.run_setup_load(None, item, load, attempt=attempt,
                                     policy=eligible(item), facts=facts)
        finally:
            exited.set()

    async def timeout_on_event(work, *, timeout):
        task = asyncio.create_task(work)
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        raise asyncio.TimeoutError

    monkeypatch.setattr(oc.asyncio, "wait_for", timeout_on_event)

    async def scenario():
        try:
            result = await oc.check_routing_stages(routes, probe=probe,
                on_abandon=lambda r, why: la.abandoned_load(attempt, why))
            rows = result["stages"]
            assert rows[0]["attempted"] and rows[0]["callback_state"] == "active"
            assert rows[1]["attempted"]  # cached shared identity, exactly one worker
            assert rows[2]["attempted"] is False and rows[2]["ownership"] == "not_submitted"
            assert not boundary.worker.acquire(blocking=False)
            refused = la.run_setup_load(None, resolved, lambda _: pytest.fail("overlap"))
            assert refused["reason_code"] == "setup_load_busy"
            hosted = resolve_stage(routing(False), "feed")
            assert la.run_setup_load(None, hosted, lambda submit: (
                submit() or {"status": "operational"}))["status"] == "operational"
        finally:
            release.set()
            assert await asyncio.to_thread(exited.wait, 5)
        assert boundary.worker.acquire(blocking=False)
        boundary.worker.release()
        assert la.local_load_hold() == "waiter_timeout"
        held = la.run_setup_load(None, resolved, lambda _: pytest.fail("sticky HOLD"))
        assert held["reason_code"] == "local_load_hold" and not held["attempted"]

    asyncio.run(scenario())
    assert constructions == ["synthetic"]


def test_cancel_before_acceptance_is_not_submitted(boundary):
    resolved = resolve_stage(routing(), "feed")
    attempt = la.SetupAttempt()
    receipt = attempt.abandon("cancelled")
    result = la.run_setup_load(None, resolved, lambda _: pytest.fail("constructed"),
                              attempt=attempt, policy=eligible(resolved), facts=facts)
    assert not receipt["accepted"] and receipt["ownership"] == "not_submitted"
    assert result["reason_code"] == "attempt_abandoned"
    assert la.local_load_hold() is None


def test_cancel_after_submission_does_not_unlock_worker(boundary):
    resolved = resolve_stage(routing(), "feed")
    entered, release = Event(), Event()
    attempt = la.SetupAttempt()

    def load(submit):
        submit()
        entered.set()
        assert release.wait(5)
        return {"status": "operational"}

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(la.run_setup_load, None, resolved, load, attempt=attempt,
                             policy=eligible(resolved), facts=facts)
        try:
            assert entered.wait(5)
            receipt = attempt.abandon("cancelled")
            assert receipt["accepted"] and receipt["attempted"]
            assert receipt["ownership"] == "unresolved"
            assert not boundary.worker.acquire(blocking=False)
        finally:
            release.set()
        result = future.result(timeout=5)
    assert result["callback_state"] == "returned"
    assert la.local_load_hold() == "waiter_cancelled"


def test_cancel_during_construction_before_submit_has_no_false_hold(boundary):
    resolved = resolve_stage(routing(), "feed")
    entered, release = Event(), Event()
    attempt = la.SetupAttempt()

    def load(submit):
        entered.set()  # synthetic constructor, has not called the transport
        assert release.wait(5)
        submit()
        pytest.fail("cancelled attempt submitted")

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(la.run_setup_load, None, resolved, load, attempt=attempt,
                             policy=eligible(resolved), facts=facts)
        try:
            assert entered.wait(5)
            receipt = attempt.abandon("cancelled")
            assert receipt["accepted"] and not receipt["attempted"]
        finally:
            release.set()
        result = future.result(timeout=5)
    assert result["ownership"] == "not_submitted"
    assert la.local_load_hold() is None


def test_constructor_failure_not_submitted_but_normal_return_finality_unproven(boundary):
    resolved = resolve_stage(routing(), "feed")
    def broken(_submit):
        raise ValueError("invalid synthetic client config")
    result = la.run_setup_load(None, resolved, broken, policy=eligible(resolved), facts=facts)
    assert result["ownership"] == "not_submitted" and la.local_load_hold() is None
    result = la.run_setup_load(None, resolved, lambda submit: (
        submit() or {"status": "operational"}), policy=eligible(resolved), facts=facts)
    assert result["ownership"] == "unresolved" and result["callback_state"] == "returned"
    assert result["status"] != "operational" and la.local_load_hold()


def test_unique_callbacks_resample_not_outer_catalog(boundary):
    routes = routing()
    routes.deep_review = StageModelConfig(model="second")
    seen, loads = [], []
    available = iter([12, 1])
    def fresh():
        value = facts()
        value["available_bytes"] = next(available)
        seen.append(value["available_bytes"])
        return value
    def probe(resolved):
        return la.run_setup_load(None, resolved, lambda _: (
            loads.append(resolved.model) or {"status": "fail", "detail": "pre-submission error"}),
            policy=eligible(resolved), facts=fresh)
    result = asyncio.run(oc.check_routing_stages(routes, probe=probe))
    assert seen == [12, 1] and loads == ["synthetic"]
    assert result["stages"][2]["reason_code"] == "available_memory_insufficient"
    assert not result["stages"][2]["attempted"]


def test_callback_identity_is_frozen_against_original_routing_mutation(boundary):
    routes = routing(False)
    seen = []
    def probe(resolved):
        seen.append((resolved.model, resolved.provider.base_url))
        routes.default.model = "changed-after-capture"
        routes.providers[0].base_url = "http://localhost:11434/v1"
        return {"status": "operational", "detail": ""}
    result = asyncio.run(oc.check_routing_stages(routes, probe=probe))
    assert seen == [("synthetic", "https://synthetic.invalid/v1")]
    assert all(row["model"] == "synthetic" for row in result["stages"])


def test_previous_unique_probe_cannot_label_unaccepted_next_probe_attempted(boundary):
    first = resolve_stage(routing(False), "feed")
    attempt = la.SetupAttempt()
    assert la.run_setup_load(None, first, lambda submit: (
        submit() or {"status": "operational"}), attempt=attempt)["attempted"]
    next_target = first.model_copy(update={"model": "not-yet-accepted"})
    receipt = la.abandoned_load(attempt, "timeout", next_target)
    assert receipt["attempted"] is False and receipt["accepted"] is False
    assert receipt["ownership"] == "not_submitted" and receipt["abort_remaining"]
    assert la.local_load_hold() is None


def test_api_cancellation_retains_dry_callback_ownership_and_hold(tmp_path, monkeypatch, boundary):
    from types import SimpleNamespace
    from zotero_summarizer.api.routes import setup as api
    from zotero_summarizer.services.setup import doctor
    from zotero_summarizer.services.setup.bootstrap import bootstrap_phase0
    from zotero_summarizer.services.llm import factory
    from zotero_summarizer.services.triage import summarization
    from zotero_summarizer.settings import Settings
    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    entered, release, finished = Event(), Event(), Event()
    constructions = []
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    monkeypatch.setattr(la, "assess_setup_load", lambda s, r, **kw: {
        "status": "admitted", "identity": load_identity(r)})
    monkeypatch.setattr(factory, "build_client_for_stage", lambda r, **_kwargs: constructions.append(r) or object())

    def dry(*_args):
        entered.set()
        assert release.wait(5)
        return SimpleNamespace(reading_priority="could", relevance_score=1)

    monkeypatch.setattr(summarization, "run_abstract_dry", dry)
    original = doctor.run_doctor
    def run(*args, **kwargs):
        try:
            return original(*args, **kwargs)
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
            assert not boundary.worker.acquire(blocking=False)
            assert la.local_load_hold() == "waiter_cancelled"
        finally:
            release.set()
            with suppress(asyncio.CancelledError):
                await task
            assert await asyncio.to_thread(finished.wait, 5)
        assert doctor.doctor_status(settings)["ready"] is False
        assert boundary.worker.acquire(blocking=False)
        boundary.worker.release()
        assert la.local_load_hold()

    asyncio.run(scenario())
    assert len(constructions) == 1


def test_sticky_hold_reconciles_persisted_ready_and_modes_but_not_hosted(tmp_path, monkeypatch, boundary):
    from zotero_summarizer.services.setup import doctor
    from zotero_summarizer.services.setup.bootstrap import bootstrap_phase0
    from zotero_summarizer.services._common import read_config, write_user_config
    from zotero_summarizer.settings import Settings
    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    monkeypatch.setattr(doctor, "_RUNNERS", {key: (
        lambda _, key=key: doctor._row(key, "ready", "synthetic")) for key in doctor._CHECKS})
    assert doctor.run_doctor(settings)["ready"]
    with boundary.state:
        boundary.hold = "synthetic_finality_unproven"
    result = doctor.doctor_status(settings)
    assert not result["ready"] and result["status"] == "needs_action"
    assert result["modes"]["local_inference"] == "needs_action"
    assert result["modes"]["offline_ready"] == "needs_action"
    assert next(r for r in result["checks"] if r["id"] == "local_profile")["status"] == "ready"
    config = read_config(settings.config_path)
    config.llm_routing = routing(False)
    write_user_config(settings.config_path, config)
    assert doctor.run_doctor(settings)["ready"]


def test_doctor_abandonment_leaves_remaining_checks_unattempted(tmp_path, monkeypatch, boundary):
    from zotero_summarizer.services.setup import doctor
    from zotero_summarizer.services.setup.bootstrap import bootstrap_phase0
    from zotero_summarizer.settings import Settings
    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    attempt = la.SetupAttempt()
    def abandon(_settings):
        attempt.abandon("cancelled")
        return doctor._row("llm_inference", "needs_action", "synthetic cancelled")
    monkeypatch.setitem(doctor._RUNNERS, "llm_inference", abandon)
    monkeypatch.setitem(doctor._RUNNERS, "dry_run", lambda _: pytest.fail("remainder loaded"))
    result = doctor.run_doctor(settings, check_ids=["llm_inference", "dry_run"], attempt=attempt)
    dry = next(r for r in result["checks"] if r["id"] == "dry_run")
    assert dry["status"] == "needs_action" and "not attempted" in dry["message"]


def test_nonsetup_timeout_preserves_default_sibling_continuation(monkeypatch, boundary):
    routes = routing(False)
    routes.deep_review = StageModelConfig(model="second")
    calls = []
    async def controlled_wait(work, *, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            work.close()  # never dispatched; no held worker
            raise asyncio.TimeoutError
        return await work
    monkeypatch.setattr(oc.asyncio, "wait_for", controlled_wait)
    monkeypatch.setattr(oc, "probe_provider", lambda p, m: {"status": "operational", "detail": ""})
    result = asyncio.run(oc.check_routing_stages(routes))
    assert result["stages"][0]["status"] == "fail"
    assert result["stages"][1]["status"] == "fail"
    assert result["stages"][2]["status"] == "operational"
    assert calls == [oc._PROBE_TIMEOUT_SECS, oc._PROBE_TIMEOUT_SECS]


@pytest.mark.parametrize("failure", ["missing", "yaml", "schema"])
def test_doctor_after_hold_reports_expected_config_failure_not_escape(tmp_path, boundary, failure):
    from zotero_summarizer.services.setup import doctor
    from zotero_summarizer.services.setup.bootstrap import bootstrap_phase0
    from zotero_summarizer.settings import Settings
    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    with boundary.state:
        boundary.hold = "synthetic_finality_unproven"
    if failure == "missing":
        settings.config_path.unlink()
    else:
        settings.config_path.write_text("llm: [" if failure == "yaml" else "research_goals: []")
    result = doctor.run_doctor(settings, check_ids=["llm_inference", "dry_run"])
    assert result["ready"] is False and result["status"] == "needs_action"
    for row in result["checks"]:
        if row["id"] in {"llm_inference", "dry_run"}:
            assert row["status"] == "needs_action" and row["detail"]


@pytest.mark.parametrize("failure", ["yaml", "schema", "environment"])
def test_hold_helper_invalid_current_config_cannot_leave_cached_ready(tmp_path, monkeypatch, boundary, failure):
    from zotero_summarizer.services.setup import doctor
    from zotero_summarizer.services.setup.bootstrap import bootstrap_phase0
    from zotero_summarizer.settings import Settings
    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    with boundary.state:
        boundary.hold = "synthetic_finality_unproven"
    rows = [doctor._row(key, "ready", "cached synthetic success") for key in doctor._CHECKS]
    if failure == "environment":
        # The source bytes/signature need not change for a cast/validation failure.
        from zotero_summarizer.services import _common
        monkeypatch.setattr(_common, "read_config", lambda *args: (_ for _ in ()).throw(
            ValueError("synthetic invalid environment override")))
    else:
        settings.config_path.write_text("llm: [" if failure == "yaml" else "research_goals: []")
    la.withhold_local_ready(settings, rows)
    for row in rows:
        if row["id"] in {"llm_inference", "dry_run"}:
            assert row["status"] == "needs_action"
            assert "configuration" in row["message"].lower()
            assert row["recovery"]["action"] == "open_ai_settings"
        else:
            assert row["status"] == "ready"


@pytest.mark.parametrize("reason,submitted", [
    pytest.param("timeout", False, id="timeout-unaccepted"),
    pytest.param("timeout", True, id="timeout-submitted"),
    pytest.param("cancelled", False, id="cancelled-unaccepted"),
    pytest.param("cancelled", True, id="cancelled-submitted"),
])
def test_hosted_abandonment_diagnostic_is_locality_neutral(boundary, reason, submitted):
    resolved = resolve_stage(routing(False), "feed")
    assert resolved.provider.is_local is False
    attempt = la.SetupAttempt()
    if submitted:
        assert la.run_setup_load(None, resolved, lambda submit: (
            submit() or {"status": "operational"}), attempt=attempt)["attempted"]

    result = la.abandoned_load(attempt, reason, resolved)
    stage = {"provider": resolved.provider.name, "model": resolved.model,
             "stage": resolved.stage, **result}
    row = la.load_check_row("llm_inference", [stage], "Synthetic success")

    assert result["reason_code"] == f"waiter_{reason}" and result["abort_remaining"]
    assert result["accepted"] is submitted and result["attempted"] is submitted
    assert result["callback_state"] == ("returned" if submitted else "not_started")
    assert result["ownership"] == ("unresolved" if submitted else "not_submitted")
    assert attempt.is_abandoned() and la.local_load_hold() is None
    assert row["status"] == "needs_action" and row["load_receipts"] == [stage]
    assert row["detail"] == f"hosted/synthetic (feed): {result['detail']}"
    assert row["recovery"] == {"label": "Review endpoint settings", "action": "open_ai_settings"}
    assert row["message"] == "LLM verification needs action"


def test_local_unknown_refusal_uses_neutral_header_without_changing_receipt(tmp_path, boundary):
    from zotero_summarizer.settings import Settings
    resolved = resolve_stage(routing(), "feed")
    assert resolved.provider.is_local is True
    result = la.run_setup_load(Settings.load(project_root=tmp_path), resolved,
                               lambda _: pytest.fail("refused local callback constructed"))
    stage = {"provider": resolved.provider.name, "model": resolved.model,
             "stage": resolved.stage, **result}

    row = la.load_check_row("dry_run", [stage], "Synthetic dry success")

    assert result["reason_code"] == "policy_missing" and result["admission"]["status"] == "unknown"
    assert result["attempted"] is False and result["ownership"] == "not_submitted"
    assert row["status"] == "needs_action" and row["load_receipts"] == [stage]
    assert row["recovery"]["action"] == "open_ai_settings"
    assert la.local_load_hold() is None
    assert row["message"] == "LLM verification needs action"


def test_hosted_operational_check_row_preserves_success_contract(boundary):
    resolved = resolve_stage(routing(False), "feed")
    result = la.run_setup_load(None, resolved, lambda submit: (
        submit() or {"status": "operational", "detail": "Synthetic response"}))
    stage = {"provider": resolved.provider.name, "model": resolved.model,
             "stage": resolved.stage, **result}

    row = la.load_check_row("llm_inference", [stage], "Synthetic success")

    assert row["status"] == "ready" and row["message"] == "Synthetic success"
    assert row["load_receipts"] == [stage] and result["attempted"] is True
    assert row["recovery"] == {"label": "Retry inference", "action": "retry"}
    assert la.local_load_hold() is None


def test_hold_helper_unexpected_programmer_failure_stays_loud(tmp_path, monkeypatch, boundary):
    from zotero_summarizer.services import _common
    from zotero_summarizer.services.setup import doctor
    from zotero_summarizer.settings import Settings
    settings = Settings.load(project_root=tmp_path)
    with boundary.state:
        boundary.hold = "synthetic_finality_unproven"
    rows = [doctor._row(key, "ready", "cached") for key in doctor._CHECKS]
    monkeypatch.setattr(_common, "read_config", lambda *args: (_ for _ in ()).throw(
        RuntimeError("synthetic programmer defect")))
    with pytest.raises(RuntimeError, match="programmer defect"):
        la.withhold_local_ready(settings, rows)
