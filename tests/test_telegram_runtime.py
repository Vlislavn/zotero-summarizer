"""Real selected SQLite/M1b through M3; all armed entry calls are owned children."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_telegram_cli_lifetime import OBSERVATION_FACTOR, WALL_SECONDS, assert_busy, owned_child, read_line
from zotero_summarizer.models.research_feed import ResearchProfile
from zotero_summarizer.services.research_feed import profile, telegram_runtime as runtime
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import repositories, research_feed_telegram as store


NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
CHANNEL = -1000000000001
COMPOSITION = r'''
import asyncio, hashlib, json, os, sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path[:0] = [sys.argv[1], *sys.argv[4:]]
from zotero_summarizer.models.research_feed import (
    ResearchProfile, ResolvedTelegramChannel, TelegramPage, TelegramPost, TelegramTransportError)
from zotero_summarizer.services.research_feed import profile, telegram_runtime as runtime
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import migrations, repositories, research_feed_telegram as store
mode, root = sys.argv[2], Path(sys.argv[3])
settings = Settings.load(project_root=root, env_path=root / 'absent.env')
now = datetime(2026, 10, 8, tzinfo=timezone.utc)
clock = [now]
channel = -1000000000001
events = []
readonly = [False]
after_cancel = [False]
if mode == 'cancel_flood_blocked_close':
    real_open = repositories._connect_to
    def failure_clock_open(path):
        if after_cancel[0]:
            clock[0] = now + timedelta(hours=1)
        return real_open(path)
    repositories._connect_to = failure_clock_open
if mode == 'persist_failure':
    from zotero_summarizer.integrations import telegram_session as owner
    import signal
    real_open = repositories._connect_to
    real_pause = signal.pause
    def controlled_open(path):
        conn = real_open(path)
        if readonly[0]:
            conn.execute('PRAGMA query_only=ON')
        return conn
    def observed_pause():
        print('hold', flush=True)
        return real_pause()
    repositories._connect_to = controlled_open
    owner.signal.pause = observed_pause
path = profile.profile_path(settings.data_dir)
path.parent.mkdir(parents=True, exist_ok=True)
channels = ['Alpha', 'Beta'] if mode == 'later_failure' else [' @ALPHA ', 'alpha']
path.write_text(ResearchProfile(themes=['agents'], projects=[], telegram_channels=channels).model_dump_json())
account = settings.data_dir / 'telegram-account'
account.mkdir(mode=0o700, exist_ok=True)
session_path = account / 'account.session'
session_path.touch(mode=0o600)

def defer(seconds):
    migrations.run_migrations(settings.triage_db_path, 'triage', migrations.TRIAGE_MIGRATIONS)
    with closing(repositories._connect_to(settings.triage_db_path)) as conn:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            store.defer_account(conn, retry_at=now + timedelta(seconds=seconds))

if mode in {'future', 'exact', 'expired'}:
    defer({'future': 1, 'exact': 0, 'expired': -1}[mode])
if mode.startswith('unsafe:'):
    kind = mode.split(':')[1]
    if kind in {'missing', 'symlink', 'directory', 'fifo'}:
        session_path.unlink()
    if kind == 'symlink':
        session_path.symlink_to(account / 'absent-target')
    elif kind == 'directory':
        session_path.mkdir()
    elif kind == 'public':
        session_path.chmod(0o644)
    elif kind == 'hardlink':
        os.link(session_path, account / 'alias.session')
    elif kind == 'fifo':
        os.mkfifo(session_path)

class Session:
    def close(self):
        events.append('session_close')
        if mode == 'persist_failure':
            print('closed', flush=True)
        if mode in {'flood_blocked_close', 'cancel_flood_blocked_close'}:
            clock[0] = now + timedelta(hours=1)
            print('blocked_close', flush=True)
            while True:
                import signal
                signal.pause()

class SDK:
    def __init__(self, session):
        self.session, self.connected = session, False
    async def connect(self):
        events.append('connect')
        if mode == 'cancel_flood_blocked_close':
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                after_cancel[0] = True
                print('translated_cancel', flush=True)
                raise TelegramTransportError('flood_wait', retry_seconds=17) from None
        if mode.startswith('connect:') or mode in {'flood_blocked_close', 'persist_failure'}:
            readonly[0] = mode == 'persist_failure'
            code = 'flood_wait' if mode in {'flood_blocked_close', 'persist_failure'} else mode.split(':')[1]
            error = TelegramTransportError(code, retry_seconds=5 if code == 'flood_wait' else None)
            error.args = ('private account/session body',)
            raise error
        self.connected = True
        return False
    async def resolve_channel(self, *, username):
        assert self.connected
        events.append(['resolve', username])
        if username == 'beta':
            raise RuntimeError('private collection failure')
        return ResolvedTelegramChannel(channel_id=channel, public_broadcast=True)
    async def fetch_page(self, *, channel_id, after_id, limit):
        events.append(['fetch', channel_id, after_id, limit])
        post = TelegramPost(schema_version=1, channel_id=channel, message_id=7,
                            text='  🧪 original  ', permalink='https://t.me/alpha/7', published_at=now.isoformat())
        return TelegramPage(posts=(post,), complete=True)
    def disconnect(self):
        events.append('disconnect')
        if mode == 'persist_failure':
            print('disconnect', flush=True)
        future = asyncio.get_running_loop().create_future()
        self.session.close()
        future.set_result(False)
        return future

if mode == 'sdk_leaf':
    import telethon
    from telethon.tl import functions, types
    from zotero_summarizer.integrations import telegram
    sdk_identity = {'version': telethon.__version__, 'modules': {}}
    for name in ('telethon', 'telethon.version', 'telethon.tl.functions', 'telethon.tl.types',
                 'zotero_summarizer.integrations.telegram'):
        source = Path(sys.modules[name].__file__).resolve()
        sdk_identity['modules'][name] = {
            'path': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    async def request(self, value):
        assert self.connected
        if isinstance(value, functions.contacts.ResolveUsernameRequest):
            native = types.Channel(1, 'fixture', types.ChatPhotoEmpty(), now,
                                   broadcast=True, username='alpha', access_hash=7)
            return types.contacts.ResolvedPeer(types.PeerChannel(1), [native], [])
        assert isinstance(value, functions.messages.GetHistoryRequest)
        assert value.limit == 100 and value.offset_id == value.add_offset == 0
        message = types.Message(id=7, peer_id=types.PeerChannel(1), date=now, message='  🧪 original  ')
        return types.messages.Messages([message], [], [], [])
    SDK.__call__ = request
else:
    runtime._page_client = lambda sdk: sdk  # Explicit fake page boundary; NOT SDK coverage.

def session_factory(path):
    assert path == session_path
    events.append('session_factory')
    return Session()

def client_factory(session):
    events.append('client_factory')
    if mode == 'defer_before_connect':
        defer(5)
    return SDK(session)

try:
    result = runtime.collect_once_cli(settings, session_factory=session_factory,
                                     client_factory=client_factory, now=lambda: clock[0])
    if mode == 'repeat':
        try:
            runtime.collect_once_cli(settings, session_factory=session_factory,
                                     client_factory=client_factory, now=lambda: now)
        except RuntimeError:
            events.append('second_refused')
except (RuntimeError, ValueError, OSError) as error:
    result = {'error_type': type(error).__name__}
receipt = {'result': result, 'events': events}
if mode == 'sdk_leaf':
    receipt['sdk_identity'] = sdk_identity
print(json.dumps(receipt, ensure_ascii=False), flush=True)
print('return_held', flush=True)
assert sys.stdin.buffer.read(1) == b'x'
'''


def run_case(tmp_path, mode, *, import_roots=()):
    if sys.platform != "darwin" or sys.implementation.name != "cpython" or not hasattr(asyncio, "Runner"):
        pytest.skip("actual M3 child requires macOS CPython with asyncio.Runner")
    with owned_child(tmp_path, mode, script=COMPOSITION, import_roots=import_roots) as (process, root):
        value = json.loads(read_line(process))
        assert read_line(process) == "return_held"
        lock = root / "data" / "telegram-account" / ".owner.lock"
        import fcntl
        fd = os.open(lock, os.O_RDWR)
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
        return value, root / "data" / "triage_history.db"


def test_real_collector_commit_and_one_disconnect_with_future(tmp_path):
    value, database = run_case(tmp_path, "repeat")
    result, events = value["result"], value["events"]
    assert result["status"] == "collected" and result["cleanup"] == "completed"
    assert result["collection"]["counts"] == dict(fetched=1, new=1, updated=0, unchanged=0, skipped=0)
    assert events == ["session_factory", "client_factory", "connect", ["resolve", "alpha"],
                      ["fetch", CHANNEL, None, 100], "disconnect", "session_close", "second_refused"]
    with closing(repositories._connect_to(database)) as conn:
        assert store.read_channel_state(conn, username="alpha")["confirmed_message_id"] == 7
        assert store.list_posts(conn, limit=10)[0]["post"].text == "  🧪 original  "


def test_missing_profile_readonly_and_existing_default_preserved(tmp_path):
    settings = Settings.load(project_root=tmp_path, env_path=tmp_path / "absent.env")
    value = profile.load_profile(settings.data_dir, persist_default=False)
    assert value.telegram_channels == [] and not settings.data_dir.exists()
    assert runtime.collect_once_cli(settings)["status"] == "no_channels"  # Guaranteed unarmed.
    assert not settings.data_dir.exists()
    assert profile.load_profile(settings.data_dir) == value
    assert profile.profile_path(settings.data_dir).is_file()


def test_factory_unconfigured_is_not_available_collection(tmp_path):
    settings = Settings.load(project_root=tmp_path, env_path=tmp_path / "absent.env")
    path = profile.profile_path(settings.data_dir)
    path.parent.mkdir(parents=True)
    path.write_text(ResearchProfile(themes=[], projects=[], telegram_channels=["alpha"]).model_dump_json())
    result = runtime.collect_once_cli(settings)  # Guaranteed unarmed, no factories.
    assert result["status"] == "app_identity_unconfigured" and result["collection"] is None
    assert not settings.triage_db_path.exists()
    assert not (settings.data_dir / "telegram-account").exists()


@pytest.mark.parametrize("kind", ["missing", "symlink", "directory", "public", "hardlink", "fifo"])
def test_unsafe_or_missing_session_before_both_factories(tmp_path, kind):
    value, _ = run_case(tmp_path, f"unsafe:{kind}")
    assert value["events"] == []
    if kind == "missing":
        assert value["result"]["status"] == "missing_session"
        assert value["result"]["cleanup"] == "not_started"
    else:
        assert value["result"]["error_type"] in {"ValueError", "OSError"}


@pytest.mark.parametrize("mode,seconds", [("future", 1), ("exact", 0), ("expired", -1)])
def test_durable_retry_before_constructors_exact_deadline_and_expiry(tmp_path, mode, seconds):
    value, database = run_case(tmp_path, mode)
    assert value["result"]["status"] == ("deferred" if seconds > 0 else "collected")
    assert ("session_factory" in value["events"]) == (seconds <= 0)
    with closing(repositories._connect_to(database)) as conn:
        assert store.read_retry_at(conn) == (NOW + timedelta(seconds=seconds)).isoformat()


def test_retry_reread_after_factories_before_connect(tmp_path):
    value, _ = run_case(tmp_path, "defer_before_connect")
    assert value["result"]["status"] == "deferred"
    assert value["events"] == ["session_factory", "client_factory", "disconnect", "session_close"]


@pytest.mark.parametrize("code", ["missing_session", "revoked_session", "session_unauthorized",
                                  "network_error", "channel_unavailable", "flood_wait"])
def test_connect_error_uses_existing_types_and_durable_flood(tmp_path, code):
    value, database = run_case(tmp_path, f"connect:{code}")
    result, events = value["result"], value["events"]
    assert result["status"] == code and result["collection"] is None
    assert result["cleanup"] == "completed" and events[-2:] == ["disconnect", "session_close"]
    assert not any(isinstance(event, list) for event in events)
    assert "private account" not in json.dumps(result)
    with closing(repositories._connect_to(database)) as conn:
        assert store.read_retry_at(conn) == ((NOW + timedelta(seconds=5)).isoformat()
                                            if code == "flood_wait" else None)


def test_later_unexpected_failure_preserves_prior_page_not_fabricated_counts(tmp_path):
    value, database = run_case(tmp_path, "later_failure")
    assert value["result"] == {"error_type": "RuntimeError"}
    assert value["events"][-2:] == ["disconnect", "session_close"]
    with closing(repositories._connect_to(database)) as conn:
        assert store.read_channel_state(conn, username="alpha")["confirmed_message_id"] == 7
        assert len(store.list_posts(conn, limit=10)) == 1
        assert store.read_channel_state(conn, username="beta") is None


def test_optional_actual_m2a_leaf_through_m3_and_sqlite(tmp_path):
    telethon = pytest.importorskip("telethon")
    from zotero_summarizer.integrations import telegram

    assert telethon.__version__ == "1.45.0"
    sdk_root = Path(telethon.__file__).resolve().parents[1]
    leaf = Path(__file__).resolve().parents[1] / "zotero_summarizer/integrations/telegram.py"
    assert Path(telegram.__file__).resolve() == leaf
    expected = {"version": telethon.__version__, "modules": {}}
    for name in ("telethon", "telethon.version", "telethon.tl.functions", "telethon.tl.types",
                 "zotero_summarizer.integrations.telegram"):
        source = Path(sys.modules[name].__file__).resolve()
        if name.startswith("telethon"):
            assert source.is_relative_to(sdk_root)
        expected["modules"][name] = {
            "path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    value, database = run_case(tmp_path, "sdk_leaf", import_roots=(sdk_root,))
    assert value["sdk_identity"] == expected
    (tmp_path / "sdk-source-identity.json").write_text(json.dumps(
        {"parent": expected, "child": value["sdk_identity"]}, indent=2) + "\n")
    assert value["result"]["collection"]["counts"]["new"] == 1
    with closing(repositories._connect_to(database)) as conn:
        assert store.read_channel_state(conn, username="alpha")["confirmed_message_id"] == 7


@pytest.mark.parametrize("wall,reserve", [("nan", "1"), ("301", "1"), ("0", "1"),
                                         ("True", "1"), ("2", "2"), ("2", "0")])
def test_invalid_budgets_before_sdk_and_account_side_effects(tmp_path, monkeypatch, wall, reserve):
    from zotero_summarizer.integrations import telegram_session as owner
    settings = Settings.load(project_root=tmp_path, env_path=tmp_path / "absent.env")
    monkeypatch.setenv("ZS_TELEGRAM_COLLECTION_WALL_SECONDS", wall)
    monkeypatch.setenv("ZS_TELEGRAM_CLEANUP_RESERVE_SECONDS", reserve)
    with pytest.raises(ValueError):
        owner.collect_once_cli(settings, None, admit=None, on_error=None)  # Guaranteed budget rejection.
    assert not settings.data_dir.exists()


@pytest.mark.parametrize("module,attribute,value", [
    ("sys", "platform", "unsupported"), ("threading", "current_thread", object),
    ("asyncio", "Runner", None), ("asyncio", "get_running_loop", object),
    ("signal", "getsignal", lambda sig: signal.SIG_IGN),
    ("signal", "getitimer", lambda timer: (1.0, 0.0)),
    ("signal", "pthread_sigmask", lambda how, mask: {signal.SIGALRM}),
    ("signal", "sigpending", lambda: {signal.SIGALRM}),
])
def test_observable_conflicts_are_unarmed_admission_only(monkeypatch, module, attribute, value):
    from zotero_summarizer.integrations import telegram_session as owner
    if not hasattr(owner.signal, "SIGALRM") or not hasattr(asyncio, "Runner"):
        pytest.skip("signal/Runner admission primitive unavailable")
    monkeypatch.setattr(owner.sys, "platform", "darwin")
    monkeypatch.setattr(owner.signal, "getsignal", lambda sig: owner.signal.SIG_DFL)
    monkeypatch.setattr(owner.signal, "getitimer", lambda timer: (0.0, 0.0))
    monkeypatch.setattr(owner.signal, "pthread_sigmask", lambda how, mask: set())
    monkeypatch.setattr(owner.signal, "sigpending", set)
    if attribute == "Runner":
        monkeypatch.delattr(owner.asyncio, "Runner")
    else:
        monkeypatch.setattr(getattr(owner, module), attribute, value)
    with pytest.raises(RuntimeError):
        owner._check_platform()  # This helper cannot arm or construct anything.


def test_flood_failure_time_is_durable_before_blocked_cleanup_and_reentry(tmp_path):
    if sys.platform != "darwin" or not hasattr(asyncio, "Runner"):
        pytest.skip("actual native M3 child required")
    import fcntl
    with owned_child(tmp_path, "flood_blocked_close", script=COMPOSITION) as (process, root):
        assert read_line(process) == "blocked_close"
        fd = os.open(root / "data" / "telegram-account" / ".owner.lock", os.O_RDWR)
        try:
            assert_busy(fd)
            with closing(repositories._connect_to(root / "data" / "triage_history.db")) as conn:
                assert store.read_retry_at(conn) == (NOW + timedelta(seconds=5)).isoformat()
            process.wait(timeout=WALL_SECONDS * OBSERVATION_FACTOR)
            output, error = process.communicate(timeout=WALL_SECONDS * OBSERVATION_FACTOR)
            assert process.returncode == -signal.SIGALRM and output == error == b""
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
    value, database = run_case(tmp_path, "resume")
    assert value["events"] == [] and value["result"]["status"] == "deferred"
    with closing(repositories._connect_to(database)) as conn:
        assert store.read_retry_at(conn) == (NOW + timedelta(seconds=5)).isoformat()


def test_work_timeout_replacement_flood_is_durable_before_blocked_cleanup(tmp_path):
    if sys.platform != "darwin" or not hasattr(asyncio, "Runner"):
        pytest.skip("actual native M3 child required")
    import fcntl
    with owned_child(tmp_path, "cancel_flood_blocked_close", script=COMPOSITION) as (process, root):
        assert [read_line(process) for _ in range(2)] == ["translated_cancel", "blocked_close"]
        fd = os.open(root / "data" / "telegram-account" / ".owner.lock", os.O_RDWR)
        try:
            assert_busy(fd)
            with closing(repositories._connect_to(root / "data" / "triage_history.db")) as conn:
                assert store.read_retry_at(conn) == (NOW + timedelta(seconds=17)).isoformat()
            process.wait(timeout=WALL_SECONDS * OBSERVATION_FACTOR)
            output, error = process.communicate(timeout=WALL_SECONDS * OBSERVATION_FACTOR)
            assert process.returncode == -signal.SIGALRM and output == error == b""
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
    value, database = run_case(tmp_path, "resume")
    assert value["events"] == [] and value["result"]["status"] == "deferred"
    with closing(repositories._connect_to(database)) as conn:
        assert store.read_retry_at(conn) == (NOW + timedelta(seconds=17)).isoformat()


def test_failed_real_sqlite_deferral_tears_down_once_then_holds_without_receipt(tmp_path):
    if sys.platform != "darwin" or not hasattr(asyncio, "Runner"):
        pytest.skip("actual native M3 child required")
    import fcntl
    with owned_child(tmp_path, "persist_failure", script=COMPOSITION) as (process, root):
        assert [read_line(process) for _ in range(3)] == ["disconnect", "closed", "hold"]
        fd = os.open(root / "data" / "telegram-account" / ".owner.lock", os.O_RDWR)
        try:
            assert_busy(fd)
            with closing(repositories._connect_to(root / "data" / "triage_history.db")) as conn:
                assert store.read_retry_at(conn) is None
            process.wait(timeout=WALL_SECONDS * OBSERVATION_FACTOR)
            output, error = process.communicate(timeout=WALL_SECONDS * OBSERVATION_FACTOR)
            assert process.returncode == -signal.SIGALRM and output == error == b""
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
