"""Background job wrapper for "Calibrate to my setup" so the UI can poll live progress.

The calibration sweep is bounded but slow (a few real LLM digests). Before this it ran
inside one blocking request with no feedback; now ``start`` kicks it off on a daemon
thread and ``status`` exposes ``{status, completed, total, phase, result, error}`` — the
SAME run+poll shape ``deep_review`` uses. Single-flight: a second ``start`` while one is
running is a no-op that returns the current status.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

LOGGER = logging.getLogger(__name__)

_LOCK = threading.Lock()
_JOB: dict[str, Any] = {
    "status": "idle",      # idle | running | ready | error
    "completed": 0,
    "total": 0,
    "phase": "",
    "result": None,        # the run_full_calibration payload on success
    "error": None,         # a human-readable message on failure (e.g. "no built briefs")
}


def status() -> dict[str, Any]:
    with _LOCK:
        return dict(_JOB)


def _set(**fields: Any) -> None:
    with _LOCK:
        _JOB.update(fields)


def start(settings: Any, *, item_keys: list[str] | None = None, papers_limit: int = 3) -> dict[str, Any]:
    """Start calibration on a daemon thread (single-flight) and return the initial status.
    The frontend then polls :func:`status` until ``status`` leaves ``running``."""
    with _LOCK:
        if _JOB["status"] == "running":
            return dict(_JOB)
        _JOB.update({"status": "running", "completed": 0, "total": 0,
                     "phase": "starting", "result": None, "error": None})

    def _run() -> None:
        from zotero_summarizer.services.setup.calibration import run_full_calibration
        try:
            result = run_full_calibration(
                settings, item_keys=item_keys, papers_limit=papers_limit,
                progress=lambda p: _set(**p),
            )
            _set(status="ready", phase="done", result=result)
        except Exception as exc:  # noqa: BLE001 — background-job boundary; surfaced to the UI
            LOGGER.warning("calibration failed: %s", exc)
            _set(status="error", error=f"{type(exc).__name__}: {exc}")

    threading.Thread(target=_run, name="calibration", daemon=True).start()
    return status()


__all__ = ["start", "status"]
