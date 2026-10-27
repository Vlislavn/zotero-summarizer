"""Synthetic Telegram conversation contracts; no account or model execution."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from unittest.mock import Mock

import pytest

from tests.test_telegram_adapter import ScriptSDK, channel, history, message, resolved, NOW
from tests.test_research_feed import _settings
from zotero_summarizer.models.telegram_discussion import (
    TelegramTarget, TelegramLimits, TelegramConversation, TelegramMessage,
)
from zotero_summarizer.integrations.telegram_threads import TelethonConversationClient
from zotero_summarizer.services.research_feed import telegram_review
from zotero_summarizer.services.research_feed.profile import load_profile
from zotero_summarizer.storage import migrations, repositories, research_feed_conversations as store
from telethon.tl import functions, types


def snapshot(text="A participant claim", status="fetched", peer=-1000000000002):
    return TelegramConversation(
        target=TelegramTarget(kind="topic", username="group", message_id=50),
        peer_id=peer, root_id=50, status=status, observed_at=NOW.isoformat(),
        messages=[TelegramMessage(peer_id=peer, message_id=51, thread_id=50,
            reply_to_id=50, text=text, published_at=NOW.isoformat(),
            permalink="https://t.me/group/51")],
    )


def response():
    citation = {"source_id": "telegram:-1000000000002:51", "quote": "participant claim"}
    claim = {"text": "Participants raise a claim, not independently verified.", "citations": [citation]}
    return {"topics": [{"title": "Claim evaluation", "source_ids": [citation["source_id"]],
        "context": claim, "news": [], "discussion": [claim], "contrasts": [],
        "evidence": [], "caveats": [claim], "implications": []}]}


def generated_response(evidence_id="e1"):
    value = deepcopy(response())
    for topic in value["topics"]:
        del topic["source_ids"]
        for name in ("context", "news", "discussion", "contrasts", "evidence", "caveats", "implications"):
            for claim in [topic[name]] if name == "context" else topic[name]:
                claim["citations"] = [{"evidence_id": evidence_id}]
    return value


def test_target_normalizes_public_links_and_rejects_private_or_wrong_scope():
    assert TelegramTarget(kind="broadcast", username="https://t.me/Alpha").username == "alpha"
    assert TelegramTarget(kind="topic", username="https://t.me/Group/50").message_id == 50
    for values in [dict(kind="group", username="me"), dict(kind="topic", username="group"),
                   dict(kind="broadcast", username="https://t.me/+invite"),
                   dict(kind="group", username="https://t.me/c/123/5")]:
        with pytest.raises(ValueError):
            TelegramTarget(**values)


@pytest.mark.parametrize("forward_mode", ["direct", "saved_from", "wrong", "incomplete"])
def test_linked_discussion_routes_resolved_peer_and_root_not_post_id(forward_mode):
    group = channel(native=2, username="group", broadcast=False, megagroup=True)
    forward = {"date": NOW, "from_id": types.PeerChannel(1), "channel_post": 7}
    if forward_mode != "direct":
        forward.update(from_id=types.PeerChannel(9), channel_post=None,
                       saved_from_peer=types.PeerChannel(1 if forward_mode != "wrong" else 8),
                       saved_from_msg_id=None if forward_mode == "incomplete" else 7)
    root = message(50, native=2, fwd_from=types.MessageFwdHeader(**forward))
    discussion = types.messages.DiscussionMessage(messages=[root], max_id=51,
        read_inbox_max_id=0, read_outbox_max_id=0, unread_count=0, chats=[group], users=[])
    reply = message(51, native=2, reply_to=types.MessageReplyHeader(reply_to_msg_id=50))
    sdk = ScriptSDK(resolved(), history([message(7)]), discussion, history([reply]))
    leaf = TelethonConversationClient(sdk)
    if forward_mode in {"wrong", "incomplete"}:
        with pytest.raises(ValueError, match="root"):
            asyncio.run(leaf.collect(TelegramTarget(kind="broadcast", username="alpha"), TelegramLimits()))
        return
    batches = asyncio.run(leaf.collect(TelegramTarget(kind="broadcast", username="alpha"), TelegramLimits()))
    batch = batches[0]
    assert batch.origin.message_id == 7
    assert (batch.peer_id, batch.root_id) == (-1000000000002, 50)
    assert batch.messages[0].source_id == "telegram:-1000000000002:51"
    assert batch.messages[0].thread_id == 50
    request = sdk.calls[-1]
    assert isinstance(request, functions.messages.GetRepliesRequest)
    assert (request.peer.channel_id, request.msg_id) == (2, 50)
    assert bytes(request)
    assert [type(call) for call in sdk.calls] == [functions.contacts.ResolveUsernameRequest,
        functions.messages.GetHistoryRequest, functions.messages.GetDiscussionMessageRequest,
        functions.messages.GetRepliesRequest]


@pytest.mark.parametrize("mode,expected", [("empty", "empty"), ("unavailable", "unavailable"), ("capped", "truncated")])
def test_topic_scope_and_coverage_not_confused(mode, expected):
    group = channel(native=2, username="group", broadcast=False, megagroup=True, forum=True)
    root = message(50, native=2)
    replies = [message(52, native=2, reply_to=types.MessageReplyHeader(reply_to_msg_id=50)),
               message(51, native=2, reply_to=types.MessageReplyHeader(reply_to_msg_id=50))]
    from zotero_summarizer.models.research_feed import TelegramTransportError
    result = (TelegramTransportError("channel_unavailable") if mode == "unavailable"
              else history(replies if mode == "capped" else []))
    sdk = ScriptSDK(resolved(group), history([root]), result)
    batches = asyncio.run(TelethonConversationClient(sdk).collect(
        TelegramTarget(kind="topic", username="group", message_id=50), TelegramLimits(replies_per_thread=1)))
    assert batches[0].status == expected
    if mode == "capped":
        assert len(batches[0].messages) == 1
        assert batches[0].messages[0].thread_id == 50


def test_wrong_thread_or_mixed_peer_cannot_enter_context():
    group = channel(native=2, username="group", broadcast=False, megagroup=True, forum=True)
    for decoy in [message(51, native=3, reply_to=types.MessageReplyHeader(reply_to_msg_id=50)),
                  message(51, native=2, reply_to=types.MessageReplyHeader(reply_to_msg_id=49))]:
        sdk = ScriptSDK(resolved(group), history([message(50, native=2)]), history([decoy]))
        with pytest.raises(ValueError):
            asyncio.run(TelethonConversationClient(sdk).collect(
                TelegramTarget(kind="topic", username="group", message_id=50), TelegramLimits()))


def test_same_message_numbers_and_edits_have_distinct_hashes_and_immutable_storage(tmp_path):
    settings = _settings(tmp_path)
    migrations.run_migrations(settings.triage_db_path, "triage", migrations.TRIAGE_MIGRATIONS)
    original, edited, other = snapshot(), snapshot("Edited participant claim"), snapshot(peer=-1000000000003)
    assert len({original.content_hash, edited.content_hash, other.content_hash}) == 3
    with repositories._connect_to(settings.triage_db_path) as conn:
        for value in [original, edited, other, original]:
            store.save_snapshot(conn, value)
        assert conn.execute("SELECT count(*) FROM research_feed_conversations").fetchone()[0] == 3
        with pytest.raises(Exception, match="immutable"):
            conn.execute("UPDATE research_feed_conversations SET payload_json='{}'")


def test_citations_are_bound_to_exact_original_not_other_peer_or_thread():
    batch = snapshot()
    valid = telegram_review.validate_topics(response(), [batch])
    assert valid.topics[0].title == "Claim evaluation"
    for key, value in [("source_id", "telegram:-1000000000003:51"), ("quote", "invented")]:
        bad = deepcopy(response())
        bad["topics"][0]["context"]["citations"][0][key] = value
        with pytest.raises(ValueError):
            telegram_review.validate_topics(bad, [batch])
    bad = deepcopy(response()); bad["topics"][0]["source_ids"] = []
    with pytest.raises(ValueError):
        telegram_review.validate_topics(bad, [batch])


def test_remote_default_never_receives_discussion_and_no_implicit_route(tmp_path, monkeypatch):
    from zotero_summarizer.models import GoalsConfig
    from zotero_summarizer.services.llm import factory
    settings = _settings(tmp_path)
    profile = load_profile(settings.data_dir)
    from tests.test_research_feed_telegram import _project
    config = GoalsConfig.model_validate(json.loads(_project(tmp_path).config_path.read_text()))
    build = Mock(side_effect=AssertionError("must refuse before client construction"))
    monkeypatch.setattr(factory, "build_client_for_provider", build)
    with pytest.raises(ValueError, match="local"):
        telegram_review.generate(config, profile, [snapshot()])
    build.assert_not_called()


def test_explicit_local_selection_single_attempt_untrusted_input_and_budget(tmp_path, monkeypatch):
    from zotero_summarizer.models import GoalsConfig
    from zotero_summarizer.models.providers import LLMRoutingConfig, ProviderConfig, DefaultModelConfig
    from zotero_summarizer.models.telegram_discussion import TelegramModelSelection
    from zotero_summarizer.services.llm import factory
    from tests.test_research_feed_telegram import _project
    config = GoalsConfig.model_validate(json.loads(_project(tmp_path).config_path.read_text()))
    config.llm_routing = LLMRoutingConfig(providers=[ProviderConfig(name="local", base_url="http://127.0.0.1:11434/v1", api_key_env="LOCAL")],
        default=DefaultModelConfig(provider="local", model="not-selected"))
    profile = load_profile(_settings(tmp_path).data_dir)
    profile.telegram_model = TelegramModelSelection(provider="local", model="synthetic-model")
    profile.telegram_limits = TelegramLimits(context_tokens=32768)
    client = Mock(); client.pydantic_prompt.return_value = generated_response()
    build = Mock(return_value=client); monkeypatch.setattr(factory, "build_client_for_provider", build)
    review, identity = telegram_review.generate(config, profile, [snapshot()])
    assert review.topics and identity["model"] == "synthetic-model"
    assert identity["source_hashes"] == [snapshot().content_hash]
    assert client.pydantic_prompt.call_count == 1
    prompt = client.pydantic_prompt.call_args.args[0]
    assert "<untrusted_input>" in prompt and "participant claim" in prompt
    assert build.call_args.args[0].num_ctx == profile.telegram_limits.context_tokens
    schema = client.pydantic_prompt.call_args.args[1].model_json_schema()
    required = len(prompt.encode()) + len(json.dumps(schema, ensure_ascii=False).encode()) + profile.telegram_limits.output_tokens + 1024
    boundary = profile.model_copy(deep=True)
    boundary.telegram_limits = boundary.telegram_limits.model_copy(update={"context_tokens": required - 1})
    with pytest.raises(ValueError, match="context budget"):
        telegram_review.generate(config, boundary, [snapshot()])
    assert build.call_count == 1
    profile.telegram_limits = TelegramLimits(prompt_characters=1000)
    with pytest.raises(ValueError, match="budget"):
        telegram_review.generate(config, profile, [snapshot("x" * 1001)])
    assert client.pydantic_prompt.call_count == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_known_linked_news_mirror_is_not_independent_discussion_in_any_group_order(reverse):
    from zotero_summarizer.models.research_feed import TelegramPost
    from zotero_summarizer.models.telegram_discussion import source_records
    root = TelegramMessage(peer_id=-1000000000002, message_id=50, thread_id=50,
                           text="Author news claim", published_at=NOW.isoformat())
    original = TelegramPost(schema_version=1, channel_id=-1000000000001, message_id=7,
                            text="Author news claim", published_at=NOW.isoformat(), permalink="https://t.me/alpha/7")
    linked = snapshot().model_copy(update={"target": TelegramTarget(kind="broadcast", username="alpha"),
                                           "origin": original, "root": root})
    group = TelegramConversation(target=TelegramTarget(kind="group", username="group"), peer_id=root.peer_id,
        messages=[root.model_copy(update={"thread_id": None}), linked.messages[0].model_copy(update={"thread_id": None})],
        status="fetched", observed_at=NOW.isoformat())
    other = TelegramConversation(target=TelegramTarget(kind="group", username="other"), peer_id=-1000000000003,
        messages=[root.model_copy(update={"peer_id": -1000000000003, "thread_id": None})], status="fetched", observed_at=NOW.isoformat())
    batches = [group, linked, other]
    if reverse:
        batches.reverse()
    evidence = source_records(batches)
    assert "telegram:-1000000000002:50" not in evidence
    assert evidence[original.source_id]["role"] == "news"
    assert evidence["telegram:-1000000000002:51"]["role"] == "discussion"
    assert evidence["telegram:-1000000000003:50"]["role"] == "discussion"
    forged = response()
    forged["topics"][0]["source_ids"].append("telegram:-1000000000002:50")
    forged["topics"][0]["discussion"][0]["citations"] = [{"source_id": "telegram:-1000000000002:50", "quote": "Author news claim"}]
    with pytest.raises(ValueError):
        telegram_review.validate_topics(forged, batches)


def test_compact_multi_source_prompt_excludes_redundant_provenance_and_admits_explicit_context(tmp_path):
    from tests.test_research_feed_telegram import _project
    from zotero_summarizer.models import GoalsConfig
    from zotero_summarizer.models.providers import ProviderConfig
    from zotero_summarizer.models.research_feed import TelegramModelSelection
    settings = _project(tmp_path)
    config = GoalsConfig.model_validate(json.loads(settings.config_path.read_text()))
    config.llm_routing.providers.append(ProviderConfig(name="local", base_url="http://127.0.0.1:11434/v1", api_key_env="LOCAL"))
    profile = load_profile(settings.data_dir)
    profile.telegram_model = TelegramModelSelection(provider="local", model="synthetic")
    profile.telegram_limits = TelegramLimits(context_tokens=32768, replies_per_thread=10)
    batches = [snapshot("Discussion about controlled experiments. " * 30, peer=-1000000000000 - i) for i in range(2, 5)]
    _route, prompt, identity = telegram_review.prepare(config, profile, batches)
    assert "published_at" not in prompt and "content_hash" not in prompt and "skip_reason" not in prompt
    assert all(batch.content_hash not in prompt for batch in batches)
    assert identity["source_hashes"] == [batch.content_hash for batch in batches]
    assert "controlled experiments" in prompt and "telegram:-1000000000002:51" in prompt
    assert len(prompt) <= 24000


def test_nested_reply_keeps_thread_root_and_group_overlap_is_one_canonical_evidence_record():
    from zotero_summarizer.models.telegram_discussion import source_records
    group = channel(native=2, username="group", broadcast=False, megagroup=True, forum=True)
    nested = message(52, native=2, reply_to=types.MessageReplyHeader(reply_to_msg_id=51, reply_to_top_id=50))
    topic_sdk = ScriptSDK(resolved(group), history([message(50, native=2)]), history([nested]))
    thread = asyncio.run(TelethonConversationClient(topic_sdk).collect(
        TelegramTarget(kind="topic", username="group", message_id=50), TelegramLimits()))[0]
    group_sdk = ScriptSDK(resolved(group), history([nested]))
    window = asyncio.run(TelethonConversationClient(group_sdk).collect(
        TelegramTarget(kind="group", username="group"), TelegramLimits()))[0]
    evidence = source_records([thread, window])
    assert len(evidence) == 2  # root plus reply, not a third 'corroborating' copy
    reply = evidence["telegram:-1000000000002:52"]
    assert len(reply["contexts"]) == 2
    assert reply["original"]["reply_to_id"] == 51 and reply["original"]["reply_to_top_id"] == 50
    assert thread.messages[0].thread_id == 50


def test_group_nontext_messages_are_retained_with_scanned_skipped_cap_coverage():
    group = channel(native=2, username="group", broadcast=False, megagroup=True)
    sdk = ScriptSDK(resolved(group), history([message(52, native=2, message=""), message(51, native=2)]))
    batch = asyncio.run(TelethonConversationClient(sdk).collect(
        TelegramTarget(kind="group", username="group"), TelegramLimits(replies_per_thread=1)))[0]
    assert batch.status == "truncated" and batch.scanned_count == 2 and batch.skipped_count == 1
    assert len(batch.messages) == 1 and batch.messages[0].skip_reason == "empty_message"


def test_flood_wait_stops_other_sources_and_retains_durable_deadline(tmp_path):
    from tests.test_research_feed_telegram import _project
    from zotero_summarizer.models.research_feed import TelegramTransportError
    from zotero_summarizer.services.research_feed import telegram_conversations
    from zotero_summarizer.storage.research_feed_telegram import read_retry_at
    settings = _project(tmp_path)
    profile = load_profile(settings.data_dir)
    profile.telegram_targets = [TelegramTarget(kind="broadcast", username=name) for name in ["alpha", "beta"]]
    class AuthorizedSDK(ScriptSDK):
        async def is_user_authorized(self):
            return True
    sdk = AuthorizedSDK(TelegramTransportError("flood_wait", retry_seconds=20))
    result = asyncio.run(telegram_conversations.collect(settings, sdk, profile, now=lambda: NOW))
    assert len(sdk.calls) == 1 and result["account"]["status"] == "deferred"
    assert [row["reason"] for row in result["conversations"]] == ["flood_wait", "flood_wait"]
    assert all(row["status"] == "unavailable" for row in result["conversations"])
    with repositories._connect_to(settings.triage_db_path) as conn:
        assert read_retry_at(conn) == result["account"]["retry_at"]


def test_request_admission_is_checked_before_each_scoped_rpc_and_request_mutation_refuses():
    sdk = ScriptSDK(resolved(), history([]))
    checks = iter([True, False])
    leaf = TelethonConversationClient(sdk, before_request=lambda: next(checks))
    with pytest.raises(PermissionError):
        asyncio.run(leaf.collect(TelegramTarget(kind="broadcast", username="alpha"), TelegramLimits()))
    assert len(sdk.calls) == 1
    class MutatingSDK(ScriptSDK):
        async def __call__(self, request):
            value = await super().__call__(request)
            request.username = "other"
            return value
    with pytest.raises(ValueError, match="route changed"):
        asyncio.run(TelethonConversationClient(MutatingSDK(resolved())).collect(
            TelegramTarget(kind="broadcast", username="alpha"), TelegramLimits()))
