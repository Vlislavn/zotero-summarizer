"""Admission, per-request deferral and failure isolation with real selected SQLite."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from zotero_summarizer.models.research_feed import (
    ResolvedTelegramChannel, TelegramPage, TelegramPost, TelegramTransportError,
)
from zotero_summarizer.services.research_feed import telegram_collect as collector
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import migrations, repositories
from zotero_summarizer.storage import research_feed_telegram as store


A = -1000000000001
B = -1000000000002
NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


class Clock:
    def __init__(self, value=NOW):
        self.value = value
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.value


def _post(message=7, channel=A):
    return TelegramPost(schema_version=1, channel_id=channel, message_id=message,
                        text="🧪 Original", permalink=f"https://example.test/{channel}/{message}",
                        published_at="2026-10-08T00:00:00Z")


@pytest.fixture
def env(tmp_path, monkeypatch):
    settings = Settings.load(project_root=tmp_path / "selected", env_path=tmp_path / "absent.env")
    real_open = repositories._connect_to
    opened = []

    def tracked(path):
        conn = real_open(path)
        opened.append(conn)
        return conn

    monkeypatch.setattr(repositories, "_connect_to", tracked)
    return SimpleNamespace(settings=settings, opened=opened, open=real_open, clock=Clock())


class ScriptClient:
    def __init__(self, env):
        self.env = env
        self.resolutions = {"alpha": ResolvedTelegramChannel(channel_id=A, public_broadcast=True),
                            "beta": ResolvedTelegramChannel(channel_id=B, public_broadcast=True)}
        self.pages = {A: TelegramPage(posts=(_post(),), complete=True),
                      B: TelegramPage(posts=(_post(channel=B),), complete=True)}
        self.calls = []
        self.resolve_hook = None
        self.fetch_hook = None

    async def resolve_channel(self, *, username):
        assert not self.env.opened[-1].in_transaction
        self.calls.append(("resolve", username))
        if self.resolve_hook is not None:
            self.resolve_hook(username)
        result = self.resolutions[username]
        if isinstance(result, Exception):
            raise result
        return result

    async def fetch_page(self, *, channel_id, after_id, limit):
        assert not self.env.opened[-1].in_transaction
        assert limit == 100
        self.calls.append(("fetch", channel_id, after_id, limit))
        if self.fetch_hook is not None:
            self.fetch_hook(channel_id)
        result = self.pages[channel_id]
        if isinstance(result, Exception):
            raise result
        return result


def _run(env, client, usernames=None):
    return asyncio.run(collector.collect_pages(env.settings, ["Alpha", "Beta"] if usernames is None
                                               else usernames, client, now=env.clock))


def _seed(env):
    migrations.run_migrations(env.settings.triage_db_path, "triage", migrations.TRIAGE_MIGRATIONS)


def _defer(env, deadline):
    with closing(env.open(env.settings.triage_db_path)) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            return store.defer_account(conn, retry_at=deadline)


def _read(env):
    with closing(env.open(env.settings.triage_db_path)) as conn:
        return {"retry": store.read_retry_at(conn),
                "alpha": store.read_channel_state(conn, username="alpha"),
                "beta": store.read_channel_state(conn, username="beta"),
                "posts": [(r["post"].channel_id, r["post"].message_id)
                          for r in store.list_posts(conn, limit=1000)]}


@pytest.mark.parametrize("usernames", [None, "alpha", ("alpha",), True, [None], [7], [True], [""],
                                      ["@@alpha"], ["123"], ["___"], ["тест"], ["a" * 33],
                                      ["https://t.me/alpha"], ["+invite"], ["alpha", "@ALPHA", "a/b"]])
def test_whole_list_invalid_before_any_io_or_clock(env, monkeypatch, usernames):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid input performed I/O")

    monkeypatch.setattr(migrations, "run_migrations", forbidden)
    monkeypatch.setattr(repositories, "_connect_to", forbidden)
    client = ScriptClient(env)
    with pytest.raises(ValueError):
        asyncio.run(collector.collect_pages(env.settings, usernames, client, now=env.clock))
    assert client.calls == []
    assert env.clock.calls == 0
    assert not env.settings.data_dir.exists()


def test_empty_is_zero_io_not_checked_even_without_valid_clock(env, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("empty invocation performed I/O")

    monkeypatch.setattr(migrations, "run_migrations", forbidden)
    monkeypatch.setattr(repositories, "_connect_to", forbidden)
    result = asyncio.run(collector.collect_pages(object(), [], object(), now=forbidden))
    assert result == {"channels": [], "account": {"status": "not_checked", "retry_at": None},
                      "counts": dict(fetched=0, new=0, updated=0, unchanged=0, skipped=0)}
    assert not env.settings.data_dir.exists()


@pytest.mark.parametrize("value", [None, True, 1, "2026-10-08T00:00:00Z", datetime(2026, 10, 8),
                                  datetime.min.replace(tzinfo=timezone(timedelta(hours=1)))])
def test_initial_clock_invalid_before_io(env, value):
    env.clock.value = value
    client = ScriptClient(env)
    with pytest.raises(ValueError):
        _run(env, client)
    assert not env.settings.data_dir.exists()
    assert client.calls == []


@pytest.mark.parametrize("fields", [{"channel_id": True, "public_broadcast": True},
                                    {"channel_id": -1000000000000, "public_broadcast": True},
                                    {"channel_id": A, "public_broadcast": 1},
                                    {"channel_id": A, "public_broadcast": True, "extra": "private"}])
def test_resolved_value_strict_contract(fields):
    with pytest.raises(ValueError):
        ResolvedTelegramChannel(**fields)


@pytest.mark.parametrize("kind", ["mapping", "mutated", "bypassed"])
def test_bad_resolved_value_propagates_not_unavailable(env, kind):
    client = ScriptClient(env)
    if kind == "mapping":
        client.resolutions["alpha"] = {"channel_id": A, "public_broadcast": True}
    elif kind == "mutated":
        client.resolutions["alpha"].channel_id = True
    else:
        client.resolutions["alpha"] = ResolvedTelegramChannel.model_construct(channel_id=A, public_broadcast=1)
    with pytest.raises(ValueError, match="^invalid resolved Telegram channel$"):
        _run(env, client)
    assert client.calls == [("resolve", "alpha")]
    assert _read(env)["alpha"] is None


def test_nonpublic_resolution_diagnoses_without_binding_or_fetch(env):
    client = ScriptClient(env)
    client.resolutions["alpha"].public_broadcast = False
    result = _run(env, client)
    assert [r["status"] for r in result["channels"]] == ["channel_unavailable", "committed"]
    assert _read(env)["alpha"] is None
    assert result["counts"]["fetched"] == 1


@pytest.mark.parametrize("code,seconds", [("private-upstream-error", None), (None, None), (True, None),
                                        ([], None), ("flood_wait", None), ("flood_wait", True),
                                        ("flood_wait", 0), ("flood_wait", -1), ("flood_wait", 1.5),
                                        ("network_error", 5), ("missing_session", False)])
def test_transport_error_constructor_rejects_invalid_bounded_values(code, seconds):
    with pytest.raises(ValueError) as caught:
        TelegramTransportError(code, retry_seconds=seconds)
    assert "private-upstream-error" not in str(caught.value)


@pytest.mark.parametrize("phase", ["resolve", "fetch"])
@pytest.mark.parametrize("code", ["network_error", "channel_unavailable"])
def test_typed_channel_error_isolated_and_only_committed_counts(env, phase, code):
    client = ScriptClient(env)
    target = client.resolutions if phase == "resolve" else client.pages
    target["alpha" if phase == "resolve" else A] = TelegramTransportError(code)
    result = _run(env, client)
    assert [r["status"] for r in result["channels"]] == [code, "committed"]
    assert result["account"]["status"] == "not_deferred"
    assert result["counts"] == dict(fetched=1, new=1, updated=0, unchanged=0, skipped=0)
    state = _read(env)
    assert state["posts"] == [(B, 7)]
    assert state["retry"] is None
    assert (state["alpha"] is None) == (phase == "resolve")


@pytest.mark.parametrize("phase", ["resolve", "fetch"])
@pytest.mark.parametrize("code", ["missing_session", "session_unauthorized", "revoked_session"])
def test_account_error_stops_without_auth_inference_or_raw_error(env, phase, code):
    client = ScriptClient(env)
    error = TelegramTransportError(code)
    error.args = ("private-upstream-session-data",)
    target = client.resolutions if phase == "resolve" else client.pages
    target["alpha" if phase == "resolve" else A] = error
    result = _run(env, client)
    assert result["account"] == {"status": code, "retry_at": None}
    assert [r["status"] for r in result["channels"]] == [code, "not_attempted"]
    assert len(client.calls) == (1 if phase == "resolve" else 2)
    assert "private-upstream-session-data" not in json.dumps(result)
    assert _read(env)["posts"] == []
    assert _read(env)["retry"] is None


def test_future_deadline_before_resolve_and_exact_deadline_allowed_without_clear(env):
    _seed(env)
    deadline = _defer(env, NOW + timedelta(seconds=10))
    client = ScriptClient(env)
    result = _run(env, client)
    assert result["account"] == {"status": "deferred", "retry_at": deadline}
    assert client.calls == []
    assert all(r["status"] == "not_attempted" for r in result["channels"])
    env.clock.value = NOW + timedelta(seconds=10)
    assert _run(env, client)["counts"]["fetched"] == 2
    assert _read(env)["retry"] == deadline


@pytest.mark.parametrize("boundary", ["before_fetch", "before_next_resolve"])
def test_second_connection_installs_deadline_before_each_request(env, boundary):
    client = ScriptClient(env)

    def install(_identity):
        _defer(env, NOW + timedelta(hours=1))

    if boundary == "before_fetch":
        client.resolve_hook = install
    else:
        client.fetch_hook = install
    result = _run(env, client)
    assert result["account"]["status"] == "deferred"
    assert client.calls == ([("resolve", "alpha")] if boundary == "before_fetch" else
                            [("resolve", "alpha"), ("fetch", A, None, 100)])
    state = _read(env)
    assert state["alpha"] is not None
    assert state["alpha"]["initial_window_committed"] is (boundary == "before_next_resolve")
    assert state["beta"] is None
    assert result["counts"]["fetched"] == (0 if boundary == "before_fetch" else 1)


@pytest.mark.parametrize("phase", ["resolve", "fetch"])
def test_flood_wait_fresh_clock_durable_reopen_and_exact_retry(env, phase):
    client = ScriptClient(env)
    target = client.resolutions if phase == "resolve" else client.pages
    target["alpha" if phase == "resolve" else A] = TelegramTransportError("flood_wait", retry_seconds=10)

    def advance(_identity):
        env.clock.value = NOW + timedelta(seconds=7)

    if phase == "resolve":
        client.resolve_hook = advance
    else:
        client.fetch_hook = advance
    result = _run(env, client)
    deadline = (NOW + timedelta(seconds=17)).isoformat()
    assert result["account"] == {"status": "deferred", "retry_at": deadline}
    assert [r["status"] for r in result["channels"]] == ["flood_wait", "not_attempted"]
    assert _read(env)["retry"] == deadline
    retry = ScriptClient(env)
    assert _run(env, retry)["counts"]["fetched"] == 0
    assert retry.calls == []
    env.clock.value = NOW + timedelta(seconds=17)
    assert _run(env, retry)["counts"]["fetched"] == 2
    assert _read(env)["retry"] == deadline


def test_flood_preserves_later_concurrent_deadline_and_never_repairs_corrupt_time(env):
    client = ScriptClient(env)
    client.resolutions["alpha"] = TelegramTransportError("flood_wait", retry_seconds=10)
    client.resolve_hook = lambda _name: _defer(env, NOW + timedelta(hours=1))
    result = _run(env, client)
    assert result["account"]["retry_at"] == (NOW + timedelta(hours=1)).isoformat()
    with closing(env.open(env.settings.triage_db_path)) as conn:
        with conn:
            conn.execute("UPDATE research_feed_telegram_account SET retry_at='bad-private-time'")
    later = ScriptClient(env)
    with pytest.raises(ValueError, match="^invalid stored Telegram retry deadline$"):
        _run(env, later)
    assert later.calls == []


@pytest.mark.parametrize("kind", ["incomplete", "descending", "duplicate", "wrong_channel", "oversize", "mutated"])
def test_invalid_page_is_local_zero_writes_and_healthy_channel_continues(env, kind):
    client = ScriptClient(env)
    page = TelegramPage(posts=(_post(),), complete=True)
    bad = {"incomplete": TelegramPage(posts=(_post(),), complete=False),
           "descending": TelegramPage(posts=(_post(9), _post(7)), complete=True),
           "duplicate": TelegramPage(posts=(_post(), _post()), complete=True),
           "wrong_channel": TelegramPage(posts=(_post(channel=B),), complete=True),
           "oversize": TelegramPage.model_construct(posts=tuple(_post(i) for i in range(1, 102)), complete=True)}
    page.posts[0].message_id = True
    bad["mutated"] = page
    client.pages[A] = bad[kind]
    result = _run(env, client)
    assert [r["status"] for r in result["channels"]] == ["invalid_page", "committed"]
    assert result["counts"]["fetched"] == 1
    state = _read(env)
    assert state["alpha"]["initial_window_committed"] is False
    assert state["alpha"]["confirmed_message_id"] is None
    assert state["posts"] == [(B, 7)]


@pytest.mark.parametrize("phase", ["resolve", "fetch"])
def test_unexpected_client_value_error_not_relabelled_invalid_page(env, phase):
    client = ScriptClient(env)
    target = client.resolutions if phase == "resolve" else client.pages
    target["alpha" if phase == "resolve" else A] = ValueError("injected programming error")
    with pytest.raises(ValueError, match="injected programming error"):
        _run(env, client)
    assert len(client.calls) == (1 if phase == "resolve" else 2)


@pytest.mark.parametrize("failure", [ValueError, sqlite3.OperationalError])
def test_second_commit_error_rolls_back_page_but_preserves_first(env, monkeypatch, failure):
    real_commit = store.commit_page

    def fail_after_write(conn, **kwargs):
        counts = real_commit(conn, **kwargs)
        if kwargs["channel_id"] == B:
            raise failure("injected second commit error")
        return counts

    client = ScriptClient(env)
    with monkeypatch.context() as patch:
        patch.setattr(store, "commit_page", fail_after_write)
        with pytest.raises(failure, match="second commit"):
            _run(env, client)
    state = _read(env)
    assert state["posts"] == [(A, 7)]
    assert state["alpha"]["initial_window_committed"] is True
    assert state["beta"]["initial_window_committed"] is False
    assert state["beta"]["confirmed_message_id"] is None
    assert _run(env, ScriptClient(env), ["Beta"])["counts"]["new"] == 1


def test_commit_stale_state_propagates_and_preserves_earlier_page(env):
    client = ScriptClient(env)

    def race(channel):
        if channel == B:
            with closing(env.open(env.settings.triage_db_path)) as conn:
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    store.commit_page(conn, username="beta", channel_id=B,
                                      expected_state=(False, None), page=TelegramPage(posts=(), complete=True))

    client.fetch_hook = race
    with pytest.raises(ValueError, match="stale"):
        _run(env, client)
    state = _read(env)
    assert state["posts"] == [(A, 7)]
    assert state["beta"]["initial_window_committed"] is True
    assert state["beta"]["confirmed_message_id"] is None


@pytest.mark.parametrize("kind", ["mutated_error", "unknown_code", "bad_later_clock", "overflow"])
def test_malformed_error_clock_or_deadline_overflow_propagates_without_defer(env, kind):
    client = ScriptClient(env)
    error = TelegramTransportError("flood_wait", retry_seconds=10)
    client.pages[A] = error
    if kind == "mutated_error":
        error.retry_seconds = True
    elif kind == "unknown_code":
        error.code = "private-upstream-error"
    elif kind == "bad_later_clock":
        client.resolve_hook = lambda _name: setattr(env.clock, "value", datetime(2026, 10, 8))
    else:
        client.fetch_hook = lambda _channel: setattr(env.clock, "value", datetime.max.replace(tzinfo=timezone.utc))
    with pytest.raises((ValueError, OverflowError)):
        _run(env, client)
    assert _read(env)["retry"] is None
    assert _read(env)["posts"] == []


def test_non_utc_aware_clock_normalizes_at_exact_deadline(env):
    _seed(env)
    deadline = _defer(env, NOW)
    env.clock.value = datetime(2026, 10, 8, 3, tzinfo=timezone(timedelta(hours=3)))
    result = _run(env, ScriptClient(env), ["Alpha"])
    assert result["account"] == {"status": "not_deferred", "retry_at": deadline}
    assert result["counts"]["fetched"] == 1
    assert _read(env)["retry"] == deadline


def test_corrupt_bound_state_propagates_after_fresh_resolution_without_fetch(env):
    _seed(env)
    with closing(env.open(env.settings.triage_db_path)) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            store.bind_channel(conn, username="alpha", channel_id=A)
            conn.execute("UPDATE research_feed_telegram_channels SET confirmed_message_id=7")
    client = ScriptClient(env)
    with pytest.raises(ValueError, match="state"):
        _run(env, client)
    assert client.calls == [("resolve", "alpha")]
    with closing(env.open(env.settings.triage_db_path)) as conn:
        assert tuple(conn.execute("SELECT initial_window_committed, confirmed_message_id "
                                  "FROM research_feed_telegram_channels").fetchone()) == (0, 7)
        assert conn.execute("SELECT count(*) FROM research_feed_telegram_posts").fetchone()[0] == 0


def test_flood_persistence_error_rolls_back_deadline_and_keeps_earlier_page(env, monkeypatch):
    real_defer = store.defer_account

    def fail_after_defer(conn, *, retry_at):
        real_defer(conn, retry_at=retry_at)
        raise sqlite3.OperationalError("injected defer failure")

    client = ScriptClient(env)
    client.pages[B] = TelegramTransportError("flood_wait", retry_seconds=10)
    monkeypatch.setattr(store, "defer_account", fail_after_defer)
    with pytest.raises(sqlite3.OperationalError, match="defer failure"):
        _run(env, client)
    state = _read(env)
    assert state["retry"] is None
    assert state["posts"] == [(A, 7)]
    assert state["beta"]["initial_window_committed"] is False


def test_new_values_are_direct_module_only_without_root_export_expansion():
    from zotero_summarizer import models

    for name in ("ResolvedTelegramChannel", "TelegramTransportError"):
        assert name not in models.__all__
        assert not hasattr(models, name)
