"""Committed page flow over real SQLite; only async transport/clock are fake."""
from __future__ import annotations

import asyncio
import builtins
import json
from contextlib import closing
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from zotero_summarizer.models.research_feed import (
    ResolvedTelegramChannel, TelegramPage, TelegramPost, TelegramTransportError,
)
from zotero_summarizer.services.research_feed.telegram_collect import collect_pages
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import migrations, repositories
from zotero_summarizer.storage import research_feed_telegram as store


A = -1000000000001
B = -1000000000002
NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
PAPER_TABLES = ("rss_feeds", "rss_items", "processed_feed_items", "triage_results",
                "label_verdicts", "pending_changes", "user_feedback")


def _post(message, channel=A, **changes):
    values = dict(schema_version=1, channel_id=channel, message_id=message,
                  username="Alpha", permalink=f"https://example.test/{channel}/{message}",
                  text="  🧪 Подпись / caption\n原文  ", published_at="2026-10-02T12:00:00+03:00",
                  urls=["https://example.test/β", "https://example.test/a"])
    return TelegramPost(**(values | changes))


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
    return SimpleNamespace(settings=settings, opened=opened, open=real_open)


class HistoryClient:
    def __init__(self, env, histories=None, identities=None):
        self.env = env
        self.histories = histories if histories is not None else {A: []}
        self.identities = identities if identities is not None else {"alpha": A}
        self.calls = []
        self.fail_fetch = {}

    def _boundary(self):
        assert not self.env.opened[-1].in_transaction

    async def resolve_channel(self, *, username):
        self._boundary()
        self.calls.append(("resolve", username))
        return ResolvedTelegramChannel(channel_id=self.identities[username], public_broadcast=True)

    async def fetch_page(self, *, channel_id, after_id, limit):
        self._boundary()
        assert limit == 100
        self.calls.append(("fetch", channel_id, after_id, limit))
        with closing(self.env.open(self.env.settings.triage_db_path)) as conn:
            assert conn.execute("SELECT count(*) FROM research_feed_telegram_channels "
                                "WHERE channel_id=?", (channel_id,)).fetchone()[0] == 1
        failure = self.fail_fetch.pop(channel_id, None)
        if failure is not None:
            raise failure
        history = self.histories[channel_id]
        selected = history[-limit:] if after_id is None else [
            post for post in history if post.message_id > after_id][:limit]
        return TelegramPage(posts=tuple(selected), complete=True)

    async def connect(self):
        raise AssertionError("collector must not connect")

    async def disconnect(self):
        raise AssertionError("collector must not disconnect")

    async def start(self):
        raise AssertionError("collector must not authenticate")


def _run(env, client, usernames=None):
    return asyncio.run(collect_pages(env.settings, ["Alpha"] if usernames is None else usernames,
                                     client, now=lambda: NOW))


def _state(env, username="alpha"):
    with closing(env.open(env.settings.triage_db_path)) as conn:
        return store.read_channel_state(conn, username=username)


def _rows(env):
    with closing(env.open(env.settings.triage_db_path)) as conn:
        return store.list_posts(conn, limit=1000)


def test_initial_latest_hundred_sparse_unicode_and_skips(env):
    history = [_post(i * 3) for i in range(1, 151)]
    history[-1] = _post(450, text="", skip_reason="media_only")
    client = HistoryClient(env, {A: history})
    result = _run(env, client)
    assert client.calls == [("resolve", "alpha"), ("fetch", A, None, 100)]
    assert result["counts"] == dict(fetched=100, new=100, updated=0, unchanged=0, skipped=1)
    assert result["account"] == {"status": "not_deferred", "retry_at": None}
    assert result["channels"][0]["status"] == "committed"
    assert _state(env) == {"channel_id": A, "initial_window_committed": True,
                           "confirmed_message_id": 450}
    rows = _rows(env)
    assert [row["post"].message_id for row in rows] == list(range(153, 451, 3))
    assert rows[0]["post"].model_dump() == history[50].model_dump()
    assert rows[-1]["post"].skip_reason == "media_only"
    assert "text" not in result["channels"][0]
    json.dumps(result, allow_nan=False)


def test_backlog_drains_across_explicit_invocations_and_reopen_without_jumping(env):
    client = HistoryClient(env, {A: [_post(i) for i in range(1, 101)]})
    assert _run(env, client)["counts"]["new"] == 100
    client.histories[A].extend(_post(i) for i in range(101, 376))
    for expected_count, cursor in ((100, 200), (100, 300), (75, 375)):
        result = _run(env, client)
        assert result["counts"]["new"] == expected_count
        assert _state(env)["confirmed_message_id"] == cursor
    assert _run(env, client)["counts"]["fetched"] == 0
    assert [call[2] for call in client.calls if call[0] == "fetch"] == [None, 100, 200, 300, 375]
    rows = _rows(env)
    assert len(rows) == 375
    assert {row["post"].message_id for row in rows} == set(range(1, 376))
    assert _state(env)["confirmed_message_id"] == 375


def test_empty_initial_later_after_zero_and_distinct_publication_order(env):
    client = HistoryClient(env)
    assert _run(env, client)["channels"][0]["status"] == "committed"
    assert _state(env)["initial_window_committed"] is True
    assert _state(env)["confirmed_message_id"] is None
    client.histories[A] = [_post(7), _post(900, published_at="2000-01-01T00:00:00Z")]
    assert _run(env, client)["counts"]["new"] == 2
    assert client.calls[-1] == ("fetch", A, 0, 100)
    assert _state(env)["confirmed_message_id"] == 900
    assert _rows(env)[-1]["post"].message_id == 7
    client.histories[A].append(_post(1001))
    assert _run(env, client)["counts"]["new"] == 1
    assert client.calls[-1] == ("fetch", A, 900, 100)


def test_first_spelling_canonical_dedupe_numeric_aliases_and_composite_ids(env):
    client = HistoryClient(env, {A: [_post(7)], B: [_post(7, B)]},
                           {"alpha": A, "alias": A, "beta": B})
    result = _run(env, client, [" @ALpha ", "alpha", "@Alpha", "Alias", "Beta"])
    channels = result["channels"]
    assert [row["requested_username"] for row in channels] == [" @ALpha ", "Alias", "Beta"]
    assert [row["username"] for row in channels] == ["alpha", "alias", "beta"]
    assert [row["status"] for row in channels] == ["committed", "same_channel_already_seen", "committed"]
    assert client.calls == [("resolve", "alpha"), ("fetch", A, None, 100),
                            ("resolve", "alias"), ("resolve", "beta"), ("fetch", B, None, 100)]
    assert _state(env, "alias") == _state(env, "alpha")
    assert result["counts"]["fetched"] == 2
    assert {row["post"].source_id for row in _rows(env)} == {f"telegram:{A}:7", f"telegram:{B}:7"}


def test_failed_first_fetch_retains_binding_and_fresh_resolution_refuses_rebind(env):
    client = HistoryClient(env, {A: [_post(7)], B: [_post(8, B)]})
    client.fail_fetch[A] = TelegramTransportError("network_error")
    result = _run(env, client)
    assert result["channels"][0]["status"] == "network_error"
    assert result["counts"]["fetched"] == 0
    assert _state(env) == {"channel_id": A, "initial_window_committed": False,
                           "confirmed_message_id": None}
    client.identities["alpha"] = B
    result = _run(env, client, ["@ALPHA"])
    assert result["channels"][0]["status"] == "binding_mismatch"
    assert result["channels"][0]["channel_id"] == B
    assert client.calls[-1] == ("resolve", "alpha")
    with closing(env.open(env.settings.triage_db_path)) as conn:
        assert conn.execute("SELECT count(*) FROM research_feed_telegram_channels").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM research_feed_telegram_posts").fetchone()[0] == 0
    assert _state(env)["channel_id"] == A


def test_failed_fetch_consumes_numeric_channel_attempt_for_aliases(env):
    client = HistoryClient(env, {A: [_post(7)]}, {"alpha": A, "alias": A})
    client.fail_fetch[A] = TelegramTransportError("channel_unavailable")
    result = _run(env, client, ["Alpha", "Alias"])
    assert [row["status"] for row in result["channels"]] == ["channel_unavailable", "same_channel_already_seen"]
    assert len([call for call in client.calls if call[0] == "fetch"]) == 1
    assert _state(env, "alias")["initial_window_committed"] is False
    assert _rows(env) == []
    assert _run(env, client)["counts"]["new"] == 1


def test_saved_overlap_reuses_exact_original_hash_and_edit_invalidation(env):
    settings = env.settings
    migrations.run_migrations(settings.triage_db_path, "triage", migrations.TRIAGE_MIGRATIONS)
    first, second = _post(2), _post(9)
    with closing(env.open(settings.triage_db_path)) as conn:
        with conn:
            store.upsert_post(conn, first)
            store.upsert_post(conn, second)
            old = store.list_posts(conn, limit=10)
            for row in old:
                store.save_evaluation(conn, row, {"summary": "not an original"})
        exact_json = conn.execute("SELECT post_json FROM research_feed_telegram_posts "
                                  "WHERE message_id=2").fetchone()[0]
        assert store.read_channel_state(conn, username="alpha") is None
    edited = _post(9, text="  Исправленная 🧪 подпись\n原文  ", username="Renamed",
                   urls=list(reversed(second.urls)), updated_at="2026-10-03T00:00:00Z")
    client = HistoryClient(env, {A: [first, edited]})
    result = _run(env, client)
    assert result["counts"] == dict(fetched=2, new=0, updated=1, unchanged=1, skipped=0)
    current = _rows(env)
    assert current[0]["post"].model_dump() == first.model_dump()
    assert current[1]["post"].model_dump() == edited.model_dump()
    assert current[0]["content_hash"] == old[0]["content_hash"]
    assert current[1]["content_hash"] != old[1]["content_hash"]
    assert current[0]["evaluation"] == {"summary": "not an original"}
    assert current[1]["evaluation"] is None
    with closing(env.open(settings.triage_db_path)) as conn:
        assert conn.execute("SELECT post_json FROM research_feed_telegram_posts "
                            "WHERE message_id=2").fetchone()[0] == exact_json
        with conn:
            assert not store.save_evaluation(conn, old[1], {"summary": "stale"})


def test_selected_root_only_no_gate_startup_sdk_or_report_writes(env, monkeypatch):
    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith(("telethon", "zotero_summarizer.services.model",
                            "zotero_summarizer.services.llm", "zotero_summarizer.services.lifecycle")):
            raise AssertionError("collector activated an unrelated dependency")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    result = _run(env, HistoryClient(env, {A: [_post(7)]}))
    assert result["counts"]["new"] == 1
    settings = env.settings
    with closing(env.open(settings.triage_db_path)) as conn:
        assert all(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0 for table in PAPER_TABLES)
    assert not settings.corpus_db_path.exists()
    assert not settings.golden_csv_path.exists()
    assert not settings.golden_jsonl_path.exists()
    assert not settings.model_dir.exists()
    assert not settings.interaction_log_path.exists()
    assert not settings.config_path.exists()
    assert not (settings.data_dir / "research_feed").exists()
    assert all(path.name.startswith("triage_history.db") for path in settings.data_dir.iterdir())
