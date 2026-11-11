"""Stall guard for deep-review LLM calls: a per-call deadline learned from the stage's own
recent seconds, one retry, fail-closed after the second stall.

Deadline = 3 × the stage's p90 (last 50 completed calls in this process, ≥5 samples),
clamped to 180–900 s. No history → no deadline (the transport timeout still applies).
A stalled call is abandoned on its worker thread: its per-review slot is released at once
and its late result is discarded. At most 2 abandoned threads live at a time; beyond that
calls run unguarded rather than piling up orphans. A call that returns or raises by itself
is never retried here."""
from __future__ import annotations

from collections import deque
from contextvars import copy_context
import logging
import threading
from time import perf_counter
from typing import Any, Callable

LOGGER = logging.getLogger("zotero_summarizer.llm")

FACTOR, FLOOR_SECONDS, CEILING_SECONDS, MIN_SAMPLES, MAX_ORPHANS = 3.0, 180.0, 900.0, 5, 2

_LOCK = threading.Lock()
_SECONDS: dict[str, deque[float]] = {}
_ORPHANS = 0


class Stalled(TimeoutError):
    """The call outlived its deadline twice."""


def deadline(stage: str) -> float | None:
    with _LOCK:
        samples = sorted(_SECONDS.get(stage, ()))
    if len(samples) < MIN_SAMPLES:
        return None
    p90 = samples[min(len(samples) - 1, int(0.9 * len(samples)))]
    return min(CEILING_SECONDS, max(FLOOR_SECONDS, FACTOR * p90))


def _record(stage: str, seconds: float) -> None:
    with _LOCK:
        _SECONDS.setdefault(stage, deque(maxlen=50)).append(seconds)


def _run_with_deadline(call: Callable[[], Any], limit: float | None) -> tuple[bool, Any]:
    """``(finished, value)``; re-raises the call's own exception. ``finished=False`` = abandoned."""
    global _ORPHANS
    with _LOCK:
        if _ORPHANS >= MAX_ORPHANS:
            limit = None
    if limit is None:
        return True, call()
    box: dict[str, Any] = {}
    done, abandoned = threading.Event(), [False]
    context = copy_context()

    def worker() -> None:
        global _ORPHANS
        try:
            box['value'] = context.run(call)
        except BaseException as exc:  # handed back to the caller thread
            box['error'] = exc
        finally:
            with _LOCK:
                if abandoned[0]:
                    _ORPHANS -= 1
            done.set()

    threading.Thread(target=worker, name="llm-call", daemon=True).start()
    if not done.wait(limit):
        with _LOCK:
            if not done.is_set():
                abandoned[0] = True
                _ORPHANS += 1
                return False, None
    if 'error' in box:
        raise box['error']
    return True, box['value']


def run(call: Callable[[], Any], stage: str, slots: Any = None) -> tuple[Any, float]:
    """``(value, seconds)``; the slot is held only while an attempt runs (queueing is not call time)."""
    for attempt in (1, 2):
        if slots is not None:
            slots.acquire()
        started, limit = perf_counter(), deadline(stage)
        try:
            finished, value = _run_with_deadline(call, limit)
        finally:
            if slots is not None:
                slots.release()
        seconds = perf_counter() - started
        if finished:
            _record(stage, seconds)
            return value, seconds
        LOGGER.warning("LLM call stalled stage=%s seconds=%.1f deadline=%.0f attempt=%d/2%s",
                       stage, seconds, limit, attempt, " — retrying" if attempt == 1 else " — giving up")
    raise Stalled(f"{stage} call exceeded {limit:.0f}s twice")
