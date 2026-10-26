"""Synthetic one-shot publication, actual isolated child and frozen replay."""
import csv
import hashlib
import json
import os
import signal
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from test_feedback_analysis_history import event, log, project
from test_feedback_analysis_inputs import CUTOFF
from zotero_summarizer.services.golden.feedback_analysis import analyze_inputs, verify_inputs, BudgetExceeded
from zotero_summarizer.services.golden.feedback_analysis import operation
from zotero_summarizer.services.golden.feedback_analysis.provenance import canonical_bytes


def dispatch(settings, **kwargs):
    from zotero_summarizer.services.golden.feedback_analysis.operation import run_feedback_analysis
    return run_feedback_analysis(settings, cutoff=CUTOFF, provenance={"seed": 42}, **kwargs)


def test_actual_child_publication_and_known_replay(tmp_path):
    settings = project(tmp_path)
    log(settings, [event("A", comment="local comment"), event("B", new_user_priority="dont_read")])
    settings.config_path.write_text('goals: []\nsecret: SYNTHETIC-SECRET\n')
    before = settings.interaction_log_path.read_bytes()

    receipt = dispatch(settings)

    assert receipt["status"] == "published" and receipt["published"] is True
    run = Path(receipt["run_path"])
    manifest = verify_inputs(run / "retained")
    result = json.loads((run / "result.json").read_bytes())
    assert receipt["attempt_id"] != receipt["run_id"]
    assert result == analyze_inputs(run / "retained", budgets=receipt["analysis_budgets"])
    assert manifest["content_digest"] == receipt["input_content_digest"]
    assert result["result_digest"] == receipt["result_digest"]
    assert receipt["child"]["pid"] != os.getpid() and receipt["child"]["reaped"] is True
    assert receipt["child"]["group_absent"] is True
    assert 0 < receipt["child"]["reported_peak_rss_bytes"] < operation.DEFAULT_RSS_BYTES
    assert receipt["child"]["import_root"] == str(Path(__file__).resolve().parents[1])
    assert receipt["config"]["goals_file"]["sha256"] == hashlib.sha256(settings.config_path.read_bytes()).hexdigest()
    assert len(receipt["code"]["commit"]) == 40
    assert settings.interaction_log_path.read_bytes() == before
    assert "SYNTHETIC-SECRET" not in (run / "result.json").read_text()
    for name, record in receipt["outputs"].items():
        raw = (run / name).read_bytes()
        assert record == {"sha256": hashlib.sha256(raw).hexdigest(), "byte_size": len(raw)}
    assert all(p.stat().st_mode & 0o222 == 0 for p in [run, *run.rglob("*")])
    assert not (settings.feedback_analysis_dir / "state.json").exists()


def test_insufficient_report_still_publishes_hashes_and_gaps(tmp_path):
    settings = project(tmp_path)

    receipt = dispatch(settings)

    assert receipt["status"] == "insufficient_data" and receipt["published"] is True
    assert receipt["gap_reasons"] and receipt["outputs"]["result.md"]["byte_size"] > 0
    result = json.loads((Path(receipt["run_path"]) / "result.json").read_bytes())
    assert result["ranking"]["precision_at_10"] is None
    assert result["chronological"]["scored_evaluation"] is None


def test_parent_csv_limit_cannot_contaminate_actual_child(tmp_path):
    settings = project(tmp_path)
    comment = "π" * 140000
    with settings.golden_csv_path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["item_key", "gold_notes"])
        writer.writerow(["A", comment])
        writer.writerow(["B", "tail"])
    previous = csv.field_size_limit()
    try:
        csv.field_size_limit(8)
        receipt = dispatch(settings)
        assert csv.field_size_limit() == 8
    finally:
        csv.field_size_limit(previous)
    result = json.loads((Path(receipt["run_path"]) / "result.json").read_bytes())
    assert [e["comment"] for e in result["events"] if e["channel"] == "golden_csv"] == [comment, "tail"]
    assert result["result_digest"] == hashlib.sha256(canonical_bytes(
        {k: v for k, v in result.items() if k != "result_digest"})).hexdigest()


def failure(settings):
    paths = list(settings.feedback_analysis_dir.glob("attempts/*/failure.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_bytes())


@pytest.mark.parametrize("problem", ["missing", "corrupt"])
def test_required_input_child_failure_truthful_and_reaped(tmp_path, problem):
    settings = project(tmp_path)
    settings.triage_db_path.unlink()
    if problem == "corrupt":
        settings.triage_db_path.write_bytes(b"synthetic corrupt database")
    expected = FileNotFoundError if problem == "missing" else sqlite3.DatabaseError

    with pytest.raises(expected) as caught:
        dispatch(settings)

    receipt = failure(settings)
    assert receipt["published"] is False and receipt["status"] == "failure" and receipt["receipt_persistence"] == {"status": "written", "error_class": None}
    assert receipt["phase"] == "freeze" and receipt == caught.value.feedback_receipt
    assert receipt["child"]["reaped"] and receipt["child"]["group_absent"]
    assert not list(settings.feedback_analysis_dir.glob("runs/*"))
    assert settings.triage_db_path.exists() is (problem == "corrupt")


def delayed_child(monkeypatch):
    actual = operation._child_command
    def delayed(path):
        command = actual(path)
        command[3] = command[3].replace("raise SystemExit", "from zotero_summarizer.services.golden.feedback_analysis.worker import _offline; "
                                       "_offline(); import time; time.sleep(30); raise SystemExit")
        return command
    monkeypatch.setattr(operation, "_child_command", delayed)


def test_external_deadline_terminates_actual_child(tmp_path, monkeypatch):
    settings = project(tmp_path)
    delayed_child(monkeypatch)

    with pytest.raises(TimeoutError):
        dispatch(settings, budgets={"operation": {"wall_cap_seconds": 0.2, "poll_seconds": 0.05}})

    receipt = failure(settings)
    assert receipt["error_class"] == "TimeoutError" and not receipt["published"]
    assert receipt["child"]["group_absent"] and receipt["child"]["reaped"]
    assert receipt["child"]["returncode"] != 0


@pytest.mark.parametrize("kind", ["keyboard", "sigterm", "sigint"])
def test_interruption_cleanup_and_signal_restoration(tmp_path, monkeypatch, kind):
    settings = project(tmp_path)
    delayed_child(monkeypatch)
    original = signal.getsignal(signal.SIGTERM)
    actual = operation.OperationGuard.check
    def cancel(self, process=None):
        actual(self, process)
        if process is not None:
            if kind == "keyboard":
                raise KeyboardInterrupt()
            os.kill(os.getpid(), signal.SIGTERM if kind == "sigterm" else signal.SIGINT)
    monkeypatch.setattr(operation.OperationGuard, "check", cancel)

    with pytest.raises(InterruptedError if kind == "sigterm" else KeyboardInterrupt):
        dispatch(settings)

    receipt = failure(settings)
    assert receipt["child"]["group_absent"] and receipt["child"]["reaped"]
    assert signal.getsignal(signal.SIGTERM) is original


def test_concurrent_owned_lock_refuses_second_dispatch(tmp_path, monkeypatch):
    settings = project(tmp_path)
    root = operation._private_directory(settings.feedback_analysis_dir)
    def forbidden(*args, **kwargs):
        pytest.fail("busy operation must not launch a child")
    monkeypatch.setattr(operation.subprocess, "Popen", forbidden)

    with operation._single_run(root), pytest.raises(BlockingIOError):
        dispatch(settings)

    assert failure(settings)["published"] is False


@pytest.mark.parametrize("change", ["tamper", "extra", "symlink", "child_version"])
def test_parent_refuses_tampered_output_protocol(tmp_path, monkeypatch, change):
    settings = project(tmp_path)
    actual = operation._launch
    def changed(attempt, request, guard, child):
        status = actual(attempt, request, guard, child)
        staging = attempt / "run-staging"
        if change == "tamper":
            (staging / "result.md").write_bytes(b"tampered")
        elif change == "extra":
            (staging / "extra").write_bytes(b"unexpected")
        elif change == "symlink":
            (staging / "result.md").unlink()
            (staging / "result.md").symlink_to(settings.interaction_log_path)
        else:
            path = staging / "output-manifest.json"
            payload = json.loads(path.read_bytes())
            payload["schema_version"] = 2
            path.write_bytes(canonical_bytes(payload))
            status["output_manifest_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        return status
    monkeypatch.setattr(operation, "_launch", changed)

    with pytest.raises(ValueError):
        dispatch(settings)

    assert failure(settings)["phase"] == "validation"
    assert not list(settings.feedback_analysis_dir.glob("runs/*"))


def test_post_rename_receipt_failure_records_publication_truth(tmp_path, monkeypatch):
    settings = project(tmp_path)
    actual = operation.write_protocol
    def broken(path, payload):
        if path.name == "receipt.json":
            raise OSError("synthetic receipt failure")
        return actual(path, payload)
    monkeypatch.setattr(operation, "write_protocol", broken)

    with pytest.raises(OSError):
        dispatch(settings)

    receipt = failure(settings)
    assert receipt["status"] == "failure" and receipt["published"] is True and receipt["receipt_persistence"] == {"status": "written", "error_class": None}
    assert Path(receipt["run_path"]).is_dir()
    assert (Path(receipt["run_path"]) / "output-manifest.json").is_file()


def test_existing_destination_never_reused(tmp_path, monkeypatch):
    settings = project(tmp_path)
    actual = operation._publish
    def occupied(staging, destination, guard):
        destination.mkdir()
        (destination / "marker").write_bytes(b"preexisting")
        return actual(staging, destination, guard)
    monkeypatch.setattr(operation, "_publish", occupied)

    with pytest.raises(FileExistsError):
        dispatch(settings)

    receipt = failure(settings)
    assert (Path(receipt["run_path"]) / "marker").read_bytes() == b"preexisting"
    assert list(settings.feedback_analysis_dir.glob("attempts/*/run-staging/result.json"))
    assert receipt["published"] is False
    assert receipt["publication_state"] == "not_published"
    assert receipt["child"]["reaped"] and receipt["child"]["group_absent"]


@pytest.mark.parametrize("commit", ["chmod_failure", "interrupted", "unobservable"])
def test_owned_commit_failure_observation(tmp_path, monkeypatch, commit):
    settings = project(tmp_path)
    original = operation._rename_exclusive
    interruption = InterruptedError("synthetic post-commit interruption")
    def interrupted(source, destination):
        original(source, destination)
        if commit == "unobservable":
            original(destination, destination.with_name(destination.name + "_retained"))
        raise interruption
    def chmod_failure(path, mode):
        if Path(path).parent.name == "runs":
            raise PermissionError("synthetic chmod failure")
        return original_chmod(path, mode)
    if commit == "chmod_failure":
        original_chmod = operation.os.chmod
        monkeypatch.setattr(operation.os, "chmod", chmod_failure)
    else:
        monkeypatch.setattr(operation, "_rename_exclusive", interrupted)

    with pytest.raises(PermissionError if commit == "chmod_failure" else InterruptedError) as caught:
        dispatch(settings)

    receipt = failure(settings)
    expected = None if commit == "unobservable" else True
    assert receipt["published"] is expected and receipt["status"] == "failure"
    assert receipt["publication_state"] == ("unknown" if expected is None else "published_but_not_confirmed")
    assert receipt["child"]["reaped"] and receipt["child"]["group_absent"]
    if commit != "chmod_failure":
        assert caught.value is interruption
    assert not (Path(receipt["attempt_path"]) / "run-staging").exists()
    assert list(settings.feedback_analysis_dir.glob("runs/*/result.json"))


@pytest.mark.parametrize("observation", ["contradictory", "inaccessible"])
def test_publication_observation_unknown_is_not_success(tmp_path, monkeypatch, observation):
    from types import SimpleNamespace
    source, target = tmp_path / "source", tmp_path / "target"
    def observed(path):
        if observation == "inaccessible" and path == target:
            raise PermissionError("synthetic observation denial")
        return SimpleNamespace(st_dev=1, st_ino=2)
    monkeypatch.setattr(operation.Path, "lstat", observed)

    published, error_class = operation._publication_observation(source, target, (1, 2))

    assert published is None
    assert error_class == ("PermissionError" if observation == "inaccessible" else None)


@pytest.mark.parametrize("nonempty", [False, True])
def test_native_rename_refuses_late_destination_without_overwrite(tmp_path, nonempty):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    (source / "new").write_bytes(b"new retained output")
    target.mkdir()  # appeared after any caller's pre-check
    if nonempty:
        (target / "old").write_bytes(b"old retained output")

    with pytest.raises(FileExistsError):
        operation._rename_exclusive(source, target)

    assert (source / "new").read_bytes() == b"new retained output"
    assert sorted(p.name for p in target.iterdir()) == (["old"] if nonempty else [])


def test_native_rename_moves_real_directory(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    (source / "new").write_bytes(b"complete")
    operation._rename_exclusive(source, target)
    assert not source.exists() and (target / "new").read_bytes() == b"complete"


def test_unsupported_publication_fails_without_rename_fallback(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    monkeypatch.setattr(operation.sys, "platform", "unsupported")
    with pytest.raises(NotImplementedError):
        operation._rename_exclusive(source, target)
    assert source.exists() and not target.exists()


def test_foreign_signal_handler_is_preserved(tmp_path):
    original = signal.getsignal(signal.SIGTERM)
    def foreign(signum, frame):
        raise AssertionError("not invoked")
    try:
        with pytest.raises(RuntimeError), operation._signals():
            signal.signal(signal.SIGTERM, foreign)
        assert signal.getsignal(signal.SIGTERM) is foreign
    finally:
        signal.signal(signal.SIGTERM, original)


@pytest.mark.parametrize("name,raw", [("WALL_CAP_SECONDS", "nan"), ("MAX_RSS_BYTES", "bad"), ("SEED", "bad")])
def test_malformed_env_is_not_ignored_by_override(tmp_path, monkeypatch, name, raw):
    settings = project(tmp_path)
    monkeypatch.setenv(operation.BUDGET_ENV_PREFIX + name, raw)
    with pytest.raises(ValueError):
        dispatch(settings, budgets={"operation": {"wall_cap_seconds": 10}})
    assert not settings.feedback_analysis_dir.exists()


def test_parent_bounded_validation_refuses_over_budget(tmp_path, monkeypatch):
    settings = project(tmp_path)
    actual = operation._launch
    def reduced(attempt, request, guard, child):
        status = actual(attempt, request, guard, child)
        guard.budgets["max_io_bytes"] = 1
        return status
    monkeypatch.setattr(operation, "_launch", reduced)
    with pytest.raises(BudgetExceeded):
        dispatch(settings)
    assert failure(settings)["phase"] == "validation"


@pytest.mark.parametrize("budget", [{"unknown": 1}, {"operation": {"max_rss_bytes": True}},
                                   {"operation": {"min_free_ram_percent": 19}}, {"analysis": {"max_records": 0}}])
def test_invalid_budget_before_any_output(tmp_path, budget):
    settings = project(tmp_path)
    with pytest.raises(ValueError):
        dispatch(settings, budgets=budget)
    assert not settings.feedback_analysis_dir.exists()


@pytest.mark.parametrize("provenance", [{"code": {"secret": "synthetic"}}, {"seed": True}, {"seed": -1}])
def test_secret_bearing_or_invalid_provenance_rejected(tmp_path, provenance):
    settings = project(tmp_path)
    with pytest.raises(ValueError):
        operation.run_feedback_analysis(settings, cutoff=CUTOFF, provenance=provenance)
    assert not settings.feedback_analysis_dir.exists()


@pytest.mark.parametrize("path_kind", ["symlink", "traversal", "source_overlap"])
def test_path_admission_never_writes_sources(tmp_path, path_kind):
    settings = project(tmp_path)
    before = settings.triage_db_path.read_bytes()
    if path_kind == "symlink":
        settings.feedback_analysis_dir.symlink_to(settings.data_dir, target_is_directory=True)
    elif path_kind == "traversal":
        settings = replace(settings, data_dir=settings.data_dir / ".." / "data")
    else:
        settings = replace(settings, config_path=settings.feedback_analysis_dir / ".operation.lock")
    with pytest.raises(ValueError):
        dispatch(settings)
    assert settings.triage_db_path.read_bytes() == before


def test_concurrent_parent_csv_setter_and_reader_are_isolated(tmp_path):
    import io
    settings = project(tmp_path)
    settings.golden_csv_path.write_text("item_key,gold_notes\nA," + "c" * 140000 + "\nB,tail\n")
    stopped, errors = threading.Event(), []
    previous = csv.field_size_limit()
    def unrelated():
        try:
            while not stopped.wait(0.002):
                csv.field_size_limit(16)
                assert list(csv.reader(io.StringIO("parent,reader\n"))) == [["parent", "reader"]]
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=unrelated)
    thread.start()
    try:
        receipt = dispatch(settings)
        assert csv.field_size_limit() == 16
    finally:
        stopped.set()
        thread.join(timeout=5)
        csv.field_size_limit(previous)
    assert not thread.is_alive() and errors == []
    result = json.loads((Path(receipt["run_path"]) / "result.json").read_bytes())
    assert result["coverage"]["sources"]["golden_csv"]["records"] == 2
    assert result["coverage"]["sources"]["golden_csv"]["malformed"] == 0


@pytest.mark.parametrize("problem", ["source_count", "config_bytes"])
def test_child_provenance_work_bounds_fail_before_capture(tmp_path, problem):
    settings = project(tmp_path)
    if problem == "config_bytes":
        settings.config_path.write_bytes(b"x" * (1024**2 + 1))
    budget = {"operation": {"max_source_files": 1}} if problem == "source_count" else None
    with pytest.raises((ValueError, BudgetExceeded)):
        dispatch(settings, budgets=budget)
    receipt = failure(settings)
    assert receipt["phase"] == "provenance" and receipt["published"] is False
    assert receipt["child"]["reaped"] and receipt["child"]["group_absent"]


def test_analysis_work_failure_retains_frozen_inputs(tmp_path):
    settings = project(tmp_path)
    log(settings, [event("A"), event("B")])
    with pytest.raises(BudgetExceeded):
        dispatch(settings, budgets={"analysis": {"max_records": 1}})
    receipt = failure(settings)
    assert receipt["phase"] == "analysis"
    verify_inputs(Path(receipt["attempt_path"]) / "run-staging/retained")


@pytest.mark.parametrize("safeguard", ["rss", "free", "swap"])
def test_external_resource_tripwires_are_not_absolute_swap(tmp_path, monkeypatch, safeguard):
    from types import SimpleNamespace
    guard = operation.OperationGuard(operation.resolve_budgets(None)["operation"])
    baseline = guard.swap_baseline
    monkeypatch.setattr(operation.psutil, "virtual_memory", lambda: SimpleNamespace(available=80, total=100))
    monkeypatch.setattr(operation.psutil, "swap_memory", lambda: SimpleNamespace(used=baseline))
    guard.check()  # arbitrarily high historical swap is allowed when growth is zero
    if safeguard == "free":
        monkeypatch.setattr(operation.psutil, "virtual_memory", lambda: SimpleNamespace(available=19, total=100))
    elif safeguard == "swap":
        monkeypatch.setattr(operation.psutil, "swap_memory", lambda: SimpleNamespace(used=baseline + 2 * 1024**3 + 1))
    else:
        process = SimpleNamespace(pid=123, poll=lambda: None)
        tree = SimpleNamespace(children=lambda recursive: [], memory_info=lambda: SimpleNamespace(rss=1024**3 + 1))
        monkeypatch.setattr(operation.psutil, "Process", lambda pid: tree)
    with pytest.raises(BudgetExceeded):
        guard.check(process if safeguard == "rss" else None)


@pytest.mark.parametrize("phase", ["before", "after"])
@pytest.mark.parametrize("boundary", ["api", "cli"])
@pytest.mark.parametrize("secondary_kind", ["io", "io_after_write", "programming"])
def test_failure_receipt_persistence_preserves_visible_failure(tmp_path, monkeypatch, capsys, phase, boundary, secondary_kind):
    from zotero_summarizer.cli import main
    settings, actual = project(tmp_path), operation.write_protocol
    if phase == "before":
        settings.triage_db_path.unlink()
    primary = RuntimeError("SYNTHETIC-PRIVATE-primary")
    secondary = ValueError("SYNTHETIC-PRIVATE-secondary") if secondary_kind == "programming" else PermissionError("SYNTHETIC-PRIVATE-secondary")
    def broken(path, payload):
        if path.name == "receipt.json":
            raise primary
        if path.name == "failure.json":
            if secondary_kind == "io_after_write":
                actual(path, payload)
            raise secondary
        return actual(path, payload)
    monkeypatch.setattr(operation, "write_protocol", broken)
    expected = ValueError if secondary_kind == "programming" else FileNotFoundError if phase == "before" else RuntimeError
    with pytest.raises(expected) as caught:
        if boundary == "api":
            dispatch(settings)
        else:
            main(["goldenset", "analyze-feedback", "--project-root", str(settings.project_root), "--cutoff", CUTOFF.isoformat()])
    original = secondary.__context__
    assert isinstance(original, FileNotFoundError if phase == "before" else RuntimeError) and (phase == "before" or original is primary)
    assert caught.value is (secondary if secondary_kind == "programming" else original)
    receipt = caught.value.feedback_receipt
    assert receipt["status"] == "failure" and receipt["published"] is (phase == "after")
    assert receipt["error_class"] == type(original).__name__ and original.feedback_receipt is receipt
    assert receipt["receipt_persistence"] == {"status": "failed", "error_class": type(secondary).__name__}
    assert Path(receipt["receipt_path"]).is_file() is (secondary_kind == "io_after_write")
    assert receipt["child"]["reaped"] and receipt["child"]["group_absent"]
    assert bool(list(settings.feedback_analysis_dir.glob("runs/*/output-manifest.json"))) is (phase == "after")
    assert "SYNTHETIC-PRIVATE" not in json.dumps(receipt)
    if boundary == "cli":
        summary = json.loads(capsys.readouterr().out)
        for key in ("status", "published", "phase", "error_class", "receipt_persistence", "receipt_path", "publication_state"):
            assert summary[key] == receipt[key]
