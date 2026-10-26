"""Standalone default-getter qualification, not an injected loader freshness claim."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.test_feed_assessment_producer import produce_review
from tests.test_research_feed import _seed, _settings
from tests.test_research_feed_boundaries import START, END
from zotero_summarizer import cli
from zotero_summarizer.runtime import AppContext, get_context, set_context
from zotero_summarizer.services.llm import factory
from zotero_summarizer.services import lifecycle
from zotero_summarizer.services._common import read_config
from zotero_summarizer.services.library import _review_cache, _review_identity
from zotero_summarizer.services.research_feed.profile import load_profile
from zotero_summarizer.services.setup.bootstrap import _default_goals_config


def _prepare_current_cache(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    set_context(AppContext(settings=settings))
    settings.config_path.write_text(json.dumps(_default_goals_config().model_dump(mode="json")))
    config = read_config(settings.config_path, calibration_path=settings.calibration_path)
    _seed(settings, "paper", "Agent evaluation")
    entry, _context, _generator, _mapper = produce_review(
        tmp_path, monkeypatch, selected_profile=load_profile(settings.data_dir),
        config=config, strategy=config.quality_review.chunk_strategy)
    source = Path(entry["review_identity"]["source_path"])
    assert entry["review_identity"] == _review_identity.build_review_identity(
        config=config, pdf_path=str(source), source_kind="override", focus_prompt="")
    _review_cache._write_one("paper", entry)
    return settings, source


def _cached_cli(tmp_path, capsys):
    code = cli.main(["research-feed", "run", "--from", START.isoformat(), "--to", END.isoformat(),
                     "--project-root", str(tmp_path), "--cached-only"])
    result = json.loads(capsys.readouterr().out)
    return code, result, json.loads(Path(result["json_path"]).read_text())


def _forbid_startup_and_clients(monkeypatch):
    startup = Mock(side_effect=AssertionError("cached-only cannot start lifecycle"))
    clients = Mock(side_effect=AssertionError("cached-only cannot construct clients"))
    monkeypatch.setattr(lifecycle, "startup", startup)
    monkeypatch.setattr(factory, "resolve_stage_client", clients)
    return startup, clients


def test_standalone_cached_default_getter_uses_existing_config_without_runtime_mutation(tmp_path, monkeypatch, capsys):
    settings, _ = _prepare_current_cache(tmp_path, monkeypatch)
    startup, clients = _forbid_startup_and_clients(monkeypatch)
    before = _review_cache._cache_path().read_bytes()

    code, result, payload = _cached_cli(tmp_path, capsys)

    assert code == 0 and result["cards_generated"] == 1 and result["failed"] == 0
    assert payload["cards"][0]["card"]["project_assessment_status"] == "partial"
    assert payload["cards"][0]["card"]["research_ideas"]
    assert get_context().state.app_state is None  # CLI creates a standalone, uninitialized context.
    assert (settings.model_dir / "deep_reviews.json").read_bytes() == before
    startup.assert_not_called()
    clients.assert_not_called()


def test_standalone_cached_abstract_stage_reuses_success_without_client(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from zotero_summarizer.models import ResearchCandidate, SummarizeResponse
    from zotero_summarizer.services.research_feed.cursor import CandidateCursor
    from zotero_summarizer.services.triage import summarization
    settings, _ = _prepare_current_cache(tmp_path, monkeypatch)
    config = read_config(settings.config_path, calibration_path=settings.calibration_path)
    get_context().state.app_state = SimpleNamespace(config=config)
    client = object()
    monkeypatch.setattr(factory, "resolve_stage_client", Mock(return_value=client))
    generate = Mock(return_value=SummarizeResponse(
        executive_summary="A retained abstract result.", relevance_score=4,
        triage_rationale="Synthetic declared relevance.",
        composite_relevance_score=4, reading_priority="should_read",
    ))
    monkeypatch.setattr(summarization, "run_abstract_dry", generate)
    candidate = ResearchCandidate(source_id="new-paper", source="app_rss", title="Agent evaluation", abstract="Evidence")
    profile = load_profile(settings.data_dir)
    first = CandidateCursor(settings).row(candidate, profile)
    set_context(AppContext(settings=settings))
    _, clients = _forbid_startup_and_clients(monkeypatch)

    assert CandidateCursor(settings).row(candidate, profile, generate=False) == first
    assert generate.call_count == 1 and get_context().state.app_state is None
    clients.assert_not_called()


def test_weekly_missing_native_history_uses_qualified_cache_without_native_observation(tmp_path, monkeypatch):
    import sqlite3
    from types import SimpleNamespace
    from zotero_summarizer.models import SummarizeResponse
    from zotero_summarizer.services.research_feed import runner, source
    from zotero_summarizer.services.research_feed.cursor import CandidateCursor
    from zotero_summarizer.services.triage import summarization

    settings, _ = _prepare_current_cache(tmp_path, monkeypatch)
    with sqlite3.connect(settings.triage_db_path) as conn:
        conn.execute("DELETE FROM processed_feed_items WHERE stable_feed_key='paper'")
        conn.execute("UPDATE rss_items SET abstract='Agent evaluation evidence' WHERE stable_feed_key='paper'")
    candidate = source.load_candidates(settings.triage_db_path, start=START, end=END, limit=1, venue="")[0]
    config = read_config(settings.config_path, calibration_path=settings.calibration_path)
    get_context().state.app_state = SimpleNamespace(config=config)
    monkeypatch.setattr(factory, "resolve_stage_client", Mock(return_value=object()))
    generate = Mock(return_value=SummarizeResponse(
        executive_summary="A retained abstract result.", relevance_score=4, triage_confidence=0.4,
        triage_rationale="Synthetic declared relevance.", composite_relevance_score=4, reading_priority="should_read",
    ))
    monkeypatch.setattr(summarization, "run_abstract_dry", generate)
    profile = load_profile(settings.data_dir)
    cached = CandidateCursor(settings).row(candidate, profile)
    cache_path = settings.data_dir / "research_feed" / "triage.json"
    before = cache_path.read_bytes()
    set_context(AppContext(settings=settings))
    startup, clients = _forbid_startup_and_clients(monkeypatch)

    result = runner.run_weekly(settings, start=START, end=END, generate_reviews=False)

    payload = json.loads(Path(result["json_path"]).read_text())
    assert result["cards_generated"] == 1 and result["failed"] == 0
    entry = payload["metadata"]["source_history"][candidate.source_id]
    assert entry["history"] == "missing" and entry["reason"] == "no_prior_history"
    assert entry["admission"] == {"status": "eligible", "reason": "triage_rule", "native_decision": None}
    assert payload["cards"][0]["provenance"]["triage"] == cached["triage_provenance"]
    assert cached["triage_provenance"]["basis"] == "abstract_only"
    assert cache_path.read_bytes() == before and generate.call_count == 1
    assert get_context().state.app_state is None
    startup.assert_not_called()
    clients.assert_not_called()


@pytest.mark.parametrize("failure", ["missing_config", "invalid_config", "stale_config", "stale_source", "missing_source"])
def test_cached_default_getter_reports_qualification_error_not_unsupported_science(
    tmp_path, monkeypatch, capsys, failure,
):
    settings, source = _prepare_current_cache(tmp_path, monkeypatch)
    startup, clients = _forbid_startup_and_clients(monkeypatch)
    _, first, _ = _cached_cli(tmp_path, capsys)
    original = Path(first["json_path"]).read_bytes()
    if failure == "missing_config":
        settings.config_path.unlink()
    elif failure == "invalid_config":
        settings.config_path.write_text("research_goals: [unterminated")
    elif failure == "stale_config":
        config = json.loads(settings.config_path.read_text())
        config["research_goals"].append("A changed standing goal")
        settings.config_path.write_text(json.dumps(config))
    elif failure == "stale_source":
        source.write_bytes(b"changed source bytes")
    else:
        source.unlink()

    code, result, payload = _cached_cli(tmp_path, capsys)

    assert code == 1 and result["cards_generated"] == 0 and result["failed"] == 1
    assert payload["manual_full_text_required"] == []
    error = payload["failed"][0]["error"]
    if failure == "missing_config":
        assert "FileNotFoundError" in error and "goals.yaml" in error
    elif failure == "invalid_config":
        assert "ParserError" in error
    elif failure == "missing_source":
        assert "review source unavailable" in error
    else:
        assert "review source/config identity stale" in error
    assert Path(first["json_path"]).read_bytes() == original
    assert get_context().state.app_state is None
    startup.assert_not_called()
    clients.assert_not_called()
