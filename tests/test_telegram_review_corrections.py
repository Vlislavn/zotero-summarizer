"""Review-discriminating controls over synthetic TL responses and immutable bundles."""
from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import Mock
from datetime import datetime, timedelta, timezone

import pytest
from telethon.tl import types

from tests.test_telegram_adapter import ScriptSDK, channel, history, message, resolved, NOW
from tests.test_research_feed_telegram import _project
from tests.test_telegram_conversations import snapshot, response
from zotero_summarizer.models import GoalsConfig
from zotero_summarizer.models.providers import ProviderConfig
from zotero_summarizer.models.research_feed import TelegramModelSelection, TelegramTransportError
from zotero_summarizer.models.telegram_discussion import TelegramTarget, TelegramLimits, TelegramConversation, digest
from zotero_summarizer.integrations.telegram_threads import TelethonConversationClient
from zotero_summarizer.services.research_feed import telegram_collect, telegram_conversations, telegram_review, reports
from zotero_summarizer.services.research_feed.profile import load_profile
from zotero_summarizer.storage import repositories
from zotero_summarizer.storage.research_feed_telegram import read_retry_at, defer_account


def discussion(post, root=50):
    group = channel(native=2, username="group", broadcast=False, megagroup=True)
    original = message(root, native=2, fwd_from=types.MessageFwdHeader(
        date=NOW, from_id=types.PeerChannel(1), channel_post=post))
    return types.messages.DiscussionMessage(messages=[original], unread_count=0, chats=[group], users=[])


class AuthorizedSDK(ScriptSDK):
    async def is_user_authorized(self):
        return True


@pytest.mark.parametrize("phase", ["link", "replies"])
@pytest.mark.parametrize("failure", ["flood_wait", "session_unauthorized", "revoked_session", "admission", "validation"])
def test_later_post_stop_preserves_completed_origins_and_remaining_coverage(tmp_path, monkeypatch, failure, phase):
    settings = _project(tmp_path)
    profile = load_profile(settings.data_dir)
    profile.telegram_targets = [TelegramTarget(kind="broadcast", username=name) for name in ["alpha", "beta"]]
    invalid = history([]) if phase == "link" else history([message(61, native=3)])
    last = (invalid if failure == "validation" else
            TelegramTransportError("flood_wait", retry_seconds=20) if failure in {"flood_wait", "admission"} else
            TelegramTransportError(failure))
    script = [resolved(), history([message(9), message(8), message(7)]),
              discussion(7), history([message(51, native=2, reply_to=types.MessageReplyHeader(reply_to_msg_id=50))])]
    if phase == "replies":
        script.append(discussion(8, root=60))
    sdk = AuthorizedSDK(*script, last)
    if failure == "admission":
        original = telegram_collect._admit_request
        calls = []
        def admit(conn, now, account):
            calls.append(True)
            if len(calls) == (5 if phase == "link" else 6):
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    defer_account(conn, retry_at=NOW + timedelta(seconds=20))
            return original(conn, now, account)
        monkeypatch.setattr(telegram_collect, "_admit_request", admit)

    result = asyncio.run(telegram_conversations.collect(settings, sdk, profile, now=lambda: NOW))
    values = result["conversations"]
    assert [row["origin"]["message_id"] for row in values[:3]] == [7, 8, 9]
    assert values[0]["status"] == "fetched" and values[0]["messages"][0]["message_id"] == 51
    assert values[1]["status"] == values[2]["status"] == "unavailable"
    assert values[1]["attempted"] is True and values[2]["attempted"] is False
    assert values[3]["target"]["username"] == "beta" and values[3]["attempted"] is False
    assert len(sdk.calls) == (4 if failure == "admission" else 5) + (phase == "replies")
    if phase == "replies":
        assert values[1]["peer_id"] == -1000000000002 and values[1]["root_id"] == 60
        assert values[1]["root"]["message_id"] == 60
    if failure in {"flood_wait", "admission"}:
        assert result["account"]["status"] == "deferred"
        with repositories._connect_to(settings.triage_db_path) as conn:
            assert read_retry_at(conn) == result["account"]["retry_at"]
    if failure in {"session_unauthorized", "revoked_session"}:
        assert result["authorized"] is False and result["account"]["status"] == failure
    with repositories._connect_to(settings.triage_db_path) as conn:
        saved = [json.loads(row[0]) for row in conn.execute("SELECT payload_json FROM research_feed_conversations")]
    assert any(row.get("origin", {}).get("message_id") == 7 and row["messages"] for row in saved if row.get("origin"))
    assert {row["origin"]["message_id"] for row in saved if row.get("origin")} == {7, 8, 9}


def test_broadcast_window_records_lookahead_separately_from_observed_empty_replies(tmp_path):
    sdk = ScriptSDK(resolved(), history([message(8), message(7)]),
                    types.messages.DiscussionMessage(messages=[], unread_count=0, chats=[], users=[]))
    values = asyncio.run(TelethonConversationClient(sdk).collect(
        TelegramTarget(kind="broadcast", username="alpha"), TelegramLimits(posts_per_channel=1)))
    assert values[0].status == "empty"
    window = values[0].post_window.model_dump()
    assert window == {"limit": 1, "scanned": 2, "retained": 1, "lookahead": 1, "truncated": True}
    settings = _project(tmp_path)
    result = telegram_conversations.publish(settings, values, collection={}, profile=load_profile(settings.data_dir))
    payload = reports.get_report(settings, result["run_id"])
    assert payload["metadata"]["status"] == "partial"
    assert payload["conversations"][0]["post_window"] == window
    assert "Post window" in reports.download_bytes(settings, result["run_id"], "md").decode()


def local_context(tmp_path):
    settings = _project(tmp_path)
    config = GoalsConfig.model_validate(json.loads(settings.config_path.read_text()))
    config.llm_routing.providers.append(ProviderConfig(name="local", base_url="http://127.0.0.1:11434/v1", api_key_env="LOCAL"))
    profile = load_profile(settings.data_dir)
    profile.telegram_model = TelegramModelSelection(provider="local", model="synthetic")
    profile.telegram_limits = TelegramLimits(context_tokens=32768)
    return config, profile


def test_group_prompt_preserves_peer_qualified_nested_edges_and_unresolved_context(tmp_path):
    config, profile = local_context(tmp_path)
    group = channel(native=2, username="group", broadcast=False, megagroup=True, forum=True)
    raw = [message(56, native=2, reply_to=types.MessageReplyHeader(reply_to_msg_id=51, reply_to_peer_id=types.PeerUser(9876543213))),
           message(55, native=2, reply_to=types.MessageReplyHeader(reply_to_msg_id=51, reply_to_peer_id=types.PeerChannel(3))),
           message(54, native=2, reply_to=types.MessageReplyHeader(reply_to_msg_id=53, reply_to_top_id=40)),
           message(53, native=2, reply_to=types.MessageReplyHeader(reply_to_msg_id=51, reply_to_top_id=50)),
           message(51, native=2)]
    batch = asyncio.run(TelethonConversationClient(ScriptSDK(resolved(group), history(raw))).collect(
        TelegramTarget(kind="group", username="group"), TelegramLimits()))[0]
    other = snapshot(peer=-1000000000003)
    other.messages[0].reply_peer_is_current = True
    _route, prompt, _identity = telegram_review.prepare(config, profile, [batch, other])
    records = json.loads(prompt.split("Original source records: <untrusted_input>")[1].split("</untrusted_input>")[0])
    sources = {row["source_id"]: row for row in records}
    nested = sources["telegram:-1000000000002:53"]["reply_context"]
    assert nested["parent"] == {"source_id": "telegram:-1000000000002:51", "status": "retained"}
    assert nested["top"] == {"source_id": "telegram:-1000000000002:50", "status": "unresolved_out_of_window"}
    assert sources["telegram:-1000000000002:54"]["reply_context"]["top"]["source_id"] == "telegram:-1000000000002:40"
    assert sources["telegram:-1000000000003:51"]["reply_context"]["parent"] == {
        "source_id": "telegram:-1000000000003:50", "status": "unresolved_out_of_window"}
    for number in (55, 56):
        assert sources[f"telegram:-1000000000002:{number}"]["reply_context"]["parent"] == {
            "message_id": 51, "status": "unresolved_peer"}
    assert "9876543213" not in prompt
    assert "sender" not in prompt and "access_hash" not in prompt and "published_at" not in prompt


def test_unauthorized_partial_export_is_verified_and_readable_without_generation(tmp_path, monkeypatch):
    import subprocess
    from pathlib import Path
    from zotero_summarizer.cli import _telegram
    from zotero_summarizer.services.llm import factory
    settings = _project(tmp_path)
    profile = load_profile(settings.data_dir)
    result = {"status": "revoked_session", "cleanup": "completed", "account": {"status": "revoked_session"},
              "collection": {"authorized": False, "conversations": [snapshot().model_dump(mode="json")]}}
    exported = telegram_conversations.publish(settings, [snapshot()], collection=result, profile=profile)
    recorded = _telegram._receipt(settings, "conversations", result, exported)
    def child(command, **_options):
        path = Path(command[command.index("--receipt") + 1])
        path.write_text(json.dumps(recorded))
        return subprocess.CompletedProcess(command, 1)
    monkeypatch.setattr(_telegram.subprocess, "run", child)
    build = Mock(side_effect=AssertionError("partial failure cannot start a model"))
    monkeypatch.setattr(factory, "build_client_for_provider", build)
    with pytest.raises(_telegram.TelegramChildFailed) as caught:
        _telegram.run_child(settings, "conversations")
    assert caught.value.code == "revoked_session"
    assert caught.value.collection_report_id == exported["run_id"]
    assert reports.get_report(settings, caught.value.collection_report_id)["conversations"][0]["messages"]
    build.assert_not_called()
    Path(exported["inputs_path"]).write_text("changed")
    with pytest.raises(RuntimeError, match="verification"):
        _telegram.run_child(settings, "conversations")


def test_absent_new_coverage_fields_preserve_historical_v2_content_hash():
    original = snapshot().model_dump(mode="json")
    original.pop("post_window", None); original.pop("attempted", None)
    for row in original["messages"]:
        row.pop("reply_peer_is_current", None)
    expected = digest({key: value for key, value in original.items() if key != "observed_at"})
    assert TelegramConversation.model_validate(original).content_hash == expected


def test_weekly_chronology_merges_sources_before_limit_and_keeps_unavailable_bytes(tmp_path):
    settings = _project(tmp_path)
    root = settings.data_dir / "research_feed"
    originals = {}
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(101):
        slug = f"weekly-z-{100-i:03d}"
        bundle = root / slug; bundle.mkdir()
        data = {"metadata": {"schema_version": 1, "source": "app_rss", "from": "2026-01-01", "to": "2026-01-02",
                 "generated_at": (start + timedelta(days=i)).isoformat(),
                 "counts": {"discovered": 0, "shortlisted": 0, "cards_generated": 0}},
                "cards": [], "rejected_near_threshold": [], "manual_full_text_required": [], "failed": []}
        path = bundle / f"{slug}.json"; path.write_text(json.dumps(data)); originals[path] = path.read_bytes()
    for slug, day in [("telegram-review-0000", 150), ("saved-news-ffff", 130)]:
        bundle = root / "telegram" / slug; bundle.mkdir(parents=True)
        data = {"metadata": {"schema_version": 1, "source": "telegram", "generated_at": (start + timedelta(days=day)).isoformat(),
                            "counts": {"cards_generated": 0}}, "posts": [], "cards": [], "uncertain": []}
        path = bundle / f"{slug}.json"; path.write_text(json.dumps(data)); originals[path] = path.read_bytes()
    broken = root / "weekly-a-unavailable"; broken.mkdir()
    path = broken / "weekly-a-unavailable.json"; path.write_text("broken"); originals[path] = path.read_bytes()
    os.utime(broken, (start.timestamp() + 200*86400, start.timestamp() + 200*86400))
    listing = reports.list_reports(settings, limit=100)["items"]
    assert [row["run_id"] for row in listing[:3]] == [broken.name, "telegram-review-0000", "saved-news-ffff"]
    assert listing[0]["available"] is False and len(listing) == 100
    assert listing[3]["run_id"] == "weekly-z-000"
    assert {file: file.read_bytes() for file in originals} == originals
