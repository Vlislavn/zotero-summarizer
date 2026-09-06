from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from zotero_summarizer.models import ResearchCandidate, ResearchEngineeringCard, ResearchProfile
from zotero_summarizer.services.research_feed.runner import run_weekly
from zotero_summarizer.services.research_feed.source import deduplicate
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import repositories


def _settings(tmp_path) -> Settings:
    settings = Settings.load(project_root=tmp_path)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.triage_db_path)
    conn.row_factory = sqlite3.Row
    repositories.apply_schema(conn)
    conn.execute("INSERT INTO rss_feeds (id, name, url) VALUES (1, 'AI Conference', 'https://example.test/feed')")
    conn.commit()
    conn.close()
    return settings


def _seed(settings: Settings, key: str, title: str, *, materialized: str = "") -> None:
    summary = {"summary": {
        "executive_summary": "A benchmark for agent harness evaluation",
        "methods": "benchmark evaluation method", "triage_rationale": "Direct project fit",
        "triage_confidence": 0.9,
    }}
    conn = sqlite3.connect(settings.triage_db_path)
    conn.execute(
        """INSERT INTO rss_items
           (rss_feed_id, stable_feed_key, title, abstract, url, canonical_url, publication_date)
           VALUES (1, ?, ?, 'agent harness benchmark', ?, ?, '2026-08-25T00:00:00+00:00')""",
        (key, title, f"https://example.test/{key}", f"https://example.test/{key}"),
    )
    conn.execute(
        """INSERT INTO processed_feed_items
           (feed_library_id, feed_item_id, source_type, stable_feed_key, guid, title,
            decision, composite_score, run_id, shap_contribs_json, materialized_zotero_key)
           VALUES (1, ?, 'app_rss', ?, ?, ?, 'selected', 5, 'r1', ?, ?)""",
        (len(key), key, key, title, json.dumps(summary), materialized or None),
    )
    conn.commit()
    conn.close()


def _review(key: str) -> dict:
    return {
        "review_contract_version": 2, "reviewed_at": "2026-08-26T00:00:00Z",
        "provenance": {"provider": "local", "model": "model", "prompt_schema_version": 2},
        "digest": {
            "tldr": f"Review {key}", "key_strength": "Reliable benchmark",
            "methods": "agent evaluation", "implementation": ["Add the benchmark"],
            "relevance": "Use in agent harness", "read_decision": "skim",
            "basis": "full_text", "significance": 4, "novelty": 4,
        },
        "quality": {"missing_critical": [], "red_flags": []},
        "goal_summaries": [{"goal": "agent harness", "relevance": "high"}],
        "code_link": {
            "found": True, "exists": True, "relevance": "matched",
            "url": f"https://github.com/example/{key}",
        },
    }


def test_schema_and_dedupe_reject_unknown_or_fabricated_values() -> None:
    with pytest.raises(ValidationError):
        ResearchProfile(themes=["x"], projects=["y"], topic_taxonomy=["made-up"])
    with pytest.raises(ValidationError):
        ResearchEngineeringCard(
            source_id="x", problem="p", core_idea="i", engineering_novelty="n",
            code_urls=["github.com/invented"], reproducibility_tier="unknown",
            reproducibility_rationale="unknown", research_impact=0, production_impact=0,
            personal_novelty=0, worth_reading="skip",
        )
    first = ResearchCandidate(source_id="a", source="rss", title="Same Paper", doi="10.1/x")
    duplicate = ResearchCandidate(source_id="b", source="rss", title="Same Paper", doi="10.1/x")
    assert deduplicate([first, duplicate]) == [first]


def test_weekly_run_enforces_budget_isolates_failure_and_is_idempotent(tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed(settings, "a-key", "Agent harness benchmark", materialized="Z1")
    _seed(settings, "bb-key", "Agent evaluation method", materialized="Z2")

    def loader(key: str):
        if key == "bb-key":
            raise RuntimeError("broken paper")
        return _review(key)

    args = dict(
        start=datetime(2026, 8, 20, tzinfo=timezone.utc),
        end=datetime(2026, 8, 29, tzinfo=timezone.utc),
        shortlist_budget=2, card_budget=2, queue_zotero=True, dry_run=False,
        review_loader=loader,
    )
    first = run_weekly(settings, **args)
    second = run_weekly(settings, **args)

    assert first["cards_generated"] == 1
    assert first["failed"] == 1
    assert first["writebacks_queued"] == 1
    assert second["writebacks_queued"] == 0
    payload = json.loads((settings.data_dir / "research_feed" / "weekly-2026-08-29.json").read_text())
    assert payload["cards"][0]["provenance"]["model"] == "model"
    assert payload["metadata"]["writebacks"] == {
        "attempted": 0, "succeeded": 0, "failed": 0, "skipped": 1,
    }
    assert (settings.data_dir / "research_feed" / "weekly-2026-08-29.md").exists()
    with repositories.with_db_path(settings.triage_db_path):
        assert len(repositories.get_pending_changes(status=None)) == 1


def test_dry_run_never_queues_zotero(tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed(settings, "dry-key", "Agent harness benchmark", materialized="Z1")

    result = run_weekly(
        settings, start=datetime(2026, 8, 20, tzinfo=timezone.utc),
        end=datetime(2026, 8, 29, tzinfo=timezone.utc), dry_run=True,
        queue_zotero=True, review_loader=_review,
    )

    assert result["writebacks_queued"] == 0
    with repositories.with_db_path(settings.triage_db_path):
        assert repositories.get_pending_changes(status=None) == []


def test_writeback_failure_isolated_and_counted(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    _seed(settings, "a-key", "Agent harness benchmark", materialized="Z1")
    _seed(settings, "bb-key", "Agent evaluation method", materialized="Z2")
    insert = repositories.insert_pending_changes

    def fail_one(item_key, *args, **kwargs):
        if item_key == "Z1":
            raise RuntimeError("locked item")
        return insert(item_key, *args, **kwargs)

    monkeypatch.setattr(repositories, "insert_pending_changes", fail_one)
    run_weekly(
        settings, start=datetime(2026, 8, 20, tzinfo=timezone.utc),
        end=datetime(2026, 8, 29, tzinfo=timezone.utc), queue_zotero=True,
        dry_run=False, shortlist_budget=2, card_budget=2, review_loader=_review,
    )
    payload = json.loads((settings.data_dir / "research_feed" / "weekly-2026-08-29.json").read_text())
    assert payload["metadata"]["writebacks"] == {
        "attempted": 2, "succeeded": 1, "failed": 1, "skipped": 0,
    }
