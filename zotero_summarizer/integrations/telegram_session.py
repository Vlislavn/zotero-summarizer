"""Dedicated macOS CLI lifetime only; native fuse/lease survive return until exit."""
from __future__ import annotations

import asyncio
import math
import os
from pathlib import Path
import signal
import stat
import sys
import threading
import time
from typing import NoReturn

from zotero_summarizer.models.research_feed import TelegramTransportError


DEFAULT_WALL_SECONDS = 90.0  # Unmeasured operational safety heuristic, not a throughput budget.
MAX_WALL_SECONDS = 300.0
DEFAULT_CLEANUP_RESERVE_SECONDS = 10.0
_local_owner = threading.Lock()
_lease_fd = None
_retained = []
_last_interrupt = None


def _budgets() -> tuple[float, float]:
    try:
        wall = float(os.environ.get("ZS_TELEGRAM_COLLECTION_WALL_SECONDS", DEFAULT_WALL_SECONDS))
        reserve = float(os.environ.get("ZS_TELEGRAM_CLEANUP_RESERVE_SECONDS", DEFAULT_CLEANUP_RESERVE_SECONDS))
    except (ValueError, OverflowError):
        raise ValueError("invalid Telegram safety budgets") from None
    if not (math.isfinite(wall) and math.isfinite(reserve)
            and 0 < reserve < wall <= MAX_WALL_SECONDS):
        raise ValueError("invalid Telegram safety budgets")
    return wall, reserve


def _check_platform() -> None:
    if (sys.platform != "darwin" or sys.implementation.name != "cpython"
            or threading.current_thread() is not threading.main_thread()
            or not hasattr(asyncio, "Runner") or not hasattr(asyncio, "timeout")):
        raise RuntimeError("Telegram collection requires a dedicated macOS CPython main-thread CLI")
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        raise RuntimeError("Telegram collection cannot run inside an event loop")
    if (signal.getsignal(signal.SIGALRM) != signal.SIG_DFL
            or signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0)
            or signal.SIGALRM in signal.pthread_sigmask(signal.SIG_BLOCK, set())
            or signal.SIGALRM in signal.sigpending()):
        raise RuntimeError("Telegram collection conflicts with existing signal state")


def _arm_fuse(deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("Telegram safety budget expired before admission")
    # SIG_DFL is native termination, NOT a Python callback. Never restore/disarm.
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.setitimer(signal.ITIMER_REAL, remaining, 0.0)


def _no_symlinks(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Telegram account path must be absolute")
    for component in (*reversed(path.parents), path):
        if component.is_symlink():
            raise ValueError("unsafe Telegram account path")


def _private_file(fd: int, path: Path) -> None:
    opened, current = os.fstat(fd), path.lstat()
    if (not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(current.st_mode)
            or opened.st_uid != os.geteuid() or current.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != 0o600 or stat.S_IMODE(current.st_mode) != 0o600
            or opened.st_nlink != 1 or current.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)):
        raise ValueError("unsafe Telegram session or lease metadata")


def _claim_file(data_dir: Path) -> None:
    import fcntl

    global _lease_fd
    directory = data_dir / "telegram-account"
    _no_symlinks(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError("unsafe Telegram account directory")
    path = directory / ".owner.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        _private_file(fd, path)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        # No opaque construction has occurred; failed admission owns no SDK work.
        os.close(fd)
        raise
    _lease_fd = fd  # Raw process-lifetime lease: no finally/atexit close or unlock.


def _prepare_auth_session(path: Path) -> None:
    _no_symlinks(path)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    except FileExistsError:
        return  # Existing paths must pass the unchanged metadata admission next.
    try:
        _private_file(fd, path)
    finally:
        os.close(fd)


def _session_metadata(path: Path) -> int:
    _no_symlinks(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise TelegramTransportError("missing_session") from None
    try:
        _private_file(fd, path)
    except BaseException:
        os.close(fd)
        raise
    return fd  # Metadata only, never read/hashed; retained through process death.


def _hold_for_cli_death(*resources) -> NoReturn:
    global _last_interrupt
    _retained.extend(resources)
    while True:
        try:
            signal.pause()
        except KeyboardInterrupt as error:
            _last_interrupt = error  # Do not unwind/renew the fuse on repeated SIGINT.


def _close_uncertain_session(state) -> NoReturn:
    if state["session"] is not None:
        try:
            state["session"].close()
        except BaseException as error:
            state["close_error"] = error
    _hold_for_cli_death(state)


def _within_work(state, stop: float) -> bool:
    if time.monotonic() >= stop:
        state["status"] = "work_timeout"
        return False
    return True


def _admitted(admit) -> bool:
    decision = admit()
    if type(decision) is not bool:
        raise ValueError("Telegram admission callback must return a boolean")
    return decision


def _construct(state, session_factory, client_factory, stop: float) -> None:
    if not _within_work(state, stop):
        return
    try:
        state["session"] = session_factory(state["path"])
        if state["session"] is None:
            raise TypeError("Telegram session factory returned no session")
        _no_symlinks(state["path"])
        _private_file(state["metadata_fd"], state["path"])
        if not _within_work(state, stop):
            return
        state["client"] = client_factory(state["session"])
        if state["client"] is None or state["client"].session is not state["session"]:
            raise TypeError("Telegram client factory did not retain its owned session")
        _no_symlinks(state["path"])
        _private_file(state["metadata_fd"], state["path"])
    except BaseException as error:
        # Even a successful known-session close cannot prove partial-client cleanup.
        state["error"] = error
        _close_uncertain_session(state)


async def _disconnect(state, deadline: float, reserve: float) -> None:
    end = min(deadline, time.monotonic() + reserve)
    try:
        state["waiter"] = asyncio.ensure_future(state["client"].disconnect())
    except BaseException as error:
        state["cleanup_error"] = error
        _close_uncertain_session(state)
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            _hold_for_cli_death(state)
        try:
            await asyncio.wait_for(asyncio.shield(state["waiter"]), remaining)
            state["cleanup"] = "completed"
            return
        except asyncio.CancelledError as error:
            if state["waiter"].cancelled():
                state["cleanup_error"] = error
                _close_uncertain_session(state)
            state["status"] = "interrupted"
            state["interruption"] = error
        except BaseException as error:
            state["cleanup_error"] = error
            _close_uncertain_session(state)


async def _work(state, collect, admit, factories, stop: float) -> None:
    if not _within_work(state, stop):
        return
    state["phase"] = "admission"
    if not _admitted(admit):
        state["status"] = "deferred"
        return
    state["phase"] = "metadata"
    if state["purpose"] == "auth":
        _prepare_auth_session(state["path"])
    state["metadata_fd"] = _session_metadata(state["path"])
    _construct(state, *factories, stop)
    if state["client"] is None or not _within_work(state, stop):
        return
    state["phase"] = "admission"
    if not _admitted(admit):
        state["status"] = "deferred"
        return
    if not _within_work(state, stop):
        return
    state["phase"] = "connect"
    await state["client"].connect()
    if not _within_work(state, stop):
        return
    state["phase"] = "collection"
    state["collection"] = await collect(state["client"])
    if type(state["collection"]) is not dict:
        raise TypeError("Telegram collection callback must return a receipt")
    state["status"] = "collected"


async def _execute(state, collect, admit, factories, deadline: float, reserve: float) -> None:
    stop = deadline - reserve
    timeout = asyncio.timeout(max(0.0, stop - time.monotonic()))
    try:
        try:
            async with timeout:
                try:
                    await _work(state, collect, admit, factories, stop)
                except Exception as error:
                    state["error"] = error
        except (asyncio.CancelledError, KeyboardInterrupt) as error:
            state["status"] = "interrupted"
            state["interruption"] = error
        except TimeoutError:
            state["status"] = "work_timeout"
        error = state["error"]
        if error is not None and (state["phase"] == "connect"
                or (state.get("purpose") == "auth" and state["phase"] == "collection")
                or (state["phase"] == "metadata" and isinstance(error, TelegramTransportError))):
            state["failure"] = _transport_error(error)
            if state["failure"] is not None:
                state["on_error"](state["failure"])
    finally:
        if state["client"] is not None:
            await _disconnect(state, deadline, reserve)
        elif state["session"] is not None:
            try:
                state["session"].close()
            except BaseException as error:
                state["close_error"] = error
                _hold_for_cli_death(state)
            state["cleanup"] = "completed"


def _transport_error(error):
    if isinstance(error, TelegramTransportError):
        return TelegramTransportError(error.code, retry_seconds=error.retry_seconds)
    if isinstance(error, (ConnectionError, TimeoutError)):
        return TelegramTransportError("network_error")
    try:
        from telethon.errors import rpcbaseerrors, rpcerrorlist
    except ModuleNotFoundError as absent:
        if absent.name != "telethon":
            raise
        return None
    if isinstance(error, rpcerrorlist.SessionRevokedError):
        return TelegramTransportError("revoked_session")
    if isinstance(error, (rpcbaseerrors.UnauthorizedError, rpcerrorlist.AuthKeyDuplicatedError)):
        return TelegramTransportError("session_unauthorized")
    if isinstance(error, rpcerrorlist.FloodWaitError):
        return TelegramTransportError("flood_wait", retry_seconds=error.seconds)
    if isinstance(error, (rpcerrorlist.UsernameInvalidError, rpcerrorlist.UsernameNotOccupiedError,
                          rpcerrorlist.ChannelPrivateError, rpcerrorlist.ChannelPublicGroupNaError)):
        return TelegramTransportError("channel_unavailable")
    return None


def collect_once_cli(settings, collect, *, session_factory=None, client_factory=None,
                     admit, on_error, purpose="collect") -> dict:
    """Use only in a consented, provisioned dedicated process that exits after return.

    Two opaque factories: session_factory(path), then client_factory(known_session).
    on_error receives mapped connect/typed metadata/auth-work failures BEFORE teardown.
    Auth alone creates a missing session under the lease; no credential/default factory.
    The native fuse and raw file lease
    intentionally survive this call, including on failure and normal completion.
    """
    if purpose not in {"collect", "auth"}:
        raise ValueError("invalid Telegram process purpose")
    if purpose == "auth" and not (sys.stdin.isatty() and sys.stderr.isatty()):
        raise ValueError("Telegram auth requires an interactive terminal")
    wall, reserve = _budgets()
    if session_factory is None or client_factory is None:
        return {"status": "app_identity_unconfigured", "cleanup": "not_started", "collection": None}
    if not all(callable(value) for value in (collect, session_factory, client_factory, admit, on_error)):
        raise ValueError("Telegram runtime requires explicit callable construction/composition")
    if _local_owner.locked():
        raise RuntimeError("Telegram collection already owns this dedicated process")
    _check_platform()
    if not _local_owner.acquire(blocking=False):
        raise RuntimeError("Telegram collection already owns this dedicated process")
    deadline = time.monotonic() + wall
    _arm_fuse(deadline)
    try:
        _claim_file(settings.data_dir)
    except BlockingIOError:
        return {"status": "busy", "cleanup": "not_started", "collection": None}
    state = {"path": settings.data_dir / "telegram-account" / "account.session", "phase": "admission",
             "purpose": purpose,
             "session": None, "client": None, "metadata_fd": None, "waiter": None,
             "collection": None, "status": "not_attempted", "cleanup": "not_started", "error": None,
             "failure": None, "on_error": on_error}
    _retained.append(state)
    try:
        with asyncio.Runner() as runner:
            runner.run(_execute(state, collect, admit, (session_factory, client_factory), deadline, reserve))
    except BaseException as error:
        _hold_for_cli_death(state, error)
    result = {name: state[name] for name in ("status", "cleanup", "collection")}
    if state["error"] is not None:
        failure = state["failure"]
        if failure is None:
            if state["phase"] == "metadata":
                raise ValueError("unsafe Telegram session metadata") from None
            raise RuntimeError("Telegram lifecycle or collection failed") from None
        result["status"] = failure.code
    return result


__all__ = ["collect_once_cli"]
