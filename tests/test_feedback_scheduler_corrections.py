"""Parent CAPA counterexamples and affirmative scheduled-finalization boundaries."""
import asyncio
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_feedback_analysis_scheduler import MONDAY, due, enabled
from zotero_summarizer.services.golden.feedback_analysis import operation, scheduler, schedule_state

_DEFAULT_TEST_CAP_SECONDS = 20.0
_MAX_TEST_CAP_SECONDS = 60.0
_TEST_CAP_SECONDS = float(os.getenv("ZS_FEEDBACK_SCHEDULER_TEST_CAP_SECONDS", str(_DEFAULT_TEST_CAP_SECONDS)))
if not 0 < _TEST_CAP_SECONDS <= _MAX_TEST_CAP_SECONDS:
    raise ValueError("ZS_FEEDBACK_SCHEDULER_TEST_CAP_SECONDS must be positive and <=60")


@pytest.mark.parametrize("target,after", [("receipt.json", False), ("receipt.json", True),
    ("schedule-receipt.json", False), ("schedule-receipt.json", True)])
def test_failed_writers_and_failed_negative_evidence_hold(tmp_path, monkeypatch, target, after):
    settings = enabled(tmp_path)
    original, original_status = operation.write_protocol, scheduler._status
    primary = PermissionError("synthetic required writer refusal")
    def fail(path, payload):
        if path.name == target:
            if after:
                original(path, payload)
            raise primary
        if path.name == "failure.json":
            raise PermissionError("synthetic negative evidence refusal")
        return original(path, payload)
    monkeypatch.setattr(operation, "write_protocol", fail)
    monkeypatch.setattr(schedule_state, "write_protocol", fail)
    monkeypatch.setattr(scheduler, "_status", lambda *a: (_ for _ in ()).throw(PermissionError("status")))
    with pytest.raises(PermissionError) as caught:
        due(settings)
    assert caught.value is primary
    failure = primary.feedback_receipt
    assert failure["published"] is True
    assert failure["receipt_persistence"]["status"] == "failed"
    assert primary.schedule_status_persistence_error == "PermissionError"
    assert failure["child"]["reaped"] and failure["child"]["group_absent"]
    attempts = list((settings.feedback_analysis_dir / "attempts").iterdir())
    assert len(attempts) == 1 and not (attempts[0] / "failure.json").exists()
    monkeypatch.setattr(operation, "write_protocol", original)
    monkeypatch.setattr(schedule_state, "write_protocol", original)
    monkeypatch.setattr(scheduler, "_status", original_status)
    monkeypatch.setattr(operation, "_launch", lambda *a: pytest.fail("unconfirmed attempt cannot launch"))
    for _ in range(2):
        with pytest.raises(schedule_state.UnconfirmedPublication):
            due(settings)
    assert not (settings.feedback_schedule_dir / "cursor.json").exists()
    assert list((settings.feedback_analysis_dir / "attempts").iterdir()) == attempts
    assert len(list((settings.feedback_analysis_dir / "runs").iterdir())) == 1


@pytest.mark.parametrize("source", ["config_path", "triage_db_path", "golden_csv_path",
    "interaction_log_path", "classifier_runs_log_path"])
@pytest.mark.parametrize("location", ["status.json", "cursor.json", ".operation.lock"])
@pytest.mark.parametrize("entry", ["due", "sibling", "private_root"])
def test_source_admission_precedes_all_namespace_io(tmp_path, monkeypatch, source, location, entry):
    settings = enabled(tmp_path)
    settings.feedback_analysis_dir.mkdir(mode=0o700)
    settings.feedback_schedule_dir.mkdir(mode=0o700)
    path = settings.feedback_schedule_dir / location
    sentinel = b"synthetic selected source; do not change\n"
    path.write_bytes(sentinel)
    path.chmod(0o600)
    before = {str(p.relative_to(settings.feedback_analysis_dir)): p.stat().st_mode
              for p in settings.feedback_analysis_dir.rglob("*")}
    if source == "classifier_runs_log_path":
        keys = (*operation.SETTINGS_KEYS, "feedback_schedule_dir", "feedback_schedule_enabled",
                "feedback_schedule_max_windows", "feedback_schedule_wall_cap_seconds")
        selected = SimpleNamespace(**({key: getattr(settings, key) for key in keys} | {source: path}))
    else:
        selected = replace(settings, **{source: path})
    monkeypatch.setattr(operation, "resolve_budgets", lambda *a: pytest.fail("admission must precede budgets"))
    with pytest.raises(ValueError):
        if entry == "due":
            due(selected)
        elif entry == "sibling":
            operation.run_scheduled_feedback_analysis(selected, cutoff=MONDAY, provenance={},
                                                      schedule_intent=scheduler.weekly_intent(MONDAY))
        else:
            schedule_state.private_root(selected)
    assert path.read_bytes() == sentinel
    assert {str(p.relative_to(settings.feedback_analysis_dir)): p.stat().st_mode
            for p in settings.feedback_analysis_dir.rglob("*")} == before


@pytest.mark.parametrize("source", ["config_path", "interaction_log_path"])
def test_actual_overlap_refusal_never_overwrites_status_source(tmp_path, source):
    settings = enabled(tmp_path)
    settings.feedback_analysis_dir.mkdir(mode=0o700)
    settings.feedback_schedule_dir.mkdir(mode=0o700)
    path = settings.feedback_schedule_dir / "status.json"
    sentinel = b"synthetic input source\n"
    path.write_bytes(sentinel)
    path.chmod(0o600)
    with pytest.raises(ValueError) as caught:
        due(replace(settings, **{source: path}))
    assert type(caught.value) is ValueError
    assert path.read_bytes() == sentinel


def test_missing_fcntl_disabled_daemon_and_cli_import_are_supported(tmp_path):
    code = '''
import asyncio, builtins, sys
from pathlib import Path
from types import SimpleNamespace
original = builtins.__import__
def deny(name, *args, **kwargs):
    if name == "fcntl": raise ModuleNotFoundError("synthetic unavailable fcntl")
    return original(name, *args, **kwargs)
builtins.__import__ = deny
from zotero_summarizer.cli import build_parser
from zotero_summarizer.services.triage.feeds import _loop
from zotero_summarizer.settings import Settings
from dataclasses import replace
settings = Settings.load(project_root=Path(sys.argv[1]), env_path=Path(sys.argv[1]) / "absent.env")
assert settings.feedback_schedule_enabled is False
build_parser().parse_args(["feeds", "serve", "--max-ticks", "1"])
_loop._load_config = lambda: {"feeds": {}}
_loop.run_daemon_tick = lambda **kwargs: SimpleNamespace(errors=[], fatal_llm_error=False, as_dict=lambda: {})
asyncio.run(_loop.run_daemon_loop(max_ticks=1, feedback_settings=settings))
assert "zotero_summarizer.services.golden.feedback_analysis.scheduler" not in sys.modules
from zotero_summarizer.services.golden.feedback_analysis import scheduler
try:
    asyncio.run(scheduler.run_due_feedback(replace(settings, feedback_schedule_enabled=True),
                                          now=__import__("datetime").datetime(2026,10,12,tzinfo=__import__("datetime").timezone.utc)))
except NotImplementedError:
    print("disabled-importable; activated-refusal")
else:
    raise AssertionError("unsupported activation did not refuse")
'''
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True,
                            text=True, env=os.environ.copy(), timeout=_TEST_CAP_SECONDS)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "disabled-importable; activated-refusal"


@pytest.mark.parametrize("fault", ["prepare_after_write", "before_move", "foreign_exact_copy"])
def test_confirmation_failure_or_foreign_marker_never_confirms(tmp_path, monkeypatch, fault):
    settings = enabled(tmp_path)
    original_move, original_write = operation._rename_exclusive, schedule_state.write_protocol
    def move(source, destination):
        if destination.name == "schedule-finalized":
            if fault == "before_move":
                raise PermissionError("confirmation move refused")
            if fault == "foreign_exact_copy":
                destination.mkdir(mode=0o700)
                (destination / "commit.json").write_bytes((source / "commit.json").read_bytes())
        return original_move(source, destination)
    def write(path, payload):
        result = original_write(path, payload)
        if fault == "prepare_after_write" and path.name == "commit.json":
            raise PermissionError("confirmation preparation writer failed")
        return result
    monkeypatch.setattr(operation, "_rename_exclusive", move)
    monkeypatch.setattr(schedule_state, "write_protocol", write)
    with pytest.raises(OSError):
        due(settings)
    attempt = next((settings.feedback_analysis_dir / "attempts").iterdir())
    # Even loss of negative evidence cannot turn a prepared/foreign marker into success.
    (attempt / "failure.json").unlink()
    monkeypatch.setattr(operation, "_launch", lambda *a: pytest.fail("held confirmation cannot launch"))
    with pytest.raises(schedule_state.UnconfirmedPublication):
        due(settings)
    assert not (settings.feedback_schedule_dir / "cursor.json").exists()


def test_finalization_binding_change_holds_exact_finalized_recovery(tmp_path, monkeypatch):
    settings = enabled(tmp_path)
    original = scheduler.commit_cursor
    monkeypatch.setattr(scheduler, "commit_cursor", lambda *a: (_ for _ in ()).throw(OSError("cursor")))
    with pytest.raises(OSError):
        due(settings)
    attempt = next((settings.feedback_analysis_dir / "attempts").iterdir())
    marker = attempt / "schedule-finalized/commit.json"
    binding = json.loads(marker.read_bytes())
    binding["attempt_id"] = "foreign"
    marker.chmod(0o600)
    marker.write_text(json.dumps(binding))
    monkeypatch.setattr(scheduler, "commit_cursor", original)
    monkeypatch.setattr(operation, "_launch", lambda *a: pytest.fail("changed marker cannot launch"))
    with pytest.raises(schedule_state.UnconfirmedPublication):
        due(settings)
    assert not (settings.feedback_schedule_dir / "cursor.json").exists()


@pytest.mark.parametrize("after", [False, True])
def test_fresh_reuse_sidecar_failure_cannot_borrow_publisher_confirmation(tmp_path, monkeypatch, after):
    settings = enabled(tmp_path)
    due(settings)
    (settings.feedback_schedule_dir / "cursor.json").unlink()
    original = schedule_state.write_protocol
    def fail(path, payload):
        if path.name == "schedule-receipt.json":
            if after:
                original(path, payload)
            raise PermissionError("current reused sidecar failed")
        return original(path, payload)
    monkeypatch.setattr(schedule_state, "write_protocol", fail)
    with pytest.raises(PermissionError):
        operation.run_scheduled_feedback_analysis(settings, cutoff=MONDAY, provenance={},
                                                  schedule_intent=scheduler.weekly_intent(MONDAY))
    monkeypatch.setattr(operation, "_launch", lambda *a: pytest.fail("current reuse is not finalized"))
    with pytest.raises(schedule_state.UnconfirmedPublication):
        due(settings)
    assert len(list((settings.feedback_analysis_dir / "runs").iterdir())) == 1
    assert not (settings.feedback_schedule_dir / "cursor.json").exists()
