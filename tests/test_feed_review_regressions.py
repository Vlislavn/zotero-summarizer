"""Focused review regressions; scripted replies prove wiring, not usefulness."""
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_feed_engineering import _assessment, _generated_entry
from tests.test_research_feed import _review, _seed, _settings
from tests.test_research_feed_boundaries import START, END
from zotero_summarizer import models
from zotero_summarizer.models import ResearchCandidate, ResearchProfile
from zotero_summarizer.runtime import AppContext, set_context
from zotero_summarizer.services.library import _feed_assessment as feed, _pdf_acquire, _review_cache, deep_review
from zotero_summarizer.services.research_feed import runner
from zotero_summarizer.services.research_feed.card import build_card
from zotero_summarizer.services.research_feed.profile import load_profile


@pytest.mark.parametrize("name", [
    "FeedReviewContext", "FeedProjectAssessment", "FeedReproduction",
    "FeedImpact", "FeedAssessment", "FeedDigestResponse",
])
def test_assessment_models_are_public_package_contracts(name):
    from zotero_summarizer.models import research_feed
    assert getattr(models, name) is getattr(research_feed, name)


def test_omitted_projects_publish_explicit_unknowns(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _seed(settings, "paper", "Agent evaluation")
    profile = load_profile(settings.data_dir)
    entry, generator, verifier = _generated_entry(
        tmp_path, monkeypatch, "paper", profile, content=_assessment("paper", projects=(profile.projects[0],)),
    )

    result = runner.run_weekly(settings, start=START, end=END, review_loader=lambda _key: entry)
    card = json.loads(Path(result["json_path"]).read_text())["cards"][0]["card"]

    assert result["failed"] == 0 and card["project_assessment_status"] == "partial"
    assert [row["target_project"] for row in card["project_assessments"]] == profile.projects
    assert card["project_assessments"][0]["assessment"] == "applicable"
    for index, row in enumerate(card["project_assessments"][1:], 1):
        assert row["assessment"] == "unknown" and row["use_kind"] is None
        assert card["assessment_evidence"][f"/projects/{index}"] is None
    assert len(card["research_ideas"]) == len(generator.calls) == verifier.calls == 1


@pytest.mark.parametrize("quote", ["", "  ", "\n\t"])
def test_blank_unknown_quote_retains_valid_neighbor(tmp_path, monkeypatch, quote):
    profile = ResearchProfile(themes=[], projects=["agent harness", "activation oracle"])
    content = _assessment("paper", projects=tuple(profile.projects))
    content["projects"][1].update(
        assessment="unknown", use_kind=None, mechanism="", target_component="",
        proposed_change="", minimal_validation_experiment="", evidence_quote=quote,
    )
    entry, generator, verifier = _generated_entry(tmp_path, monkeypatch, "paper", profile, content=content)

    assessment = feed.qualify(entry, feed.context_for("paper", profile))

    assert assessment["status"] == "partial"
    assert assessment["content"]["projects"][1]["assessment"] == "unknown"
    assert assessment["evidence"]["/projects/1"] is None
    assert assessment["evidence"]["/projects/0"]["quote"]
    assert len(generator.calls) == verifier.calls == 1


@pytest.mark.parametrize("blocker", ["code", "quality"])
def test_effort_estimate_cannot_override_existing_blocker(tmp_path, monkeypatch, blocker):
    profile = ResearchProfile(themes=[], projects=["agent harness"])
    content = _assessment("paper")
    content["reproduction"] = {
        "tier": "one_week", "rationale": "Scripted effort estimate", "missing_inputs": [],
        "evidence_quote": content["projects"][0]["evidence_quote"],
    }
    quality = {'missing_critical': ['Dataset missing']} if blocker == 'quality' else None
    entry, _, _ = _generated_entry(tmp_path, monkeypatch, "paper", profile, content=content, quality=quality)
    if blocker == "code":
        entry["code_link"] = {"found": False}
        expected = "No verified code URL"
    else:
        expected = "Dataset missing"
    candidate = ResearchCandidate(source_id="paper", source="app_rss", title="Agent evaluation")
    triage = runner.triage_candidate(candidate, {"decision": "selected"}, profile)

    card = build_card(candidate, triage, entry, profile)

    assert card.reproducibility_tier == "blocked"
    assert expected in card.missing_reproduction_inputs
    assert card.reproduction_assessment.tier == "one_week"


def _unprocessed(settings, key):
    with sqlite3.connect(settings.triage_db_path) as conn:
        conn.execute("DELETE FROM processed_feed_items WHERE stable_feed_key = ?", (key,))


def test_cached_only_missing_abstract_stage_is_failure_not_rejection(tmp_path):
    settings = _settings(tmp_path)
    _seed(settings, "paper", "Agent evaluation")
    _unprocessed(settings, "paper")

    result = runner.run_weekly(settings, start=START, end=END, review_loader=_review)
    payload = json.loads(Path(result["json_path"]).read_text())

    assert result["discovered"] == result["failed"] == 1
    assert result["triaged"] == 0 and payload["rejected_near_threshold"] == []
    assert payload["failed"][0]["source_id"] == "paper"


def test_optional_corrupt_abstract_cache_does_not_abort_history_neighbors(tmp_path):
    settings = _settings(tmp_path)
    _seed(settings, "history", "Agent evaluation")
    _seed(settings, "uncached", "Agent benchmark")
    _unprocessed(settings, "uncached")
    cache_dir = settings.data_dir / "research_feed"
    cache_dir.mkdir()
    (cache_dir / "triage.json").write_text("{truncated")

    result = runner.run_weekly(settings, start=START, end=END, review_loader=_review)
    payload = json.loads(Path(result["json_path"]).read_text())

    assert result["cards_generated"] == result["failed"] == 1
    assert payload["cards"][0]["candidate"]["source_id"] == "history"
    assert payload["failed"][0]["source_id"] == "uncached"
    assert (cache_dir / "triage.json").read_text() == "{truncated"


@pytest.mark.parametrize("error", [FileNotFoundError("missing PDF"), PermissionError("denied")])
def test_generation_reacquires_missing_pdf_but_not_denied_source(tmp_path, monkeypatch, error):
    settings = _settings(tmp_path)
    profile = ResearchProfile(themes=[], projects=["agent harness"])
    entry, _, _ = _generated_entry(tmp_path, monkeypatch, "paper", profile)
    candidate = ResearchCandidate(source_id="paper", source="app_rss", title="Agent evaluation")
    monkeypatch.setattr(feed, "current_review", Mock(side_effect=[error, entry]))
    start = Mock()
    monkeypatch.setattr(deep_review, "start", start)
    monkeypatch.setattr(deep_review, "status", lambda _key: {"status": "ready"})

    outcomes = runner._ensure_reviews(settings, [candidate], 1, profile)

    if isinstance(error, FileNotFoundError):
        assert outcomes == {} and start.call_args.kwargs["item_keys"] == ["paper"]
        assert start.call_args.kwargs["pdf_overrides"] == {}
    else:
        start.assert_not_called()
        assert "PermissionError" in outcomes["paper"]


@pytest.mark.parametrize("feed_mode", [True, False])
def test_real_worker_limits_feed_acquisition_and_binds_owning_cache(tmp_path, monkeypatch, feed_mode):
    settings = _settings(tmp_path)
    set_context(AppContext(settings=settings))
    profile = ResearchProfile(themes=[], projects=["agent harness"])
    context = feed.context_for("paper", profile)
    entry, _, _ = _generated_entry(tmp_path, monkeypatch, "paper", profile)
    envelope = {key: value for key, value in entry.pop("feed_assessment").items() if key not in {
        "payload_sha256", "review_identity", "digest_sha256", "provenance", "generated_at",
    }}
    reader = SimpleNamespace(get_item_detail=lambda _key: {
        "title": "Agent evaluation", "url": "https://example.test/paper", "has_pdf": False,
    })
    acquired = _pdf_acquire.AcquireResult(path=Path(entry["review_identity"]["source_path"]), outcome="acquired_direct")
    restricted, interactive = Mock(return_value=acquired), Mock(return_value=acquired)
    monkeypatch.setattr(_pdf_acquire, "acquire_pdf_for", restricted)
    monkeypatch.setattr(_pdf_acquire, "acquire_for_item", interactive)

    def review_one(item, **kwargs):
        if feed_mode:
            assert kwargs["feed_context"] == context
            kwargs["feed_out"].update(envelope)
        return entry

    monkeypatch.setattr(deep_review, "_review_one", review_one)
    monkeypatch.setattr(deep_review, "get_current_review", lambda _key: None)
    monkeypatch.setattr(deep_review, "_try_rebuild_render", Mock())
    ctx = {"reader": reader, "_acquire_missing": True,
           "_feed_contexts": {"paper": context} if feed_mode else {}}

    deep_review._review_worker({"item_key": "paper"}, ctx, "")

    assert deep_review.status("paper")["status"] == "ready"
    saved = _review_cache.get_cached_review("paper")
    if feed_mode:
        assert restricted.call_args.kwargs["allow_browser"] is False
        interactive.assert_not_called()
        assert feed.qualify(saved, context)["status"] == "assessed"
    else:
        restricted.assert_not_called()
        interactive.assert_called_once_with("paper", reader=reader)
        assert "feed_assessment" not in saved


def test_v1_provenance_keeps_numeric_triage_confidence(tmp_path):
    settings = _settings(tmp_path)
    _seed(settings, "paper", "Agent evaluation")
    result = runner.run_weekly(settings, start=START, end=END, review_loader=_review)
    record = json.loads(Path(result["json_path"]).read_text())["cards"][0]
    assert record["provenance"]["confidence"] == record["triage"]["confidence"] == 0.9
