from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from zotero_summarizer import runtime
from zotero_summarizer.api.routes import rss as rss_routes
from zotero_summarizer.integrations import app_rss
from zotero_summarizer.integrations.app_rss import AppRssReader
from zotero_summarizer.models import GoalsConfig, SummarizeResponse, TriageResult
from zotero_summarizer.services.triage import summarization
from zotero_summarizer.services.triage.feeds import _tick_phases, _triage as feed_triage
from zotero_summarizer.services.triage.prompts import DEFAULT_PRACTITIONER_TRIAGE_PROMPT
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.storage import rss as rss_storage


HACKERNOON_FEEDS = (
    ("HackerNoon AI Agents", "https://hackernoon.com/tagged/ai-agents/feed"),
    ("HackerNoon Agentic AI", "https://hackernoon.com/tagged/agentic-ai/feed"),
)


@pytest.mark.parametrize(
    ("url", "uses_practitioner_prompt"),
    [
        ("https://hackernoon.com/post", True),
        ("https://www.hackernoon.com/post", True),
        ("https://HackerNoon.com/post", True),
        ("https://evilhackernoon.com/post", False),
        ("https://hackernoon.com.attacker.invalid/post", False),
        ("https://attacker.invalid/?next=https://hackernoon.com/post", False),
    ],
)
def test_practitioner_prompt_routing_uses_exact_host(url, uses_practitioner_prompt, monkeypatch):
    summary = SummarizeResponse(
        executive_summary="Preserved summary",
        relevance_score=3,
        composite_relevance_score=2.75,
        reading_priority="could_read",
        triage_rationale="Preserved rationale",
    )
    run_pipeline = Mock(return_value=summary)
    monkeypatch.setattr(feed_triage, "run_abstract_pipeline", run_pipeline)
    monkeypatch.setattr(feed_triage, "_apply_prestige", Mock())
    monkeypatch.setattr(feed_triage.surprise_service, "compute_surprise_score", Mock(return_value=0.25))

    candidate, error, fatal = feed_triage._triage_one(
        {"url": url, "title": "A public engineering post", "abstract": "A public summary."},
        log_prefix="test",
    )

    assert candidate is not None
    assert error is None
    assert fatal is False
    assert candidate.summary is summary
    expected_prompt = DEFAULT_PRACTITIONER_TRIAGE_PROMPT if uses_practitioner_prompt else None
    assert run_pipeline.call_args.kwargs["triage_prompt_override"] == expected_prompt
    assert run_pipeline.call_args.kwargs["llm_override"] is None


def test_hackernoon_feeds_use_explicit_app_subscription_route(tmp_path, monkeypatch):
    monkeypatch.setattr(
        rss_routes,
        "get_settings",
        lambda: SimpleNamespace(
            triage_db_path=tmp_path / "triage.db",
            zotero_data_dir=tmp_path / "zotero",
        ),
    )
    monkeypatch.setattr(rss_routes, "validate_rss_url", lambda url: str(url).strip())

    created = [
        asyncio.run(rss_routes.add_feed(rss_routes.RssFeedRequest(name=name, url=url)))
        for name, url in HACKERNOON_FEEDS
    ]
    listed = asyncio.run(rss_routes.list_feeds())

    assert {feed["url"] for feed in created} == {url for _, url in HACKERNOON_FEEDS}
    assert {feed["source"] for feed in created} == {"app"}
    assert listed["total"] == 2
    assert {feed["url"] for feed in listed["feeds"]} == {url for _, url in HACKERNOON_FEEDS}


def test_overlapping_hackernoon_tag_feeds_deduplicate_by_generic_stable_identity(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "triage.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        feeds_storage.init_feeds_schema(conn)
        for name, url in HACKERNOON_FEEDS:
            rss_storage.upsert_rss_feed(conn, name=name, url=url)
        conn.commit()
    finally:
        conn.close()

    rss_xml = b'''<?xml version="1.0"?>
    <rss version="2.0"><channel><title>HackerNoon</title><item>
      <title>Concrete engineering story</title>
      <guid>shared-hackernoon-story</guid>
      <link>https://hackernoon.com/concrete-engineering-story</link>
      <description>One fixture entry appears in both tag feeds.</description>
    </item></channel></rss>'''
    monkeypatch.setattr(
        app_rss,
        "_fetch_public_url",
        lambda url, *, timeout: (rss_xml, httpx.Headers()),
    )

    reader = AppRssReader(db_path)
    result = reader.refresh_feeds(max_feeds=2, max_new_items_per_feed=5)
    items = reader.get_feed_items(limit=None)
    expected_key = "feed:g:adffc19dd59a091f56058c7b3f1124ac3271a379453b639437869d9f047d9f50"

    assert result == {"feeds": 2, "inserted": 1, "updated": 1, "errors": []}
    assert len(items) == 1
    assert items[0]["stable_feed_key"] == expected_key
    assert items[0]["guid"] == "shared-hackernoon-story"


def test_generic_cross_host_guid_identity_survives_store_reopen(tmp_path, monkeypatch):
    """Synthetic overlap is source-agnostic, not actual empty-tag-feed overlap."""
    db_path = tmp_path / "data/triage.db"
    with feeds_storage.open_triage_conn(db_path) as conn:
        for name, url in (("Source A", "https://example.org/feed"),
                          ("Source B", "https://another.example/feed")):
            rss_storage.upsert_rss_feed(conn, name=name, url=url)
        conn.commit()
    xml = b'''<rss version="2.0"><channel><title>Generic</title><item>
    <guid>generic-shared-story</guid><link>https://example.org/story</link>
    <title>Generic story</title><description>Generic fixture description.</description>
    </item></channel></rss>'''
    monkeypatch.setattr(app_rss, "_fetch_public_url", lambda url, *, timeout: (xml, httpx.Headers()))
    reader = AppRssReader(db_path)

    result = reader.refresh_feeds(max_feeds=2)
    reopened = AppRssReader(db_path).get_feed_items(limit=None)

    assert result == {"feeds": 2, "inserted": 1, "updated": 1, "errors": []}
    assert len(reopened) == 1
    assert reopened[0]["stable_feed_key"] == reader.get_feed_items(limit=None)[0]["stable_feed_key"]
    assert reopened[0]["guid"] == "generic-shared-story"
    assert reopened[0]["abstract"] == "Generic fixture description."


def test_valid_empty_tag_feeds_record_checks_without_fabricating_items(tmp_path, monkeypatch):
    db = tmp_path / "triage.db"
    with feeds_storage.open_triage_conn(db) as conn:
        for name, url in HACKERNOON_FEEDS:
            rss_storage.upsert_rss_feed(conn, name=name, url=url)
        conn.commit()
    xml = b'<rss version="2.0"><channel><title>Synthetic empty tag</title></channel></rss>'
    monkeypatch.setattr(app_rss, "_fetch_public_url", lambda url, *, timeout: (xml, httpx.Headers()))
    monkeypatch.setattr(app_rss, "offline_requested", lambda: False)

    result = AppRssReader(db).refresh_feeds(max_feeds=2)
    reopened = AppRssReader(db)

    assert result == {"feeds": 2, "inserted": 0, "updated": 0, "errors": []}
    assert reopened.get_feed_items(limit=None) == []
    groups = reopened.get_feed_groups()
    assert len(groups) == 2
    assert all(group["last_check"] and group["last_check_error"] is None for group in groups)


@pytest.mark.parametrize("story_url", [
    "https://hackernoon.com/synthetic-engineering-story",
    "https://example.org/synthetic-academic-story",
])
def test_ingested_story_reaches_shared_scoring_and_processed_pool(
    tmp_path, monkeypatch, story_url,
):
    """Scripted model I/O proves wiring, not useful-versus-hype accuracy."""
    settings = Settings.load(project_root=tmp_path)
    config = GoalsConfig(
        relevance_scale={i: str(i) for i in range(1, 6)},
        llm={"draft_model": "scripted", "refine_model": "scripted",
             "api_base": "https://example.invalid/v1", "api_key_env": "SYNTHETIC_KEY"},
        prestige={"enabled": False},
    )
    runtime.set_context(runtime.AppContext(
        settings=settings, state=runtime.RuntimeState(app_state=SimpleNamespace(config=config)),
    ))
    with feeds_storage.open_triage_conn(settings.triage_db_path) as conn:
        for name, url in HACKERNOON_FEEDS:
            rss_storage.upsert_rss_feed(conn, name=name, url=url)
        conn.commit()
    xml = f'''<rss version="2.0"><channel><title>Synthetic</title><item>
    <guid>shared-synthetic-guid</guid><title>Synthetic story</title><link>{story_url}</link>
    <description>Synthetic source, not an evaluated engineering claim.</description>
    </item></channel></rss>'''.encode()
    monkeypatch.setattr(app_rss, "_fetch_public_url", lambda url, *, timeout: (xml, httpx.Headers()))
    monkeypatch.setattr(app_rss, "offline_requested", lambda: False)
    monkeypatch.setattr(summarization.corpus, "run_corpus_match", Mock(return_value={
        "has_corpus": False, "affinity_score": 0.0,
    }))
    monkeypatch.setattr(feed_triage.surprise_service, "compute_surprise_score", Mock(return_value=0.25))
    triage = TriageResult(score=3, reading_priority="could_read", tags=[],
                          rationale="Scripted assessment", confidence=0.5)
    llm = Mock()
    llm.prompt.return_value = json.dumps({"executive_summary": "Scripted summary"})
    llm.pydantic_prompt.return_value = triage
    reader = AppRssReader(settings.triage_db_path)

    refreshed = reader.refresh_feeds(max_feeds=2)
    item, = reader.get_feed_items(limit=None)
    candidate, error, fatal = feed_triage._triage_one(item, log_prefix="journey", triage_llm=llm)

    assert refreshed == {"feeds": 2, "inserted": 1, "updated": 1, "errors": []}
    assert candidate is not None and error is None and fatal is False
    assert candidate.feed_item["stable_feed_key"] == item["stable_feed_key"]
    assert candidate.summary.relevance_score == 3
    assert candidate.composite_score == 2.5
    assert candidate.summary.reading_priority == "could_read"
    llm.prompt.assert_called_once()
    llm.pydantic_prompt.assert_called_once()
    assert llm.pydantic_prompt.call_args.kwargs["pydantic_model"] is TriageResult
    prompt = llm.pydantic_prompt.call_args.kwargs["prompt"]
    assert ("practitioner" in prompt.lower()) is ("hackernoon.com" in story_url)
    _tick_phases.record_tick_decisions(
        _tick_phases._TickResults([(item, candidate)], [], [], [], [], []),
        tick_id="journey", review_mode=True,
    )
    with feeds_storage.open_triage_conn(settings.triage_db_path) as conn:
        row, = conn.execute("SELECT * FROM processed_feed_items").fetchall()
    assert row["decision"] == feeds_storage.DECISION_AWAITING_REVIEW
    assert json.loads(row["shap_contribs_json"])["summary"] == candidate.summary.model_dump(mode="json")
