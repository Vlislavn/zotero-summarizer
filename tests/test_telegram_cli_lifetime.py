"""Owned synthetic child discriminators for the real M3 fuse/lease, never an account."""
from __future__ import annotations

import asyncio
import math
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time
from contextlib import contextmanager

import pytest

from zotero_summarizer.models.research_feed import TelegramTransportError


WALL_SECONDS = float(os.environ.get("ZS_TELEGRAM_TEST_WALL_SECONDS", "4"))
RESERVE_SECONDS = float(os.environ.get("ZS_TELEGRAM_TEST_RESERVE_SECONDS", "0.5"))
OBSERVATION_FACTOR = 4
INTERRUPTS = 3
ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or sys.implementation.name != "cpython" or not hasattr(asyncio, "Runner"),
    reason="native M3 contract requires dedicated macOS CPython with asyncio.Runner",
)

CHILD = r'''
import asyncio, ctypes, json, os, signal, socket, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
from zotero_summarizer.integrations import telegram_session as owner
mode, directory = sys.argv[2], Path(sys.argv[3])
settings = SimpleNamespace(data_dir=directory)
account = directory / 'telegram-account'
account.mkdir(parents=True, mode=0o700, exist_ok=True)
(account / 'account.session').touch(mode=0o600)

def line(value):
    os.write(1, (value + '\n').encode())

def denied(*args, **kwargs):
    raise AssertionError('network forbidden')
socket.create_connection = denied
socket.socket.connect = denied

def blocked(label):
    line(label)
    libc = ctypes.PyDLL(None)
    read = libc.read
    read.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t]
    read.restype = ctypes.c_ssize_t
    buffer = ctypes.create_string_buffer(1)
    read(0, buffer, 1)
    raise AssertionError('blocking discriminator unexpectedly returned')

real_pause = signal.pause
def observed_pause():
    line('hold')
    return real_pause()
owner.signal.pause = observed_pause

class Session:
    def close(self):
        line('close')
        if mode == 'blocked_close':
            blocked('blocked_close')
        if mode == 'failed_close':
            raise ValueError('synthetic private session body')

def session_factory(path):
    assert signal.getsignal(signal.SIGALRM) == signal.SIG_DFL
    assert signal.getitimer(signal.ITIMER_REAL)[0] > 0
    line('session')
    if mode == 'blocked_constructor':
        blocked('blocked_constructor')
    if mode == 'session_raises':
        raise ValueError('synthetic private session body')
    return Session()

class Client:
    def __init__(self, session):
        self.session = session
    async def connect(self):
        line('connect')
        if mode in {'timeout_owner', 'timeout_unknown', 'timeout_provider'}:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if mode == 'timeout_unknown':
                    raise RuntimeError('synthetic replacement') from None
                if mode == 'timeout_provider':
                    raise TimeoutError('provider replacement') from None
                raise
    def disconnect(self):
        line('disconnect')
        async def finish():
            if mode == 'never_disconnect':
                await asyncio.get_running_loop().create_future()
            if mode == 'repeated_cancel':
                task = asyncio.current_task()
                # Cancel the owner, not this retained SDK cleanup task.
                for number in range(3):
                    self.collect_task.cancel()
                    await asyncio.sleep(0)
                assert not task.cancelled()
            self.session.close()
        return asyncio.shield(asyncio.create_task(finish()))

def client_factory(session):
    line('client')
    if mode == 'client_raises':
        raise RuntimeError('synthetic private client body')
    return Client(session)

async def collect(client):
    line('collect')
    client.collect_task = asyncio.current_task()
    if mode == 'cancel_work':
        raise asyncio.CancelledError()
    return {'committed': True}

if mode == 'shutdown_tail':
    real_close = asyncio.Runner.close
    def delayed_close(self):
        real_close(self)
        blocked('shutdown_tail')
    asyncio.Runner.close = delayed_close

def lifecycle_error(error):
    line('failure:' + error.code)

try:
    result = owner.collect_once_cli(settings, collect, session_factory=session_factory,
                                    client_factory=client_factory, admit=lambda: True,
                                    on_error=lifecycle_error)
except RuntimeError as error:
    if mode != 'timeout_unknown':
        raise
    result = {'raised': type(error).__name__}
line(json.dumps(result, sort_keys=True))
if mode in {'repeated_cancel', 'cancel_work', 'timeout_owner', 'timeout_unknown', 'timeout_provider'}:
    line('return_held')
    assert sys.stdin.buffer.read(1) == b'x'
if mode == 'normal_tail':
    try:
        owner.collect_once_cli(settings, collect, session_factory=session_factory,
                               client_factory=client_factory, admit=lambda: True,
                               on_error=lambda error: None)
    except RuntimeError:
        line('second_refused')
    blocked('normal_tail')
'''


@contextmanager
def owned_child(tmp_path, mode, *, script=CHILD, import_roots=()):
    # Explicit child argv only: -I deliberately ignores inherited PYTHONPATH.
    roots = tuple(Path(path) for path in import_roots)
    assert all(path.is_absolute() and path == path.resolve() and path.is_dir() for path in roots)
    assert 2 <= WALL_SECONDS <= 10
    assert 0 < RESERVE_SECONDS < WALL_SECONDS / 2
    directory = tmp_path / "owned"
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                       ZS_TELEGRAM_COLLECTION_WALL_SECONDS=str(WALL_SECONDS),
                       ZS_TELEGRAM_CLEANUP_RESERVE_SECONDS=str(RESERVE_SECONDS))
    process = subprocess.Popen([sys.executable, "-I", "-B", "-u", "-c", script,
                                str(ROOT), mode, str(directory), *map(str, roots)], cwd=tmp_path,
                               env=environment, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True, bufsize=0)
    try:
        yield process, directory
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=WALL_SECONDS * OBSERVATION_FACTOR)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def read_line(process):
    ready, _, _ = select.select([process.stdout], [], [], WALL_SECONDS * OBSERVATION_FACTOR)
    assert ready, "owned child phase receipt missing"
    return process.stdout.readline().decode().rstrip("\n")


def until(process, expected):
    seen = []
    for _ in range(16):
        value = read_line(process)
        seen.append(value)
        if value == expected:
            return seen
    raise AssertionError(f"phase not reached: {expected}; bounded events: {seen}")


def assert_busy(fd):
    import fcntl
    with pytest.raises(BlockingIOError):
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize("mode,phase", [
    ("blocked_constructor", "blocked_constructor"), ("session_raises", "hold"),
    ("client_raises", "hold"), ("blocked_close", "blocked_close"),
    ("failed_close", "hold"), ("never_disconnect", "hold"),
    ("shutdown_tail", "shutdown_tail"), ("normal_tail", "normal_tail"),
])
def test_actual_owner_fuse_and_lease_through_uncertain_and_normal_tail(tmp_path, mode, phase):
    import fcntl
    with owned_child(tmp_path, mode) as (process, directory):
        seen = until(process, phase)
        lock = directory / "telegram-account" / ".owner.lock"
        inode = lock.stat().st_ino
        fd = os.open(lock, os.O_RDWR)
        try:
            assert_busy(fd)
            if mode == "normal_tail":
                assert "second_refused" in seen
            process.wait(timeout=WALL_SECONDS * OBSERVATION_FACTOR)
            output, error = process.communicate(timeout=WALL_SECONDS * OBSERVATION_FACTOR)
            assert process.returncode == -signal.SIGALRM
            assert "synthetic private" not in error.decode() + output.decode()
            assert lock.stat().st_ino == inode
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)


def test_competing_process_cannot_construct_while_stable_lease_is_owned(tmp_path):
    import fcntl
    directory = tmp_path / "owned" / "telegram-account"
    directory.mkdir(parents=True, mode=0o700)
    lock = directory / ".owner.lock"
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        inode = lock.stat().st_ino
        with owned_child(tmp_path, "busy") as (process, _):
            output, error = process.communicate(timeout=WALL_SECONDS * OBSERVATION_FACTOR)
            import json
            assert process.returncode == 0 and error == b""
            assert json.loads(output) == {"status": "busy", "cleanup": "not_started", "collection": None}
            assert lock.stat().st_ino == inode
    finally:
        os.close(fd)


def test_three_acked_sigints_do_not_unwind_terminal_lease_hold(tmp_path):
    import fcntl
    with owned_child(tmp_path, "client_raises") as (process, directory):
        until(process, "hold")
        fd = os.open(directory / "telegram-account" / ".owner.lock", os.O_RDWR)
        try:
            for _ in range(INTERRUPTS):
                assert_busy(fd)
                process.send_signal(signal.SIGINT)
                assert read_line(process) == "hold"
            process.wait(timeout=WALL_SECONDS * OBSERVATION_FACTOR)
            assert process.returncode == -signal.SIGALRM
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)


@pytest.mark.parametrize("mode", ["repeated_cancel", "cancel_work"])
def test_cancellation_retains_single_sdk_future_and_completed_close(tmp_path, mode):
    import fcntl
    import json
    with owned_child(tmp_path, mode) as (process, directory):
        events = until(process, "return_held")
        result = json.loads(events[-2])
        fd = os.open(directory / "telegram-account" / ".owner.lock", os.O_RDWR)
        try:
            assert_busy(fd)
            process.stdin.write(b"x")
            process.stdin.flush()
            process.wait(timeout=WALL_SECONDS * OBSERVATION_FACTOR)
            output, error = process.communicate(timeout=WALL_SECONDS * OBSERVATION_FACTOR)
            assert process.returncode == 0 and output == error == b""
            assert events.count("disconnect") == events.count("close") == 1
            assert result["status"] == "interrupted" and result["cleanup"] == "completed"
            assert result["collection"] == ({"committed": True} if mode == "repeated_cancel" else None)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)


@pytest.mark.parametrize("mode,status", [("timeout_owner", "work_timeout"),
                                       ("timeout_unknown", None), ("timeout_provider", "network_error")])
def test_public_owner_preserves_timeout_cancellation_outcomes(tmp_path, mode, status):
    import fcntl
    import json
    with owned_child(tmp_path, mode) as (process, directory):
        events = until(process, "return_held")
        result = json.loads(events[-2])
        assert events.count("disconnect") == events.count("close") == 1
        if status is None:
            assert result == {"raised": "RuntimeError"}
        else:
            assert result["status"] == status and result["cleanup"] == "completed"
        if status == "network_error":
            assert events.count("failure:network_error") == 1
            assert events.index("failure:network_error") < events.index("disconnect")
        fd = os.open(directory / "telegram-account" / ".owner.lock", os.O_RDWR)
        try:
            assert_busy(fd)
            process.stdin.write(b"x")
            process.stdin.flush()
            process.wait(timeout=WALL_SECONDS * OBSERVATION_FACTOR)
            output, error = process.communicate(timeout=WALL_SECONDS * OBSERVATION_FACTOR)
            assert process.returncode == 0 and output == error == b""
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)


PROBE_SECONDS_DEFAULT = 0.1
PROBE_SECONDS_CAP = 1.0


def _cancelled_controller(monkeypatch, replacement, callback_failure=None):
    from zotero_summarizer.integrations import telegram_session as owner
    seconds = float(os.environ.get("ZS_M3_TIMEOUT_PROBE_SECONDS", PROBE_SECONDS_DEFAULT))
    assert math.isfinite(seconds) and 0 < seconds <= PROBE_SECONDS_CAP
    events = []

    async def work(state, collect, admit, factories, stop):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if replacement is None:
                raise
            raise replacement from None

    async def disconnect(state, deadline, reserve):
        events.append("disconnect")
        state["cleanup"] = "completed"

    def on_error(error):
        events.append(error.code)
        if callback_failure is not None:
            raise callback_failure

    monkeypatch.setattr(owner, "_work", work)
    monkeypatch.setattr(owner, "_disconnect", disconnect)
    state = {"status": "not_attempted", "phase": "connect", "error": None, "failure": None,
             "client": object(), "session": None, "on_error": on_error}

    async def execute():
        await owner._execute(state, None, None, (None, None), time.monotonic() + 2 * seconds, seconds)

    if callback_failure is None:
        asyncio.run(execute())  # Direct controller only: no fuse/lease/SDK path.
    else:
        with pytest.raises(type(callback_failure)) as caught:
            asyncio.run(execute())
        assert caught.value is callback_failure
    return state, events


@pytest.mark.parametrize("replacement,code", [
    (None, None), (TelegramTransportError("flood_wait", retry_seconds=23), "flood_wait"),
    (RuntimeError("synthetic replacement"), None), (TimeoutError("provider timeout"), "network_error"),
], ids=["genuine_owner_timeout", "mapped_replacement", "unknown_replacement", "provider_timeout_replacement"])
def test_received_error_survives_owner_timeout_cancellation(monkeypatch, replacement, code):
    state, events = _cancelled_controller(monkeypatch, replacement)
    assert state["cleanup"] == "completed"
    assert state["error"] is replacement
    assert events == ([code, "disconnect"] if code is not None else ["disconnect"])
    if replacement is None:
        assert state["status"] == "work_timeout" and state["failure"] is None
    else:
        assert state["status"] != "work_timeout"
        assert (state["failure"].code if state["failure"] is not None else None) == code


def test_persistence_timeout_after_expiry_is_not_owner_timeout(monkeypatch):
    replacement = TelegramTransportError("flood_wait", retry_seconds=31)
    state, events = _cancelled_controller(monkeypatch, replacement, TimeoutError("storage timeout"))
    assert state["error"] is replacement and state["failure"].code == "flood_wait"
    assert events == ["flood_wait", "disconnect"]
