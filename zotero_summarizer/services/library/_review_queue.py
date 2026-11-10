"""Two-lane admission for the shared deep-review pool: owner requests outrank background work.

``interactive`` = a review the owner asked for (Generate/Regenerate, Add); ``background`` =
Today preparation, the startup prewarms and the review fleet. The pool size (total
concurrency) is unchanged; this only decides WHO takes the next free slot:

* interactive jobs start before any queued background job (they jump the line);
* background jobs never take the last slot when the pool has more than one, so a new
  interactive review starts at once instead of waiting for a running one to finish.
  A local provider (pool of one) cannot reserve a slot: there the interactive job runs next.

A queued job is already ``running`` in the job registry; ``ahead`` tells the poller how
many queued jobs start before it (``None`` once it has a slot).
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Any

INTERACTIVE, BACKGROUND = "interactive", "background"
RESERVED_FOR_INTERACTIVE = 1


class ReviewQueue:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queued: dict[str, deque[tuple[str, Any]]] = {INTERACTIVE: deque(), BACKGROUND: deque()}
        self._active = {INTERACTIVE: 0, BACKGROUND: 0}

    def add(self, key: str, payload: Any, lane: str) -> None:
        with self._lock:
            self._queued[lane].append((key, payload))

    def promote(self, key: str) -> None:
        """The owner asked for a paper already queued as background work: move it up."""
        with self._lock:
            for entry in self._queued[BACKGROUND]:
                if entry[0] == key:
                    self._queued[BACKGROUND].remove(entry)
                    self._queued[INTERACTIVE].append(entry)
                    return

    def claim(self, capacity: int) -> tuple[str, Any] | None:
        """The next job allowed to start (its lane + payload), or ``None``; counts it active."""
        with self._lock:
            if sum(self._active.values()) >= capacity:
                return None
            if self._queued[INTERACTIVE]:
                lane = INTERACTIVE
            elif self._queued[BACKGROUND] and self._active[BACKGROUND] < capacity - (
                RESERVED_FOR_INTERACTIVE if capacity > 1 else 0
            ):
                lane = BACKGROUND
            else:
                return None
            self._active[lane] += 1
            return lane, self._queued[lane].popleft()[1]

    def release(self, lane: str) -> None:
        with self._lock:
            self._active[lane] -= 1

    def ahead(self, key: str) -> int | None:
        """Queued jobs that start before ``key``; ``None`` when it is not waiting."""
        with self._lock:
            order = [k for lane in (INTERACTIVE, BACKGROUND) for k, _ in self._queued[lane]]
        return order.index(key) if key in order else None
