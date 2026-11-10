"""One background "Run now" analysis for the app UI; the child-process service does the work."""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from .operation import run_feedback_analysis

__all__ = ["start", "status"]

_LOCK = threading.Lock()
_JOB: dict = {"state": "idle"}
FAILURE_TEXT = {
    "BudgetExceeded": "The analysis stopped at a safety limit, most often low free memory. Close heavy apps and run it again.",
    "BlockingIOError": "Another analysis is already running.",
}


def _work(settings) -> None:
    try:
        receipt = run_feedback_analysis(settings, cutoff=datetime.now(timezone.utc), provenance={})
        done = {"state": receipt["status"], "run_id": receipt["run_id"]}
    except BaseException as exc:  # a failed run must be visible, never silent
        done = {"state": "failure", "message": FAILURE_TEXT.get(type(exc).__name__, f"The analysis failed ({type(exc).__name__}).")}
    with _LOCK:
        _JOB.clear()
        _JOB.update(done, finished_at=datetime.now(timezone.utc).isoformat())


def start(settings) -> bool:
    """Start a run unless one is running in this process; the service lock covers the daemon."""
    with _LOCK:
        if _JOB["state"] == "running":
            return False
        _JOB.clear()
        _JOB.update(state="running", started_at=datetime.now(timezone.utc).isoformat())
    threading.Thread(target=_work, args=(settings,), name="feedback-analysis", daemon=True).start()
    return True


def status() -> dict:
    with _LOCK:
        return dict(_JOB)
