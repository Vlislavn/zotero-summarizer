"""Regression probes for signal ownership and externally checked worker peaks."""
import os
import signal
from pathlib import Path

import pytest

from test_feedback_analysis_history import project
from test_feedback_analysis_operation import delayed_child, dispatch, failure
from zotero_summarizer.services.golden.feedback_analysis import BudgetExceeded, operation


def test_sigterm_during_launch_reaps_the_created_child(tmp_path, monkeypatch):
    settings = project(tmp_path)
    delayed_child(monkeypatch)
    actual = operation.subprocess.Popen
    processes = []

    def interrupted(*args, **kwargs):
        process = actual(*args, **kwargs)
        processes.append(process)
        os.kill(os.getpid(), signal.SIGTERM)
        return process

    monkeypatch.setattr(operation.subprocess, "Popen", interrupted)
    try:
        with pytest.raises(InterruptedError):
            dispatch(settings)
        receipt = failure(settings)
        assert receipt["child"]["pid"] == processes[0].pid
        assert receipt["child"]["reaped"] and receipt["child"]["group_absent"]
        assert processes[0].poll() is not None
    finally:
        for process in processes:
            operation._reap(process, operation.resolve_budgets(None)["operation"])


def test_sigterm_during_success_receipt_records_publication(tmp_path, monkeypatch):
    settings = project(tmp_path)
    actual = operation.write_protocol
    original = signal.getsignal(signal.SIGTERM)

    def interrupted(path, payload):
        if path.name == "receipt.json":
            handler = signal.getsignal(signal.SIGTERM)
            # Calling the default action would kill pytest; prove ownership first.
            assert callable(handler), "SIGTERM ownership ended before receipt persistence"
            os.kill(os.getpid(), signal.SIGTERM)
        return actual(path, payload)

    monkeypatch.setattr(operation, "write_protocol", interrupted)
    with pytest.raises(InterruptedError):
        dispatch(settings)
    receipt = failure(settings)
    assert receipt["published"] is True
    assert receipt["publication_state"] == "published_but_not_confirmed"
    assert Path(receipt["run_path"], "result.json").is_file()
    assert signal.getsignal(signal.SIGTERM) is original


@pytest.mark.parametrize("peak", [operation.DEFAULT_RSS_BYTES + 1, 0, None, True])
def test_completed_child_peak_is_validated_before_publication(tmp_path, monkeypatch, peak):
    settings = project(tmp_path)
    actual = operation.read_protocol

    def peak_status(path, guard):
        value = actual(path, guard)
        if path.name == "child-status.json":
            value["peak_rss_bytes"] = peak
        return value

    monkeypatch.setattr(operation, "read_protocol", peak_status)
    expected = BudgetExceeded if type(peak) is int and peak > 0 else ValueError
    with pytest.raises(expected):
        dispatch(settings)
    receipt = failure(settings)
    assert receipt["published"] is False
    assert receipt["child"]["reaped"] and receipt["child"]["group_absent"]
    assert not list(settings.feedback_analysis_dir.glob("runs/*"))
