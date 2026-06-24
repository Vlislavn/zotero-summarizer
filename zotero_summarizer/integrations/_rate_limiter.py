"""Shared per-process rate limiter for the integration leaves.

Lifted from the byte-identical copies that lived in ``openalex.py`` and
``pubmed.py`` (the third caller never came; the duplication did). Stdlib-only,
so it stays at the bottom of the layer graph alongside the other integrations.
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    """Per-process token bucket: at most ``rate`` calls per second."""

    def __init__(self, rate: int) -> None:
        self._interval = 1.0 / rate
        self._lock = threading.Lock()
        self._last = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._last + self._interval - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._last = now
