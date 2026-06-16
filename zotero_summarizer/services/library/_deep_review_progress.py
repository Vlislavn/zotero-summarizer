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
(``_sub``, ``_calls``); ``phase()`` and ``summary()`` are called from the
orchestrator thread only.
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
    "quality_rubric": "Scoring quality (rigor checks)",
    "quality_overstate": "Checking for overstated claims",
    "goals": "Matching to your research goals",
    "note": "Saving the note to Zotero",
}

# Median wall-clock per phase (seconds), seeded from real selective-thinking runs
# on kather sota (digest thinks; the rest run fast) so the FIRST review's ETA is
# already accurate. Self-corrects at runtime (EMA) as phases complete.
_PHASE_MEDIANS: dict[str, float] = {
    "extract": 5.0,
    "digest": 29.0,            # the one phase that reasons (the quality digest)
    "quality_rubric": 11.0,    # 3 self-consistency checks, run in parallel
    "quality_overstate": 5.0,  # thinking-off → fast
    "goals": 20.0,             # per-goal summaries, parallel
    "note": 1.0,
}
_PHASE_ORDER = list(PHASE_LABELS)  # canonical order for ETA summation


class ReviewReporter:
    """Threads through one deep review: tracks the current phase + sub-progress,
    pushes it to ``on_update`` for the polled status, and logs INFO timing per
    phase with a ``[dr item=KEY]`` prefix (mirrors the feeds ``[tick_id]`` idiom).

    ``sub()`` is safe to call from multiple concurrent threads (parallel rubric
    samples or goal calls).  All other methods (``phase``, ``summary``) are
    called from the single orchestrator thread."""

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
        self._calls = 0
        self._lock = threading.Lock()

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
        """Enter a new phase (called from orchestrator thread only).

        ``total`` sets the sub-step count (rubric runs / goals); ``is_call``
        marks a phase that is itself a single LLM call."""
        if self._phase:
            phase_elapsed = time.perf_counter() - self._phase_t0
            LOGGER.info(
                "[dr item=%s] %s done in %.1fs",
                self._item_key, self._phase, phase_elapsed,
            )
            # update the running median for future ETA estimates
            _PHASE_MEDIANS[self._phase] = round(
                0.7 * _PHASE_MEDIANS.get(self._phase, phase_elapsed) + 0.3 * phase_elapsed, 1
            )
        self._phase = name
        with self._lock:
            self._sub = {"done": 0, "total": int(total)}
        self._phase_t0 = time.perf_counter()
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
        """Final one-line summary with the total wall-clock + LLM-call count."""
        if self._phase:
            LOGGER.info(
                "[dr item=%s] %s done in %.1fs",
                self._item_key, self._phase, time.perf_counter() - self._phase_t0,
            )
        with self._lock:
            calls = self._calls
        LOGGER.info(
            "[dr item=%s] review done in %.1fs (~%d LLM calls)",
            self._item_key, time.perf_counter() - self._t0, calls,
        )
