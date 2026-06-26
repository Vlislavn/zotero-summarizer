"""The shared integration rate limiter spaces calls by at least its interval."""
from __future__ import annotations

import time

from zotero_summarizer.integrations._rate_limiter import RateLimiter


def test_acquire_spaces_calls_by_interval() -> None:
    rate = 20  # 50 ms interval
    limiter = RateLimiter(rate)
    start = time.monotonic()
    limiter.acquire()  # first call returns immediately
    limiter.acquire()  # second must wait ~one interval
    elapsed = time.monotonic() - start
    assert elapsed >= 1.0 / rate
