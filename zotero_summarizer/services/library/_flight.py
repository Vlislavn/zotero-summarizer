"""Shared single-flight latch + daemon-thread helper for the library background caches.

``border_cache`` and ``reading_queue`` each run at most one background compute at a
time and expose ``is_running``/``last_error``/``try_start``/``finish`` over that slot.
The latch holds that state so the two modules don't reimplement it (each keeps its own
``FlightLatch`` instance — separate state, shared mechanism).
"""
from __future__ import annotations

import threading
from typing import Callable


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
