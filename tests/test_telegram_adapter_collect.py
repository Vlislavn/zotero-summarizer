"""Actual TL values through the unchanged collector and real selected SQLite."""
from __future__ import annotations

import asyncio
import builtins
import json
from contextlib import closing
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("telethon")
from telethon.errors import rpcerrorlist
from telethon.tl import functions, types

from zotero_summarizer.integrations.telegram import TelethonPageClient
from zotero_summarizer.models.research_feed import TelegramPost
from zotero_summarizer.services.research_feed.telegram_collect import collect_pages
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import migrations, repositories
from zotero_summarizer.storage import research_feed_telegram as store


A = -1000000000001
B = -1000000000002
NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


def original(number, native=1, **changes):
    return types.Message(**(dict(id=number, peer_id=types.PeerChannel(native), date=NOW,
                                message="  🧪 Original / подпись\n  ") | changes))


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
    return SimpleNamespace(settings=settings, open=real_open, opened=opened, now=NOW)


class SDK:
    def __init__(self, env):
        self.env = env
        self.identities = {"alpha": 1, "alias": 1, "beta": 2}
        self.histories = {1: [], 2: []}
        self.failures = {}
        self.calls = []
        self.hook = None
        self.access_hash = 7

    async def __call__(self, request):
        assert not self.env.opened[-1].in_transaction
        self.calls.append(request)
        if isinstance(request, functions.contacts.ResolveUsernameRequest):
            phase, identity = "resolve", request.username
        else:
            assert type(request) is functions.messages.GetHistoryRequest
            assert request.limit == 100 and request.offset_date is None
            assert (request.min_id, request.max_id, request.hash) == (0, 0, 0)
            phase, identity = "fetch", request.peer.channel_id
        if self.hook is not None:
            self.hook(phase, identity)
        failure = self.failures.pop((phase, identity), None)
        if failure is not None:
            raise failure
        if phase == "resolve":
            native = self.identities[identity]
            value = types.Channel(native, "fixture", types.ChatPhotoEmpty(), NOW,
                                  broadcast=True, username=identity, access_hash=self.access_hash)
            return types.contacts.ResolvedPeer(types.PeerChannel(native), [value], [])
        source = self.histories[identity]
        if request.offset_id == 0:
            assert request.add_offset == 0
            selected = source[-100:]
        else:
            assert request.add_offset == -100
            selected = [post for post in source if post.id >= request.offset_id][:100]
        return types.messages.Messages(list(reversed(selected)), [], [], [])

    def __getattr__(self, name):
        raise AssertionError("leaf attempted convenience/lifecycle I/O")


def run(env, sdk, usernames=None):
    return asyncio.run(collect_pages(env.settings, ["alpha"] if usernames is None else usernames,
                                     TelethonPageClient(sdk), now=lambda: env.now))


def seed(env):
    migrations.run_migrations(env.settings.triage_db_path, "triage", migrations.TRIAGE_MIGRATIONS)


def state(env, username="alpha"):
    with closing(env.open(env.settings.triage_db_path)) as conn:
        return store.read_channel_state(conn, username=username)


def rows(env):
    with closing(env.open(env.settings.triage_db_path)) as conn:
        return store.list_posts(conn, limit=1000)


def defer(env, deadline):
    with closing(env.open(env.settings.triage_db_path)) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            return store.defer_account(conn, retry_at=deadline)


def retry_at(env):
    with closing(env.open(env.settings.triage_db_path)) as conn:
        return store.read_retry_at(conn)


def test_bootstrap_sparse_last100_then_275_newer_reopen_without_jump(env):
    sdk = SDK(env)
    sdk.histories[1] = [original(i * 3) for i in range(1, 151)]
    assert run(env, sdk)["counts"]["new"] == 100
    assert {row["post"].message_id for row in rows(env)} == set(range(153, 451, 3))
    sdk.histories[1].extend(original(i * 3, date=NOW - timedelta(days=1)) for i in range(151, 426))
    for count, cursor in ((100, 750), (100, 1050), (75, 1275)):
        assert run(env, sdk)["counts"]["new"] == count
        assert state(env)["confirmed_message_id"] == cursor
    assert run(env, sdk)["counts"]["fetched"] == 0
    requests = [r for r in sdk.calls if isinstance(r, functions.messages.GetHistoryRequest)]
    assert [r.offset_id for r in requests] == [0, 451, 751, 1051, 1276]
    assert len(rows(env)) == 375
    assert {row["post"].message_id for row in rows(env)} == set(range(153, 1276, 3))


def test_empty_initializer_after0_and_composite_identity(env):
    sdk = SDK(env)
    assert run(env, sdk)["channels"][0]["status"] == "committed"
    assert state(env)["initial_window_committed"] and state(env)["confirmed_message_id"] is None
    sdk.histories = {1: [original(7)], 2: [original(7, 2)]}
    result = run(env, sdk, ["alpha", "beta"])
    assert result["counts"]["new"] == 2
    assert sdk.calls[-3].offset_id == 1
    assert {row["post"].source_id for row in rows(env)} == {f"telegram:{A}:7", f"telegram:{B}:7"}


def test_failed_attempt_alias_binding_and_fresh_rebind_refusal(env):
    sdk = SDK(env)
    sdk.failures[("fetch", 1)] = ConnectionError("private_fixture")
    result = run(env, sdk, [" @ALPHA ", "alpha", "alias"])
    assert [row["status"] for row in result["channels"]] == ["network_error", "same_channel_already_seen"]
    assert len([r for r in sdk.calls if isinstance(r, functions.messages.GetHistoryRequest)]) == 1
    assert state(env, "alias") == state(env)
    assert not state(env)["initial_window_committed"]
    sdk.identities["alpha"] = 2
    before = len(sdk.calls)
    assert run(env, sdk)["channels"][0]["status"] == "binding_mismatch"
    assert len(sdk.calls) == before + 1
    assert state(env)["channel_id"] == A
    with closing(env.open(env.settings.triage_db_path)) as conn:
        assert conn.execute("SELECT count(*) FROM research_feed_telegram_channels").fetchone()[0] == 1
    sdk.identities["alpha"] = 1
    sdk.access_hash = 900
    sdk.histories[1] = [original(7)]
    assert run(env, sdk)["counts"]["new"] == 1
    assert sdk.calls[-1].peer.access_hash == 900


@pytest.mark.parametrize("kind", ["empty", "missing_date", "service_date", "bare_url", "non_http"])
def test_known_unrepresentable_stalls_repeatedly_but_other_channel_commits(env, kind):
    sdk = SDK(env)
    sdk.histories[1] = [original(1)]
    assert run(env, sdk)["counts"]["new"] == 1
    bad = original(3)
    if kind == "empty":
        bad = types.MessageEmpty(3, types.PeerChannel(1))
    elif kind == "missing_date":
        bad.date = None
    elif kind == "service_date":
        bad = types.MessageService(id=3, peer_id=types.PeerChannel(1), date=None, action=types.MessageActionEmpty())
    else:
        bad.entities = [types.MessageEntityTextUrl(0, 2, "bare.test" if kind == "bare_url" else "tg://fixture")]
    sdk.histories[1].extend([original(2), bad])
    sdk.histories[2] = [original(7, 2)]
    for attempt in range(2):
        result = run(env, sdk, ["alpha", "beta"])
        assert [row["status"] for row in result["channels"]] == ["invalid_page", "committed"]
        assert result["counts"]["fetched"] == int(attempt == 0)
        assert state(env)["confirmed_message_id"] == 1
        assert {row["post"].source_id for row in rows(env)} == {f"telegram:{A}:1", f"telegram:{B}:7"}
    requests = [r.offset_id for r in sdk.calls if isinstance(r, functions.messages.GetHistoryRequest)
                and r.peer.channel_id == 1]
    assert requests == [0, 2, 2]


@pytest.mark.parametrize("failure", ["bad_peer", "bad_date", "cancel"])
def test_loud_second_page_keeps_first_commit_and_binding_no_partial(env, failure):
    sdk = SDK(env)
    sdk.histories = {1: [original(1)], 2: [original(2, 2)]}
    if failure == "bad_peer":
        sdk.histories[2][0].peer_id = types.PeerChannel(1)
    elif failure == "bad_date":
        sdk.histories[2][0].date = False
    else:
        sdk.failures[("fetch", 2)] = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else ValueError):
        run(env, sdk, ["alpha", "beta"])
    assert state(env)["confirmed_message_id"] == 1
    assert state(env, "beta")["initial_window_committed"] is False
    assert [row["post"].source_id for row in rows(env)] == [f"telegram:{A}:1"]


@pytest.mark.parametrize("phase", ["resolve", "fetch"])
def test_actual_flood_wait_fresh_failure_time_durable_reopen_and_exact_deadline(env, phase):
    sdk = SDK(env)
    error = rpcerrorlist.FloodWaitError(functions.contacts.ResolveUsernameRequest("private_fixture"), capture=10)
    sdk.failures[(phase, "alpha" if phase == "resolve" else 1)] = error

    def advance(actual, identity):
        if actual == phase:
            env.now = NOW + timedelta(seconds=7)

    sdk.hook = advance
    result = run(env, sdk, ["alpha", "beta"])
    deadline = (NOW + timedelta(seconds=17)).isoformat()
    assert result["account"] == {"status": "deferred", "retry_at": deadline}
    assert [row["status"] for row in result["channels"]] == ["flood_wait", "not_attempted"]
    assert retry_at(env) == deadline
    before = len(sdk.calls)
    assert run(env, sdk)["counts"]["fetched"] == 0 and len(sdk.calls) == before
    sdk.hook = None
    env.now = NOW + timedelta(seconds=17)
    assert run(env, sdk)["channels"][0]["status"] == "committed"
    assert retry_at(env) == deadline
    assert "private_fixture" not in json.dumps(result)


@pytest.mark.parametrize("boundary", ["before_fetch", "before_next_resolve", "flood_max_later"])
def test_real_second_connection_retry_admission_and_max_later(env, boundary):
    sdk = SDK(env)
    later = NOW + timedelta(hours=1)
    if boundary == "flood_max_later":
        sdk.failures[("resolve", "alpha")] = rpcerrorlist.FloodWaitError(
            functions.contacts.ResolveUsernameRequest("fixture"), capture=10)

    def install(phase, identity):
        target = "fetch" if boundary == "before_next_resolve" else "resolve"
        if phase == target:
            defer(env, later)

    sdk.hook = install
    result = run(env, sdk, ["alpha", "beta"])
    assert result["account"] == {"status": "deferred", "retry_at": later.isoformat()}
    assert retry_at(env) == later.isoformat()
    assert len(sdk.calls) == (2 if boundary == "before_next_resolve" else 1)
    assert state(env, "beta") is None


def test_saved_live_overlap_hash_reuse_and_edit_invalidation_real_store(env):
    seed(env)
    saved = [TelegramPost(schema_version=1, channel_id=A, message_id=n, username="alpha",
                         permalink=f"https://t.me/alpha/{n}", text=original(n).message,
                         published_at=NOW.isoformat()) for n in (2, 9)]
    with closing(env.open(env.settings.triage_db_path)) as conn:
        with conn:
            for post in saved:
                store.upsert_post(conn, post)
            for row in store.list_posts(conn, limit=10):
                store.save_evaluation(conn, row, {"summary": "saved"})
        assert store.read_channel_state(conn, username="alpha") is None
    before = rows(env)
    sdk = SDK(env)
    sdk.histories[1] = [original(2), original(9, message="  Исправленная 🧬 caption\n", edit_date=NOW)]
    result = run(env, sdk)
    assert result["counts"] == dict(fetched=2, new=0, updated=1, unchanged=1, skipped=0)
    current = rows(env)
    assert current[0] == before[0]
    assert current[1]["content_hash"] != before[1]["content_hash"]
    assert current[1]["evaluation"] is None
    assert current[1]["post"].text == sdk.histories[1][1].message
    with closing(env.open(env.settings.triage_db_path)) as conn:
        with conn:
            assert not store.save_evaluation(conn, before[1], {"summary": "stale"})


def test_stored_skip_counts_and_only_selected_ingestion_paths(env, monkeypatch):
    sdk = SDK(env)
    sdk.histories[1] = [original(1, message="", media=types.MessageMediaUnsupported()),
        types.MessageService(id=2, peer_id=types.PeerChannel(1), date=NOW, action=types.MessageActionEmpty()),
        original(3, message=" "), original(4, pinned=True)]
    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith(("zotero_summarizer.services.model", "zotero_summarizer.services.llm",
                            "zotero_summarizer.services.lifecycle")):
            raise AssertionError("collection activated a gate/startup")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    result = run(env, sdk)
    assert result["counts"] == dict(fetched=4, new=4, updated=0, unchanged=0, skipped=3)
    assert [row["post"].skip_reason for row in rows(env)] == ["media_only", "service_message", "empty_message", None]
    settings = env.settings
    assert all(path.name.startswith("triage_history.db") for path in settings.data_dir.iterdir())
    assert not settings.config_path.exists() and not settings.model_dir.exists()
    assert not settings.corpus_db_path.exists() and not settings.interaction_log_path.exists()
    assert not settings.golden_csv_path.exists() and not settings.golden_jsonl_path.exists()
    with closing(env.open(settings.triage_db_path)) as conn:
        for table in ("rss_items", "triage_results", "label_verdicts", "pending_changes", "user_feedback"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize("phase", ["resolve", "fetch"])
@pytest.mark.parametrize("error_type,code", [
    (rpcerrorlist.SessionRevokedError, "revoked_session"),
    (rpcerrorlist.AuthKeyDuplicatedError, "session_unauthorized"),
    (rpcerrorlist.AuthKeyUnregisteredError, "session_unauthorized")])
def test_actual_account_errors_stop_remaining_requests_without_raw_strings(env, phase, error_type, code):
    sdk = SDK(env)
    error = error_type(functions.contacts.ResolveUsernameRequest("private_fixture"))
    error.args = ("private_fixture",)
    sdk.failures[(phase, "alpha" if phase == "resolve" else 1)] = error
    result = run(env, sdk, ["alpha", "beta"])
    assert result["account"] == {"status": code, "retry_at": None}
    assert [row["status"] for row in result["channels"]] == [code, "not_attempted"]
    assert len(sdk.calls) == (1 if phase == "resolve" else 2)
    assert rows(env) == [] and state(env, "beta") is None
    assert "private_fixture" not in json.dumps(result)


@pytest.mark.parametrize("phase", ["resolve", "fetch"])
def test_malformed_actual_flood_cannot_write_durable_deferral(env, phase):
    sdk = SDK(env)
    error = rpcerrorlist.FloodWaitError(functions.contacts.ResolveUsernameRequest("fixture"), capture=10)
    error.seconds = True
    sdk.failures[(phase, "alpha" if phase == "resolve" else 1)] = error
    with pytest.raises(ValueError, match="^invalid Telegram retry seconds$"):
        run(env, sdk, ["alpha", "beta"])
    assert retry_at(env) is None and rows(env) == []
    assert len(sdk.calls) == (1 if phase == "resolve" else 2)


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("mode", ["native", "replacement", "hash"])
def test_route_change_after_prior_commit_cannot_relabel_originals_or_advance_cursor(env, empty, mode):
    sdk = SDK(env)
    sdk.histories = {1: [original(1)], 2: [original(1, 2)]}
    assert run(env, sdk, ["alpha", "beta"])["counts"]["new"] == 2
    query = ("SELECT channel_id, message_id, post_json, content_hash, evaluation_json "
             "FROM research_feed_telegram_posts ORDER BY channel_id, message_id")
    with closing(env.open(env.settings.triage_db_path)) as conn:
        before = [tuple(row) for row in conn.execute(query)]
    prior_beta = state(env, "beta")
    sdk.histories[1].append(original(2))
    sdk.histories[2].append(original(2, 2))
    earlier_calls = len(sdk.calls)

    async def tampering_sdk(request):
        result = await sdk(request)
        if isinstance(request, functions.messages.GetHistoryRequest) and request.peer.channel_id == 2:
            native, access_hash = request.peer.channel_id, request.peer.access_hash
            returned_native = native
            if mode == "native":
                request.peer.channel_id = returned_native = native + 1
            elif mode == "replacement":
                request.peer = types.InputPeerChannel(native + 1, access_hash)
                returned_native = native + 1
            else:
                request.peer.access_hash = access_hash + 1
            posts = [] if empty else [original(2, returned_native)]
            return types.messages.Messages(posts, [], [], [])
        return result

    with pytest.raises(ValueError):
        run(env, tampering_sdk, ["alpha", "beta", "alias"])
    assert len(sdk.calls) == earlier_calls + 4
    assert state(env)["confirmed_message_id"] == 2
    assert state(env, "beta") == prior_beta
    assert state(env, "alias") is None
    with closing(env.open(env.settings.triage_db_path)) as conn:
        after = [tuple(row) for row in conn.execute(query)]
    assert len(after) == len(before) + 1
    assert [row for row in after if row[:2] != (A, 2)] == before
    assert {(row["post"].channel_id, row["post"].message_id) for row in rows(env)} == {
        (A, 1), (A, 2), (B, 1),
    }
