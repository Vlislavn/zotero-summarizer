"""Explicit CLI capability, ownership, bounded cancellation and calendar checks."""
import asyncio
import json
import os
import signal
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from test_feedback_analysis_history import event, log, project
from test_feedback_analysis_operation import delayed_child
from test_feedback_analysis_scheduler import MONDAY, due, enabled
from zotero_summarizer.services.golden.feedback_analysis import operation, scheduler, schedule_state
from zotero_summarizer.services.triage.feeds import _common, _loop
from zotero_summarizer.settings import Settings

_DEFAULT_TEST_CAP_SECONDS = 5.0
_MAX_TEST_CAP_SECONDS = 60.0
_TEST_CAP_SECONDS = float(os.getenv("ZS_FEEDBACK_SCHEDULER_TEST_CAP_SECONDS", str(_DEFAULT_TEST_CAP_SECONDS)))
if not 0 < _TEST_CAP_SECONDS <= _MAX_TEST_CAP_SECONDS:
    raise ValueError("ZS_FEEDBACK_SCHEDULER_TEST_CAP_SECONDS must be positive and <=60")


def report():
    return _common.DaemonTickReport(tick_id="synthetic", fetched=0, skipped_already_processed=0,
        skipped_library_dedup=0, triaged=0, fast_rejected=0, errors=0, marked_read=0, outcomes_resolved=0)


def test_default_disabled_and_off_main_refuse_without_output(tmp_path):
    settings = project(tmp_path)
    assert due(settings)["status"] == "disabled"
    assert not settings.feedback_analysis_dir.exists()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(RuntimeError):
            pool.submit(due, replace(settings, feedback_schedule_enabled=True)).result(timeout=_TEST_CAP_SECONDS)
        with pytest.raises(RuntimeError):
            pool.submit(operation.run_scheduled_feedback_analysis, settings, cutoff=MONDAY,
                provenance={}, schedule_intent=scheduler.weekly_intent(MONDAY)).result(timeout=_TEST_CAP_SECONDS)
    assert not settings.feedback_analysis_dir.exists()


def test_concurrent_cycle_owner_refuses_before_child(tmp_path, monkeypatch):
    settings = enabled(tmp_path)
    monkeypatch.setattr(operation, "_launch", lambda *a: pytest.fail("busy cycle cannot launch"))
    root = schedule_state.private_root(settings)
    with operation._single_run(root):
        with pytest.raises(BlockingIOError):
            due(settings)
        import subprocess
        import sys
        code = "import fcntl,os,sys; fd=os.open(sys.argv[1],os.O_RDWR)\ntry: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)\nexcept BlockingIOError: print('busy')\nelse: sys.exit(9)"
        probe = subprocess.run([sys.executable, "-I", "-c", code, str(root / ".operation.lock")],
                               capture_output=True, text=True, timeout=_TEST_CAP_SECONDS)
        assert probe.returncode == 0 and probe.stdout.strip() == "busy"
    assert not (settings.feedback_analysis_dir / "attempts").exists()


def test_task_cancel_after_owned_publication_does_not_commit_cursor(tmp_path, monkeypatch):
    settings = enabled(tmp_path)
    log(settings, [event("A")])
    original = operation._run_feedback_analysis
    def cancel_after_return(*args, **kwargs):
        receipt = original(*args, **kwargs)
        asyncio.current_task().cancel()
        return receipt
    monkeypatch.setattr(operation, "_run_feedback_analysis", cancel_after_return)
    with pytest.raises(asyncio.CancelledError):
        due(settings)
    assert not (settings.feedback_schedule_dir / "cursor.json").exists()
    receipts = list(settings.feedback_analysis_dir.glob("attempts/*/receipt.json"))
    assert len(receipts) == 1
    child = json.loads(receipts[0].read_bytes())["child"]
    assert child["reaped"] and child["group_absent"]


def test_sigterm_during_owned_child_restores_handlers_no_cursor(tmp_path, monkeypatch):
    settings = enabled(tmp_path)
    delayed_child(monkeypatch)
    original_handler = signal.getsignal(signal.SIGTERM)
    original_check = operation.OperationGuard.check
    def interrupt(self, process=None):
        original_check(self, process)
        if process is not None:
            os.kill(os.getpid(), signal.SIGTERM)
    monkeypatch.setattr(operation.OperationGuard, "check", interrupt)
    with pytest.raises(InterruptedError):
        due(settings)
    assert signal.getsignal(signal.SIGTERM) is original_handler
    assert not (settings.feedback_schedule_dir / "cursor.json").exists()
    failure = json.loads(next(settings.feedback_analysis_dir.glob("attempts/*/failure.json")).read_bytes())
    assert failure["published"] is False
    assert failure["child"]["reaped"] and failure["child"]["group_absent"]


def test_explicit_cli_serve_passes_selected_settings_only(tmp_path, monkeypatch):
    from zotero_summarizer.cli import _feeds, build_parser
    from zotero_summarizer.services.triage import feeds
    settings = project(tmp_path)
    seen = []
    monkeypatch.setattr(_feeds, "_bootstrap_feeds_cli", lambda args: (settings, [2]))
    async def loop(**kwargs):
        assert threading.current_thread() is threading.main_thread()
        seen.append(kwargs)
    monkeypatch.setattr(feeds, "run_daemon_loop", loop)
    args = build_parser().parse_args(["feeds", "serve", "--max-ticks", "1"])
    assert args.func(args) == 0
    assert seen == [{"feed_library_ids": [2], "max_ticks": 1, "feedback_settings": settings}]


@pytest.mark.parametrize("capability", [False, True])
def test_loop_never_implicitly_activates_from_a_tick(tmp_path, monkeypatch, capability):
    settings = replace(project(tmp_path), feedback_schedule_enabled=capability)
    seen = []
    monkeypatch.setattr(_loop, "_load_config", lambda: {"feeds": {}})
    monkeypatch.setattr(_loop, "run_daemon_tick", lambda **kw: report())
    async def scheduled(selected, **kwargs):
        assert threading.current_thread() is threading.main_thread()
        seen.append(selected)
        return {"status": "disabled"}
    monkeypatch.setattr(scheduler, "run_due_feedback", scheduled)
    asyncio.run(_loop.run_daemon_loop(max_ticks=1, feedback_settings=settings if capability else None))
    assert seen == ([settings] if capability else [])


def test_arithmetic_catchup_max_two_no_data_and_no_due(tmp_path, monkeypatch):
    settings = enabled(tmp_path)
    seen = []
    def synthetic(*args, **kwargs):
        cutoff = kwargs["cutoff"]
        seen.append(cutoff)
        intent = scheduler.weekly_intent(cutoff)
        run = settings.feedback_analysis_dir / "runs/fixture"
        attempt = settings.feedback_analysis_dir / "attempts/fixture"
        return {"schema_version": 1, "intent_id": intent["intent_id"], "window_start": intent["window_start"],
                "status": "insufficient_data", "confirmed_report": True, "cutoff": intent["cutoff"],
                "source_identity": "a" * 64, "recovery": False, "publication_action": "committed",
                "owned_published": True, "content_key": "b" * 64, "run_path": str(run),
                "operation_receipt_path": str(attempt / "receipt.json"),
                "schedule_receipt_path": str(attempt / "schedule-receipt.json"),
                "late_observations": {"at_or_before_previous_cutoff": 0,
                    "basis": "retained observations, not new-action identity", "result_path": str(run / "result.json")}}
    monkeypatch.setattr(operation, "_run_feedback_analysis", synthetic)
    first = due(settings)
    assert first["completed_windows"] == 1 and seen == [MONDAY]
    later = due(settings, MONDAY + timedelta(days=35))
    assert later["completed_windows"] == 2 and later["status"] == "no_new_data"
    assert seen == [MONDAY, MONDAY + timedelta(days=7), MONDAY + timedelta(days=14)]
    assert scheduler.closed_cutoff(MONDAY + timedelta(days=6, hours=23)) == MONDAY


@pytest.mark.parametrize("env,value", [("ENABLED", "maybe"), ("MAX_WINDOWS", "5"),
    ("MAX_WINDOWS", "0"), ("WALL_CAP_SECONDS", "nan"), ("WALL_CAP_SECONDS", "601")])
def test_schedule_settings_invalid_env_refuses(env, value, tmp_path, monkeypatch):
    monkeypatch.setenv("ZS_FEEDBACK_SCHEDULE_" + env, value)
    with pytest.raises(ValueError):
        Settings.load(project_root=tmp_path)
    assert not (tmp_path / "data").exists()
