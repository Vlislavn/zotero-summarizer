"""Live progress for one deep review.

A single ``ReviewReporter`` is threaded through the deep-review phases so the
background job can surface a live phase + sub-progress in its polled status and
emit INFO timing logs — without coupling the pure digest / quality / goal
functions to the job's module state. ``on_update`` receives the current progress
dict (the job writes it into its lock-guarded ``status()`` payload).

Every method is exception-free and cheap: progress reporting must never break or
slow a review. The pure functions take ``reporter=None`` and guard each call, so
they stay usable headlessly (e.g. the ``verify-deep-review`` CLI) and in tests.

Thread-safety: ``sub()`` is called from concurrent worker threads (parallel rubric
samples / goal calls). A ``threading.Lock`` guards the shared mutable state
(``_sub``, ``_calls``); ``phase()`` is serialised by its own lock because the
independent review branches enter phases concurrently.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

LOGGER = logging.getLogger("zotero_summarizer")

# phase key -> human label (UI + logs), in the order a review runs.
# Plain-language labels for the live progress readout — each says what the review
# is actually DOING at that moment, so the user understands the run, not jargon.
PHASE_LABELS: dict[str, str] = {
    "extract": "Reading the PDF",
    "digest": "Reading the full paper & writing the digest",
    "paper_type": "Detecting the paper type",
    "quality_rubric": "Scoring quality (rigor checks)",
    "quality_verify": "Checking the critical checks again",
    "quality_overstate": "Checking for overstated claims",
    "goals": "Matching to your research goals",
    "note": "Saving the note to Zotero",
}

# Median wall-clock per phase (seconds), seeded from real selective-thinking runs
# on the remote reasoning model (digest thinks; the rest run fast) so the FIRST review's ETA is
# already accurate. Self-corrects at runtime (EMA) as phases complete.
_PHASE_MEDIANS: dict[str, float] = {
    "extract": 5.0,
    "digest": 29.0,            # the one phase that reasons (the quality digest)
    "paper_type": 3.0,
    "quality_rubric": 11.0,    # 3 self-consistency checks, run in parallel
    "quality_verify": 12.0,
    "quality_overstate": 5.0,  # thinking-off → fast
    "goals": 20.0,             # per-goal summaries, parallel
    "note": 1.0,
}
_PHASE_ORDER = list(PHASE_LABELS)  # canonical order for ETA summation


def live_progress(job: dict[str, Any]) -> dict[str, Any]:
    """Advance clocks between phase updates without mutating the worker's snapshot."""
    progress = dict(job.get("progress") or {})
    if job.get("status") != "running" or job.get("progress_at") is None:
        return progress
    elapsed = max(0.0, time.perf_counter() - job["progress_at"])
    for key in ("phase_elapsed_seconds", "total_elapsed_seconds"):
        if key in progress:
            progress[key] = round(progress[key] + elapsed, 1)
    if progress.get("eta_seconds") is not None:
        remaining = progress["eta_seconds"] - elapsed
        progress["eta_seconds"] = round(remaining) if remaining > 0 else None
    return progress


def trim_finished(jobs: dict[str, dict[str, Any]], keep: int) -> None:
    """Drop the oldest FINISHED jobs beyond ``keep`` (running jobs are always kept).
    The caller holds the registry lock."""
    finished = [(k, j) for k, j in jobs.items() if j.get("status") != "running"]
    finished.sort(key=lambda kv: kv[1].get("started_at") or "")
    for key, _ in finished[: max(0, len(finished) - keep)]:
        jobs.pop(key, None)


def aggregate_status(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """The 'is any review running?' payload over the whole job registry."""
    running = [j for j in jobs if j.get("status") == "running"]
    latest = max((j for j in jobs if j.get("status") in ("ready", "error")),
                 key=lambda j: str(j.get("started_at") or ""), default={})
    error = latest.get("error") if not running and latest.get("status") == "error" else None
    completed = sum(1 for j in jobs if j.get("status") in ("ready", "error"))
    if running:
        state = "running"
    elif error:
        state = "error"
    elif completed > 0:
        state = "ready"
    else:
        state = "idle"
    return {
        "status": state,
        "total": len(jobs),
        "completed": completed,
        "error": error,
        "attempt": latest.get("attempt") if not running else None,
        "diagnostic": latest.get("diagnostic") if error else None,
        "started_at": min((j.get("started_at") for j in jobs if j.get("started_at")), default=None),
        "progress": live_progress(running[0]) if running else {},
    }


class ReviewReporter:
    """Threads through one deep review: tracks the current phase + sub-progress,
    pushes it to ``on_update`` for the polled status, and logs INFO timing per
    phase with a ``[dr item=KEY]`` prefix (mirrors the feeds ``[tick_id]`` idiom).

    ``sub()`` is safe to call from multiple concurrent threads (parallel rubric
    samples or goal calls). The digest, the quality layers and the goals run as
    overlapping chains: each chain times its own phases (``_open``), so a phase is
    never ended by an unrelated chain entering its phase, and the median only learns
    real durations."""

    def __init__(
        self, item_key: str, item_title: str, on_update: Callable[[dict[str, Any]], None]
    ) -> None:
        self._item_key = item_key
        self._item_title = item_title
        self._on_update = on_update
        self._phase = ""
        self._sub: dict[str, int] = {"done": 0, "total": 0}
        self._t0 = time.perf_counter()
        self._phase_t0 = self._t0
        self._open: dict[str, tuple[str, float]] = {}  # chain -> (phase, start)
        self._calls = 0
        self._lock = threading.Lock()
        self._phase_lock = threading.Lock()  # concurrent branches enter phases from different threads

    def _emit(self) -> None:
        now = time.perf_counter()
        phase_elapsed = round(now - self._phase_t0, 1)
        total_elapsed = round(now - self._t0, 1)
        eta = self._estimate_eta(total_elapsed)
        with self._lock:
            sub = dict(self._sub)
        self._on_update(
            {
                "phase": self._phase,
                "phase_label": PHASE_LABELS.get(self._phase, self._phase),
                "item_key": self._item_key,
                "item_title": self._item_title,
                "sub": sub,
                "phase_elapsed_seconds": phase_elapsed,
                "total_elapsed_seconds": total_elapsed,
                "eta_seconds": eta,
            }
        )

    def _estimate_eta(self, total_elapsed: float) -> float | None:
        """Remaining seconds estimated from median per-phase durations.

        Sums the medians of phases not yet completed; uses the fraction of
        sub-steps done to credit the current phase partially."""
        if not self._phase or self._phase not in _PHASE_ORDER:
            return None
        current_idx = _PHASE_ORDER.index(self._phase)
        # credit for current phase based on sub-progress
        with self._lock:
            sub_done = self._sub["done"]
            sub_total = self._sub["total"]
        current_median = _PHASE_MEDIANS.get(self._phase, 30.0)
        if sub_total > 0:
            fraction_done = sub_done / sub_total
            current_remaining = current_median * (1.0 - fraction_done)
        else:
            phase_elapsed = total_elapsed - sum(
                _PHASE_MEDIANS.get(p, 30.0) for p in _PHASE_ORDER[:current_idx]
            )
            current_remaining = max(0.0, current_median - phase_elapsed)
        future = sum(_PHASE_MEDIANS.get(p, 30.0) for p in _PHASE_ORDER[current_idx + 1:])
        return round(current_remaining + future, 0)

    def phase(self, name: str, *, total: int = 0, is_call: bool = False) -> None:
        """Enter a new phase .

        ``total`` sets the sub-step count (rubric runs / goals); ``is_call``
        marks a phase that is itself a single LLM call. With concurrent chains the
        reported phase is the most recently entered one."""
        with self._phase_lock:
            self._enter_phase(name, total, is_call)

    @staticmethod
    def _chain(name: str) -> str:
        if name in ("digest", "goals"):
            return name
        return "quality" if name == "paper_type" or name.startswith("quality_") else "serial"

    def _close(self, chain: str) -> None:
        """End the chain's open phase: log it and fold its OWN duration into the median."""
        opened = self._open.pop(chain, None)
        if opened is None:
            return
        name, started = opened
        elapsed = time.perf_counter() - started
        LOGGER.info("[dr item=%s] %s done in %.1fs", self._item_key, name, elapsed)
        # EWMA (0.7 prior / 0.3 latest) so one slow paper doesn't skew the ETA
        _PHASE_MEDIANS[name] = round(0.7 * _PHASE_MEDIANS.get(name, elapsed) + 0.3 * elapsed, 1)

    def finish(self, name: str) -> None:
        """End the open phase of ``name``'s chain now, when its end is not the next
        phase's start (the digest returning, goals done, the quality chain about to wait
        for the digest) — otherwise the wait would be timed as phase work."""
        with self._phase_lock:
            self._close(self._chain(name))

    def _enter_phase(self, name: str, total: int, is_call: bool) -> None:
        chain = self._chain(name)
        if name == "note":
            for open_chain in list(self._open):
                self._close(open_chain)
        else:
            if name == "digest":
                self._close("serial")  # extraction ended where the digest began
            self._close(chain)
        self._phase = name
        with self._lock:
            self._sub = {"done": 0, "total": int(total)}
        self._phase_t0 = time.perf_counter()
        self._open[chain] = (name, self._phase_t0)
        if is_call:
            with self._lock:
                self._calls += 1
        LOGGER.info(
            "[dr item=%s] %s%s", self._item_key, name, f" (0/{total})" if total else ""
        )
        self._emit()

    def sub(self, done: int, total: int) -> None:
        """Advance sub-progress (safe to call from concurrent worker threads)."""
        with self._lock:
            self._sub = {"done": int(done), "total": int(total)}
            self._calls += 1
        LOGGER.info("[dr item=%s] %s %d/%d", self._item_key, self._phase, done, total)
        self._emit()

    def summary(self) -> None:
        """Final one-line summary with the total wall-clock + the REAL LLM-call count."""
        with self._phase_lock:
            for chain in list(self._open):
                self._close(chain)
        from zotero_summarizer.services.library._review_attempt import current_timings

        timings = current_timings() or {}
        with self._lock:
            calls = self._calls
        LOGGER.info(
            "[dr item=%s] review done in %.1fs (%d LLM calls, %.0f by phase reporter%s)",
            self._item_key, time.perf_counter() - self._t0, int(timings.get("calls") or calls),
            calls, "" if timings else " - measured by the progress reporter only",
        )
