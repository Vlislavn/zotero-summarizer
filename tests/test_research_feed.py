from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from zotero_summarizer.models import ResearchCandidate, ResearchEngineeringCard, ResearchProfile
from zotero_summarizer.services.library import deep_review
from zotero_summarizer.services.research_feed.runner import run_weekly
from zotero_summarizer.services.research_feed import runner as research_runner
from zotero_summarizer.services.research_feed.source import deduplicate
from zotero_summarizer.services.research_feed import render as research_render
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
        "review_contract_version": 3, "reviewed_at": "2026-08-26T00:00:00Z",
        "review_identity": {"fixture": key},
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
    assert list(deduplicate([first, duplicate])) == [first]


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
    payload = json.loads(Path(second["json_path"]).read_text())
    assert payload["cards"][0]["provenance"]["model"] == "model"
    assert payload["metadata"]["writebacks"] == {
        "attempted": 0, "succeeded": 0, "failed": 0, "skipped": 1,
    }
    assert Path(first["markdown_path"]).exists()
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
    insert = repositories.insert_pending_change_if_absent

    def fail_one(item_key, *args, **kwargs):
        if item_key == "Z1":
            raise RuntimeError("locked item")
        return insert(item_key, *args, **kwargs)

    monkeypatch.setattr(repositories, "insert_pending_change_if_absent", fail_one)
    result = run_weekly(
        settings, start=datetime(2026, 8, 20, tzinfo=timezone.utc),
        end=datetime(2026, 8, 29, tzinfo=timezone.utc), queue_zotero=True,
        dry_run=False, shortlist_budget=2, card_budget=2, review_loader=_review,
    )
    payload = json.loads(Path(result["json_path"]).read_text())
    assert payload["metadata"]["writebacks"] == {
        "attempted": 2, "succeeded": 1, "failed": 1, "skipped": 0,
    }


def test_writeback_idempotency_checks_exact_signature_past_same_key_cap(tmp_path):
    settings = _settings(tmp_path)
    payload = {"add_tags": ["ri:weekly", "ri:yes"], "remove_tags": []}
    with repositories.with_db_path(settings.triage_db_path):
        repositories.insert_pending_changes(
            "Z1", "Paper", [{"change_type": "tag_changes", "payload": payload}],
        )
        with sqlite3.connect(settings.triage_db_path) as conn:
            conn.execute("UPDATE pending_changes SET created_at='2000-01-01T00:00:00Z'")
        repositories.insert_pending_changes(
            "Z1", "Paper", [
                {"change_type": "tag_changes", "payload": {"add_tags": [str(i)], "remove_tags": []}}
                for i in range(5000)
            ],
        )
        # Even a 5,000-row same-item fetch cannot see the old matching signature.
        assert len(repositories.get_pending_changes(status=None, limit=5000, item_key="Z1")) == 5000

    records = [{
        "candidate": {"source_id": "feed-1", "title": "Paper"},
        "card": {"worth_reading": "yes", "topic_tags": []},
        "triage": {"matched_projects": []},
    }]
    counts = research_runner._queue_tags(settings, records, {
        "feed-1": {"materialized_zotero_key": "Z1"},
    })

    assert counts == {"attempted": 0, "succeeded": 0, "failed": 0, "skipped": 1}


def test_output_namespace_distinguishes_windows_and_venue_modes(tmp_path):
    settings = _settings(tmp_path)
    common = dict(
        end=datetime(2026, 8, 29, tzinfo=timezone.utc), review_loader=_review,
    )
    first = run_weekly(settings, start=datetime(2026, 8, 20, tzinfo=timezone.utc),
                       venue="NeurIPS", **common)
    second = run_weekly(settings, start=datetime(2026, 8, 22, tzinfo=timezone.utc),
                        venue="", **common)

    assert first["json_path"] != second["json_path"]
    assert Path(first["json_path"]).exists() and Path(second["json_path"]).exists()


def test_reversed_window_fails_before_output_mutation(tmp_path):
    settings = _settings(tmp_path)
    valid = run_weekly(
        settings, start=datetime(2026, 8, 20, tzinfo=timezone.utc),
        end=datetime(2026, 8, 29, tzinfo=timezone.utc), review_loader=_review,
    )
    original = Path(valid["json_path"]).read_bytes()

    with pytest.raises(ValueError, match="on or before"):
        run_weekly(
            settings, start=datetime(2026, 8, 30, tzinfo=timezone.utc),
            end=datetime(2026, 8, 29, tzinfo=timezone.utc), review_loader=_review,
        )

    assert Path(valid["json_path"]).read_bytes() == original


@pytest.mark.parametrize("corruption", ["bad_confidence", "non_object", "bad_score"])
def test_bad_historical_triage_row_does_not_abort_healthy_candidates(tmp_path, corruption):
    settings = _settings(tmp_path)
    _seed(settings, "bad-row", "Corrupt historical summary")
    _seed(settings, "good-row", "Healthy agent method")
    with sqlite3.connect(settings.triage_db_path) as conn:
        if corruption == "bad_confidence":
            conn.execute("UPDATE processed_feed_items SET shap_contribs_json=? WHERE stable_feed_key=?",
                         (json.dumps({"summary": {"triage_confidence": 2.0}}), "bad-row"))
        elif corruption == "non_object":
            conn.execute("UPDATE processed_feed_items SET shap_contribs_json='[]' WHERE stable_feed_key=?",
                         ("bad-row",))
        else:
            conn.execute("UPDATE processed_feed_items SET composite_score='not-a-score' WHERE stable_feed_key=?",
                         ("bad-row",))

    result = run_weekly(
        settings, start=datetime(2026, 8, 20, tzinfo=timezone.utc),
        end=datetime(2026, 8, 29, tzinfo=timezone.utc), shortlist_budget=4,
        card_budget=4, review_loader=_review,
    )
    payload = json.loads(Path(result["json_path"]).read_text())

    assert {row["candidate"]["source_id"] for row in payload["cards"]} == {"good-row"}
    assert len(payload["failed"]) == 1
    assert payload["failed"][0]["source_id"] == "bad-row"


def test_deep_review_failure_is_reported_as_failed_not_missing_text(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _seed(settings, "paper-a", "Agent method")
    monkeypatch.setattr(research_runner, "_ensure_reviews", lambda *_args: {"paper-a": "LLM unavailable"})

    result = run_weekly(
        settings, start=datetime(2026, 8, 20, tzinfo=timezone.utc),
        end=datetime(2026, 8, 29, tzinfo=timezone.utc), generate_reviews=True,
        review_loader=lambda _key: None,
    )
    payload = json.loads(Path(result["json_path"]).read_text())

    assert payload["manual_full_text_required"] == []
    assert payload["failed"] == [{"source_id": "paper-a", "error": "LLM unavailable"}]
    assert payload["metadata"]["counts"]["failed"] == 1


def test_report_stage_failure_publishes_no_partial_pair(tmp_path, monkeypatch):
    settings = _settings(tmp_path)

    def fail_markdown(_payload):
        raise OSError("disk full")

    monkeypatch.setattr(research_render, "markdown", fail_markdown)

    with pytest.raises(OSError, match="disk full"):
        run_weekly(
            settings, start=datetime(2026, 8, 20, tzinfo=timezone.utc),
            end=datetime(2026, 8, 29, tzinfo=timezone.utc), review_loader=_review,
        )

    output = settings.data_dir / "research_feed"
    assert not list(output.glob("weekly-*"))
    assert not list(output.glob(".weekly-*-*"))
    assert not (output / "state.json").exists()


def test_state_pointer_failure_keeps_previous_bundle_and_pointer(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    args = dict(
        start=datetime(2026, 8, 20, tzinfo=timezone.utc),
        end=datetime(2026, 8, 29, tzinfo=timezone.utc), review_loader=_review,
    )
    previous = run_weekly(settings, **args)
    state_path = settings.data_dir / "research_feed" / "state.json"
    previous_state = state_path.read_bytes()
    previous_json = Path(previous["json_path"]).read_bytes()
    write_state = research_runner.write_json_atomic

    def fail_state(path, payload):
        if Path(path).name == "state.json":
            raise OSError("state disk full")
        write_state(path, payload)

    monkeypatch.setattr(research_runner, "write_json_atomic", fail_state)
    with pytest.raises(OSError, match="state disk full"):
        run_weekly(settings, **args)

    assert state_path.read_bytes() == previous_state
    assert Path(previous["json_path"]).read_bytes() == previous_json
    bundles = list((settings.data_dir / "research_feed").glob("weekly-*"))
    assert len(bundles) == 2
    assert all(list(bundle.glob("*.json")) and list(bundle.glob("*.md")) for bundle in bundles)


@pytest.mark.parametrize(
    ("job", "clock", "expected"),
    [
        ({"status": "error", "error": "provider setup failed"}, [0, 0], "provider setup failed"),
        ({"status": "running"}, [0, 2], "deep review timed out after 1s"),
    ],
)
def test_ensure_reviews_preserves_job_errors_and_timeout(
    tmp_path, monkeypatch, job, clock, expected,
):
    settings = _settings(tmp_path)
    candidate = ResearchCandidate(source_id="paper-a", source="app_rss", title="Paper", url="https://example.test")
    monkeypatch.setattr(deep_review, "get_current_review", lambda _key: None)
    monkeypatch.setattr(deep_review, "start", lambda **_kwargs: {"accepted": True})
    monkeypatch.setattr(deep_review, "status", lambda _key: job)
    times = iter(clock)
    monkeypatch.setattr(research_runner.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(research_runner.time, "sleep", lambda _seconds: None)

    outcome = research_runner._ensure_reviews(settings, [candidate], 1)

    assert outcome == {"paper-a": expected}
