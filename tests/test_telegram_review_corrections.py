"""Review-discriminating controls over synthetic TL responses and immutable bundles."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

from telethon.tl import types

from tests.test_telegram_adapter import ScriptSDK, history, message, resolved
from tests.test_research_feed_telegram import _project
from tests.test_telegram_conversations import snapshot
from zotero_summarizer.models import GoalsConfig
from zotero_summarizer.models.providers import ProviderConfig
from zotero_summarizer.models.research_feed import TelegramModelSelection
from zotero_summarizer.models.telegram_discussion import TelegramTarget, TelegramLimits, TelegramConversation, digest
from zotero_summarizer.integrations.telegram_threads import TelethonConversationClient
from zotero_summarizer.services.research_feed import reports
from zotero_summarizer.services.research_feed.profile import load_profile


def test_broadcast_window_records_lookahead_separately_from_observed_empty_replies(tmp_path):
    sdk = ScriptSDK(resolved(), history([message(8), message(7)]),
                    types.messages.DiscussionMessage(messages=[], unread_count=0, chats=[], users=[]))
    values = asyncio.run(TelethonConversationClient(sdk).collect(
        TelegramTarget(kind="broadcast", username="alpha"), TelegramLimits(posts_per_channel=1)))
    assert values[0].status == "empty"
    window = values[0].post_window.model_dump()
    assert window == {"limit": 1, "scanned": 2, "retained": 1, "lookahead": 1, "truncated": True}


def local_context(tmp_path):
    settings = _project(tmp_path)
    config = GoalsConfig.model_validate(json.loads(settings.config_path.read_text()))
    config.llm_routing.providers.append(ProviderConfig(name="local", base_url="http://127.0.0.1:11434/v1", api_key_env="LOCAL"))
    profile = load_profile(settings.data_dir)
    profile.telegram_model = TelegramModelSelection(provider="local", model="synthetic")
    profile.telegram_limits = TelegramLimits(context_tokens=32768)
    return config, profile


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
    # Retired v1 telegram reports no longer validate; they remain discoverable via mtime, available=False.
    for slug, day in [("telegram-review-0000", 150), ("saved-news-ffff", 130)]:
        bundle = root / "telegram" / slug; bundle.mkdir(parents=True)
        data = {"metadata": {"schema_version": 1, "source": "telegram", "generated_at": (start + timedelta(days=day)).isoformat(),
                            "counts": {"cards_generated": 0}}, "posts": [], "cards": [], "uncertain": []}
        path = bundle / f"{slug}.json"; path.write_text(json.dumps(data)); originals[path] = path.read_bytes()
        os.utime(bundle, (start.timestamp() + day*86400, start.timestamp() + day*86400))
    broken = root / "weekly-a-unavailable"; broken.mkdir()
    path = broken / "weekly-a-unavailable.json"; path.write_text("broken"); originals[path] = path.read_bytes()
    os.utime(broken, (start.timestamp() + 200*86400, start.timestamp() + 200*86400))
    listing = reports.list_reports(settings, limit=100)["items"]
    assert [row["run_id"] for row in listing[:3]] == [broken.name, "telegram-review-0000", "saved-news-ffff"]
    assert listing[0]["available"] is False and len(listing) == 100
    assert listing[3]["run_id"] == "weekly-z-000"
    assert {file: file.read_bytes() for file in originals} == originals
