"""Wall-clock deadline for ONE headless browser session (leaf).

patchright's sync calls have waits with no timeout (a CDP ``send``, ``ctx.close()``, the
driver stop). A hang there would hold ``browser_fetch._BROWSER_LOCK`` and the calling
thread forever. A daemon timer, running only while the lock is held, kills THIS session's
driver process and its descendants (the detached Chromium and helpers) at the deadline;
the blocked call then raises, the ``finally`` blocks run and the lock is released.

Not covered: waits that happen before the driver exists or outside it (DNS, cookie read,
driver start) — the driver pid is unknown there, so nothing is killed.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

import psutil

LOGGER = logging.getLogger(__name__)

_MAX_DEADLINE_SECS = 900.0
_DEADLINE_FACTOR = 10  # x the per-navigation timeout (fetch_timeout_secs)
_GRACE_SECS = 2.0      # terminate -> wait -> kill, as in ollama_runtime / _process_guard


class BrowserDeadlineError(RuntimeError):
    """The session outran its deadline and its browser was killed. Not a ``TimeoutError``:
    that is an ``OSError`` and OSError-catching callers would misread it as a network error."""


def deadline_secs(timeout: float) -> float:
    return min(_MAX_DEADLINE_SECS, _DEADLINE_FACTOR * timeout)


def _driver_pid(pw: Any) -> int | None:
    """patchright exposes no public driver pid; walk its private chain, tolerating any
    missing link (fakes, a patchright upgrade)."""
    for name in ("_impl_obj", "_connection", "_transport", "_proc", "pid"):
        pw = getattr(pw, name, None)
    return pw if type(pw) is int else None


def _kill_tree(members: list[psutil.Process]) -> None:
    for process in members:
        try:
            process.terminate()
        except psutil.Error:
            continue
    _, alive = psutil.wait_procs(members, timeout=_GRACE_SECS)
    for process in alive:
        try:
            process.kill()
        except psutil.Error:
            continue
    psutil.wait_procs(alive, timeout=_GRACE_SECS)


class SessionDeadline:
    """Context manager: arm a daemon timer on entry, cancel it on exit. ``attach(pw)`` tells it
    which driver to kill; without a driver pid the timer does nothing. ``armed=False`` (the
    headed fallback) never starts a timer."""

    def __init__(self, timeout: float, label: str, *, armed: bool = True) -> None:
        self.fired = False
        self._seconds = deadline_secs(timeout)
        self._label = label
        self._driver: psutil.Process | None = None
        self._cancelled = False
        self._guard = threading.Lock()
        self._timer = threading.Timer(self._seconds, self._fire) if armed else None
        if self._timer is not None:
            self._timer.daemon = True

    def __enter__(self) -> SessionDeadline:
        if self._timer is not None:
            self._timer.start()
        return self

    def __exit__(self, exc_type: Any, exc: BaseException | None, _tb: Any) -> None:
        with self._guard:
            self._cancelled = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer.join(timeout=2 * _GRACE_SECS + 1)  # a kill in progress finishes first
        if self.fired and isinstance(exc, Exception):
            raise BrowserDeadlineError(
                f"browser fetch exceeded its {self._seconds:.0f}s deadline; its browser was killed "
                f"(paper {self._label})") from exc

    def attach(self, pw: Any) -> None:
        """Remember the driver of the just-started playwright ``pw``; warn if it cannot be found."""
        if self._timer is None:
            return
        pid = _driver_pid(pw)
        try:
            driver = psutil.Process(pid) if pid is not None else None
        except psutil.Error:
            driver = None
        if driver is None:
            LOGGER.warning("browser deadline watchdog disarmed for paper %s: driver pid unavailable", self._label)
            return
        with self._guard:
            self._driver = driver

    def close(self, closable: Any) -> None:
        """``closable.close()`` unless the driver was killed: a later sync call on a dead driver
        can spin (``_sync`` loops on a finished dispatcher), and there is nothing left to flush."""
        if not self.fired:
            closable.close()

    def _fire(self) -> None:
        with self._guard:
            driver = self._driver
            if self._cancelled or driver is None:
                return
            self.fired = True
        try:
            members = [*reversed(driver.children(recursive=True)), driver]  # descendants first
        except psutil.Error:
            return
        LOGGER.warning(
            "browser fetch deadline %.0fs reached for paper %s: killing THIS fetch's browser session only "
            "(driver pid %d + %d child processes); the app and other work are untouched",
            self._seconds, self._label, driver.pid, len(members) - 1)
        _kill_tree(members)
