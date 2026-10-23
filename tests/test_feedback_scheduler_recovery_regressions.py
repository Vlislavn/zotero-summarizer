"""Confirmed negative retry, delayed activation recovery and daemon shutdown."""
import asyncio
import json
import os
import signal
from dataclasses import replace
from datetime import timedelta

import pytest

from test_feedback_analysis_inputs import create_current_db
from test_feedback_analysis_operation import delayed_child
from test_feedback_analysis_scheduler import MONDAY, due, enabled
from zotero_summarizer.services.golden.feedback_analysis import operation, scheduler, schedule_state
from zotero_summarizer.services.triage.feeds import _loop


@pytest.mark.parametrize("source", ["missing", "corrupt"])
def test_repaired_prepublication_source_failure_retries(tmp_path, source):
    settings = enabled(tmp_path)
    settings.triage_db_path.unlink()
    if source == "corrupt":
        settings.triage_db_path.write_bytes(b"synthetic invalid SQLite")
    with pytest.raises(Exception) as caught:
        due(settings)
    failure = caught.value.feedback_receipt
    assert failure["published"] is False
    assert failure["publication_state"] == "not_published"
    assert failure["child"]["reaped"] and failure["child"]["group_absent"]
    if settings.triage_db_path.exists():
        settings.triage_db_path.unlink()
    create_current_db(settings)

    result = due(settings)

    assert result["completed_windows"] == 1
    assert result["status"] == "insufficient_data"
    assert len(list((settings.feedback_analysis_dir / "attempts").iterdir())) == 2
    assert len(list((settings.feedback_analysis_dir / "runs").iterdir())) == 1


@pytest.mark.parametrize("damage", ["missing", "unknown", "published", "child", "identity"])
def test_uncertain_negative_evidence_still_holds(tmp_path, monkeypatch, damage):
    settings = enabled(tmp_path)
    settings.triage_db_path.unlink()
    with pytest.raises(FileNotFoundError):
        due(settings)
    path = next(settings.feedback_analysis_dir.glob("attempts/*/failure.json"))
    receipt = json.loads(path.read_bytes())
    if damage == "missing":
        path.unlink()
    else:
        if damage == "unknown":
            receipt["published"] = None
        elif damage == "published":
            receipt["published"] = True
        elif damage == "child":
            receipt["child"]["group_absent"] = False
        else:
            receipt["attempt_id"] = "foreign"
        path.write_text(json.dumps(receipt))
    create_current_db(settings)
    monkeypatch.setattr(operation, "_launch", lambda *a: pytest.fail("uncertain attempt cannot relaunch"))

    with pytest.raises(schedule_state.UnconfirmedPublication):
        due(settings)

    assert len(list((settings.feedback_analysis_dir / "attempts").iterdir())) == 1
    assert not (settings.feedback_schedule_dir / "cursor.json").exists()


def test_initial_cursor_failure_recovers_old_window_after_week_rollover(tmp_path, monkeypatch):
    settings = enabled(tmp_path)
    original = scheduler.commit_cursor
    monkeypatch.setattr(scheduler, "commit_cursor", lambda *a: (_ for _ in ()).throw(OSError("cursor")))
    with pytest.raises(OSError):
        due(settings)
    monkeypatch.setattr(scheduler, "commit_cursor", original)

    result = due(settings, MONDAY + timedelta(days=7))

    assert result["completed_windows"] == 2
    cursor = json.loads((settings.feedback_schedule_dir / "cursor.json").read_bytes())
    assert cursor["activation_cutoff"] == "2026-10-12T00:00:00Z"
    assert cursor["last_confirmed_cutoff"] == "2026-10-19T00:00:00Z"
    assert len(list((settings.feedback_analysis_dir / "attempts").iterdir())) == 2
    assert len(list(settings.feedback_analysis_dir.glob("attempts/*/schedule-recovery.json"))) == 1


def test_dangling_cursor_refuses_before_child(tmp_path, monkeypatch):
    settings = enabled(tmp_path)
    schedule_state.private_root(settings)
    cursor = settings.feedback_schedule_dir / "cursor.json"
    cursor.symlink_to(settings.feedback_schedule_dir / "absent.json")
    monkeypatch.setattr(operation, "_launch", lambda *a: pytest.fail("invalid cursor cannot launch"))

    with pytest.raises(ValueError, match="symlink"):
        due(settings)

    assert cursor.is_symlink()
    assert not (settings.feedback_analysis_dir / "attempts").exists()


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_daemon_signal_during_scheduled_child_exits_cleanly(tmp_path, monkeypatch, signum):
    settings = enabled(tmp_path)
    delayed_child(monkeypatch)
    monkeypatch.setattr(_loop, "_load_config", lambda: {"feeds": {}})
    monkeypatch.setattr(_loop, "run_daemon_tick", lambda **kw: pytest.fail("shutdown must skip feed tick"))
    def interrupt(self, process=None):
        # This signal/cleanup unit test uses a synthetic sleeper, not the analysis worker.
        if process is not None:
            os.kill(os.getpid(), signum)
    monkeypatch.setattr(operation.OperationGuard, "check", interrupt)

    try:
        asyncio.run(_loop.run_daemon_loop(max_ticks=1, feedback_settings=settings))
    except (InterruptedError, KeyboardInterrupt) as exc:
        pytest.fail(f"daemon did not handle its shutdown signal: {type(exc).__name__}")

    failure = json.loads(next(settings.feedback_analysis_dir.glob("attempts/*/failure.json")).read_bytes())
    assert failure["child"]["reaped"] and failure["child"]["group_absent"]
    assert failure["published"] is False
    assert not (settings.feedback_schedule_dir / "cursor.json").exists()


@pytest.mark.parametrize("cap", [60, 120])
def test_cycle_cap_must_exceed_fixed_operation_cap_before_output(tmp_path, cap):
    settings = replace(enabled(tmp_path), feedback_schedule_wall_cap_seconds=cap)

    with pytest.raises(ValueError, match="schedule.*cap.*operation"):
        due(settings)

    assert not settings.feedback_analysis_dir.exists()


def test_daemon_does_not_hide_unrelated_interruption(tmp_path, monkeypatch):
    settings = enabled(tmp_path)
    monkeypatch.setattr(_loop, "_load_config", lambda: {"feeds": {}})
    async def interrupted(*args, **kwargs):
        raise InterruptedError("synthetic filesystem interruption")
    monkeypatch.setattr(scheduler, "run_due_feedback", interrupted)

    with pytest.raises(InterruptedError):
        asyncio.run(_loop.run_daemon_loop(max_ticks=1, feedback_settings=settings))
