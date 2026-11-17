"""«Load discussions»: flood-wait is checked before every request; a repeat press never duplicates rows. Stub SDK only."""
from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("telethon")
from telethon.errors import rpcerrorlist
from telethon.tl import functions, types

from tests.test_research_feed_telegram import _project
from tests.test_telegram_adapter import NOW, ScriptSDK, channel, history, message, resolved
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models.research_feed import TelegramPost
from zotero_summarizer.services.research_feed import (
    telegram_collect, telegram_discussions, telegram_job, telegram_live, telegram_processing, telegram_today)
from zotero_summarizer.storage import repositories, research_feed_conversations as conversations
from zotero_summarizer.storage import research_feed_telegram as store

CHANNEL = -1000000000001
CLOCK = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
FIRST, SECOND = 7, 8  # stored replies: FIRST has more, so it is read first


def _post(number, replies):
    return TelegramPost(schema_version=2, channel_id=CHANNEL, message_id=number, username="alpha",
                        permalink=f"https://t.me/alpha/{number}", text=f"post {number}",
                        published_at="2026-10-07T00:00:00Z", replies_count=replies)


@pytest.fixture
def settings(tmp_path):
    value = _project(tmp_path)
    with closing(repositories._connect_to(value.triage_db_path)) as conn, conn:
        for number, replies in [(FIRST, 5), (SECOND, 3), (9, 0)]:
            store.upsert_post(conn, _post(number, replies))
    return value


def _thread(post, root, *replies):
    """SDK answers for one post: the post itself, its discussion root, then the comment page."""
    group = channel(native=2, username="group", broadcast=False, megagroup=True)
    forward = types.MessageFwdHeader(date=NOW, from_id=types.PeerChannel(1), channel_post=post)
    discussion = types.messages.DiscussionMessage(
        messages=[message(root, native=2, fwd_from=forward)], max_id=root + 10, read_inbox_max_id=0,
        read_outbox_max_id=0, unread_count=0, chats=[group], users=[])
    comments = [message(number, native=2, message=f"comment {number}",
                        reply_to=types.MessageReplyHeader(reply_to_msg_id=root)) for number in replies]
    return [history([message(post)]), discussion, history(list(reversed(comments)))]


def _admit(settings):
    def admit():
        with closing(repositories._connect_to(settings.triage_db_path)) as conn:
            return telegram_collect._admit_request(conn, lambda: CLOCK, {})
    return admit


def _press(settings, sdk, ids=("telegram:%d:%d" % (CHANNEL, FIRST), "telegram:%d:%d" % (CHANNEL, SECOND))):
    return asyncio.run(telegram_discussions.collect(settings, sdk, list(ids), admit=_admit(settings), now=lambda: CLOCK))


def _rows(settings):
    with closing(repositories._connect_to(settings.triage_db_path)) as conn:
        return conn.execute("SELECT count(*) FROM research_feed_conversations").fetchone()[0]


def _full_script(*extra):
    return ScriptSDK(resolved(), *_thread(FIRST, 50, 51, 52), *_thread(SECOND, 60, 61), *extra)


def test_stored_flood_wait_means_no_request_at_all(settings):
    with closing(repositories._connect_to(settings.triage_db_path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        store.defer_account(conn, retry_at=CLOCK + timedelta(minutes=30))
    sdk = ScriptSDK()
    receipt = _press(settings, sdk)
    assert sdk.calls == [] and receipt["loaded"] == 0 and _rows(settings) == 0


def test_flood_wait_during_the_press_stops_the_next_request_and_blocks_the_next_press(settings):
    wait = rpcerrorlist.FloodWaitError(functions.contacts.ResolveUsernameRequest("fixture"), capture=600)
    sdk = ScriptSDK(resolved(), *_thread(FIRST, 50, 51)[:2], wait)  # second request of the thread page fails
    receipt = _press(settings, sdk)
    requests = len(sdk.calls)
    assert receipt["loaded"] == 0 and receipt["account"]["status"] == "deferred"
    with closing(repositories._connect_to(settings.triage_db_path)) as conn:
        assert datetime.fromisoformat(store.read_retry_at(conn)) == CLOCK + timedelta(seconds=600)
    again = ScriptSDK()
    _press(settings, again)
    assert requests == 4 and again.calls == []  # nothing for the second post, nothing at all on the next press


def test_repeat_press_adds_no_rows_and_new_comments_add_one_snapshot(settings):
    first = _press(settings, _full_script())
    assert first["loaded"] == 2 and first["comments"] == 3
    rows = _rows(settings)
    assert rows == 2
    _press(settings, _full_script())
    assert _rows(settings) == rows  # unchanged threads: INSERT OR IGNORE on the content hash
    grown = ScriptSDK(resolved(), *_thread(FIRST, 50, 51, 52, 53), *_thread(SECOND, 60, 61))
    _press(settings, grown)
    assert _rows(settings) == rows + 1
    with closing(repositories._connect_to(settings.triage_db_path)) as conn:
        merged = conversations.latest_messages(conn, "telegram:%d:%d" % (CHANNEL, FIRST))
    assert sorted(m["message_id"] for m in merged) == [51, 52, 53]


def test_server_refuses_during_flood_wait_before_starting_any_child(settings, monkeypatch):
    telegram_processing.set_collect_consent(settings, True)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    with closing(repositories._connect_to(settings.triage_db_path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        store.defer_account(conn, retry_at=future)
    topic = {"id": 1, "source_ids": ["telegram:%d:%d" % (CHANNEL, FIRST)]}
    monkeypatch.setattr(telegram_today, "read_today", lambda _settings: {"trends": {"topics": [topic]}})
    monkeypatch.setattr(telegram_live, "load_discussions",
                        lambda *_: pytest.fail("a Telegram child must not start inside the wait window"))
    with pytest.raises(APIError) as refused:
        telegram_job.start_discussions(settings, 1)
    assert refused.value.error == "deferred" and refused.value.details["retry_at"]
