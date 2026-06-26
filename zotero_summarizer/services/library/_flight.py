"""Shared single-flight latch + daemon-thread + prewarm-knob helpers for the
library background caches.

``border_cache`` and ``reading_queue`` each run at most one background compute at a
time and expose ``is_running``/``last_error``/``try_start``/``finish`` over that slot.
The latch holds that state so the two modules don't reimplement it (each keeps its own
``FlightLatch`` instance — separate state, shared mechanism). ``run_in_background``
and ``resolve_prewarm_k`` are the launch primitives the two prewarm modules share
(deep-review prewarm + review-fleet prewarm), each passing its own env-var name.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Callable


class FlightLatch:
    """Thread-safe single-slot "is a background compute running?" latch."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._last_error: str | None = None

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    def try_start(self) -> bool:
        """Claim the single compute slot. Returns False if one already runs."""
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._last_error = None
            return True

    def finish(self, error: str | None = None) -> None:
        with self._lock:
            self._running = False
            self._last_error = error


def run_in_background(target: Callable[[], None]) -> None:
    """Start ``target`` (a zero-arg callable) on a daemon thread."""
    threading.Thread(target=target, daemon=True).start()


def resolve_prewarm_k(config: Any, *, env_var: str) -> int:
    """Top-N to prewarm: ``quality_review.prewarm_on_startup_k``, SUPERSEDED by the
    ``env_var`` environment variable when set. ``0`` disables.

    The env value is validated at this I/O boundary and rejected LOUDLY (raises
    ``ValueError`` naming the var) for a malformed or negative value — a typo
    should surface, not silently run with the wrong setting."""
    raw = os.getenv(env_var)
    if raw is not None and raw.strip():
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{env_var}={raw!r} must be an integer") from exc
        if value < 0:
            raise ValueError(f"{env_var}={raw!r} must be >= 0")
        return value
    return int(getattr(config.quality_review, "prewarm_on_startup_k", 0))
