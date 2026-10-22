"""Auth entrypoints run only in owned children, with synthetic credentials/TTY/SDK."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys

import pytest

from test_telegram_cli_lifetime import OBSERVATION_FACTOR, WALL_SECONDS, assert_busy, owned_child, read_line


AUTH_CHILD = r'''
import asyncio, json, os, signal, sys, types
from datetime import datetime
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from zotero_summarizer.integrations import telegram_session as owner, telegram_auth
from zotero_summarizer.models.research_feed import TelegramTransportError
from zotero_summarizer.services.research_feed import telegram_runtime
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import repositories, research_feed_telegram as store
from contextlib import closing
mode, root = sys.argv[2], Path(sys.argv[3])
settings = Settings.load(project_root=root, env_path=root / 'absent.env')
sys.stdin.isatty = lambda: mode != 'no_tty'
sys.stderr.isatty = lambda: True
package = types.ModuleType('telethon')
errors = types.ModuleType('telethon.errors')
errors.SessionPasswordNeededError = type('SessionPasswordNeededError', (Exception,), {})
sys.modules['telethon'] = package
sys.modules['telethon.errors'] = errors
events = []
now = datetime.fromisoformat('2026-10-08T00:00:00+00:00')
path = settings.data_dir / 'telegram-account' / 'account.session'
if mode in {'existing', 'unsafe'}:
    path.parent.mkdir(parents=True, mode=0o700)
    path.write_bytes(b'unchanged synthetic existing session')
    path.chmod(0o600 if mode == 'existing' else 0o644)

class Session:
    def close(self):
        events.append('close')

class SDK:
    def __init__(self, session):
        self.session, self.authorized = session, mode == 'existing'
    async def connect(self):
        events.append('connect')
    async def is_user_authorized(self):
        events.append('authorized_check')
        return self.authorized
    async def send_code_request(self, phone):
        assert phone == 'synthetic-phone'
        events.append('send_code')
        if mode == 'flood':
            raise TelegramTransportError('flood_wait', retry_seconds=17)
        return types.SimpleNamespace(phone_code_hash='synthetic-code-hash')
    async def sign_in(self, **values):
        if 'code' in values:
            assert values == dict(phone='synthetic-phone', code='synthetic-code',
                                  phone_code_hash='synthetic-code-hash')
            events.append('code')
            raise errors.SessionPasswordNeededError()
        assert values == {'password': 'synthetic-password'}
        events.append('password')
        self.authorized = True
    def disconnect(self):
        events.append('disconnect')
        self.session.close()
        future = asyncio.get_running_loop().create_future()
        future.set_result(False)
        return future

secrets = iter(['synthetic-phone', 'synthetic-code', 'synthetic-password'])
def hidden(prompt):
    if mode == 'blocked_auth':
        print('blocked_auth', flush=True)
        while True:
            signal.pause()
    return next(secrets)
telegram_auth.getpass = hidden
def session_factory(selected):
    assert selected == path and path.stat().st_mode & 0o777 == 0o600
    assert signal.getitimer(signal.ITIMER_REAL)[0] > 0
    events.append('session')
    return Session()
def client_factory(session):
    events.append('client')
    return SDK(session)
try:
    result = telegram_runtime.collect_once_cli(settings, session_factory=session_factory,
        client_factory=client_factory, purpose='auth', now=lambda: now)
except (ValueError, OSError) as error:
    result = {'error_type': type(error).__name__}
if settings.triage_db_path.exists():
    with closing(repositories._connect_to(settings.triage_db_path)) as conn:
        retry = store.read_retry_at(conn)
else:
    retry = None
print(json.dumps({'result': result, 'events': events, 'retry_at': retry,
                  'session_exists': path.exists(),
                  'session_bytes': path.read_text() if mode == 'existing' else None}), flush=True)
if mode != 'no_tty':
    print('held', flush=True)
    assert sys.stdin.buffer.read(1) == b'x'
'''


def run_auth(tmp_path, mode):
    if sys.platform != "darwin" or not hasattr(asyncio, "Runner"):
        pytest.skip("native auth owner requires macOS CPython with Runner")
    with owned_child(tmp_path, mode, script=AUTH_CHILD) as (process, root):
        value = json.loads(read_line(process))
        if mode != "no_tty":
            assert read_line(process) == "held"
            fd = os.open(root / "data" / "telegram-account" / ".owner.lock", os.O_RDWR)
            try:
                assert_busy(fd)
                process.stdin.write(b"x")
                process.stdin.flush()
                output, error = process.communicate(timeout=WALL_SECONDS * OBSERVATION_FACTOR)
                assert output == error == b"" and process.returncode == 0
            finally:
                os.close(fd)
        else:
            output, error = process.communicate(timeout=WALL_SECONDS * OBSERVATION_FACTOR)
            assert output == error == b"" and process.returncode == 0
        return value


def test_auth_new_session_code_and_2fa_under_same_process_owner(tmp_path):
    value = run_auth(tmp_path, "new")
    assert value["result"]["collection"] == {"authorized": True}
    assert value["result"]["cleanup"] == "completed"
    assert value["events"] == ["session", "client", "connect", "authorized_check", "send_code",
                                "code", "password", "authorized_check", "disconnect", "close"]


def test_auth_existing_session_is_not_replaced_or_prompted(tmp_path):
    value = run_auth(tmp_path, "existing")
    assert value["result"]["collection"] == {"authorized": True}
    assert value["session_bytes"] == "unchanged synthetic existing session"
    assert value["events"] == ["session", "client", "connect", "authorized_check", "disconnect", "close"]


def test_auth_floodwait_durable_before_child_exit(tmp_path):
    value = run_auth(tmp_path, "flood")
    assert value["result"]["status"] == "flood_wait"
    assert value["retry_at"] == "2026-10-08T00:00:17+00:00"
    assert value["events"][-2:] == ["disconnect", "close"]
    assert "password" not in value["events"]


@pytest.mark.parametrize("mode", ["no_tty", "unsafe"])
def test_auth_rejects_noninteractive_or_unsafe_session_before_constructors(tmp_path, mode):
    value = run_auth(tmp_path, mode)
    assert value["result"]["error_type"] in {"ValueError", "OSError"}
    assert value["events"] == []
    assert value["session_exists"] == (mode == "unsafe")


def test_auth_blocked_prompt_keeps_lease_until_native_fuse_death(tmp_path):
    if sys.platform != "darwin" or not hasattr(asyncio, "Runner"):
        pytest.skip("native auth owner required")
    with owned_child(tmp_path, "blocked_auth", script=AUTH_CHILD) as (process, root):
        assert read_line(process) == "blocked_auth"
        fd = os.open(root / "data" / "telegram-account" / ".owner.lock", os.O_RDWR)
        try:
            assert_busy(fd)
            output, error = process.communicate(timeout=WALL_SECONDS * OBSERVATION_FACTOR)
            assert process.returncode == -signal.SIGALRM and output == error == b""
        finally:
            os.close(fd)


def test_hidden_prompt_refuses_getpass_echo_fallback(monkeypatch):
    import warnings
    from getpass import GetPassWarning
    from zotero_summarizer.integrations import telegram_auth
    reached = []
    def no_terminal(prompt):
        warnings.warn("synthetic terminal unavailable", GetPassWarning)
        reached.append("would_echo")
    monkeypatch.setattr(telegram_auth, "getpass", no_terminal)
    with pytest.raises(GetPassWarning):
        telegram_auth.prompt_secret("synthetic prompt")
    assert reached == []
