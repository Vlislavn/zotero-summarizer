"""Public weekly work budgets must reject invalid input before side effects."""
from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest

from zotero_summarizer import cli
from zotero_summarizer.models import ResearchProfile
from zotero_summarizer.services.research_feed import runner
from zotero_summarizer.services.research_feed.profile import load_profile
from zotero_summarizer.settings import Settings
from tests.test_research_feed import _review, _seed, _settings


START = datetime(2026, 8, 20, tzinfo=timezone.utc)
END = datetime(2026, 8, 29, tzinfo=timezone.utc)


@pytest.mark.parametrize("field,value", [
    ("shortlist_budget", 0), ("shortlist_budget", -1), ("shortlist_budget", 101),
    ("card_budget", 0), ("card_budget", -1), ("card_budget", 21),
    ("source_limit", 0), ("source_limit", 5001),
    ("review_timeout_seconds", 0), ("review_timeout_seconds", 86401),
    ("shortlist_budget", True), ("card_budget", 1.5), ("card_budget", "2"),
])
def test_direct_invalid_budget_never_reads_or_starts_work(tmp_path, monkeypatch, field, value):
    settings = Settings.load(project_root=tmp_path)
    assess = Mock(side_effect=AssertionError("source work must not start"))
    monkeypatch.setattr(runner, "_assess", assess)

    with pytest.raises(ValueError):
        runner.run_weekly(settings, start=START, end=END, **{field: value})

    assess.assert_not_called()
    assert not (settings.data_dir / "research_feed").exists()


@pytest.mark.parametrize("flag,value", [
    ("--shortlist-budget", "0"), ("--shortlist-budget", "-1"), ("--shortlist-budget", "101"),
    ("--card-budget", "0"), ("--card-budget", "-1"), ("--card-budget", "21"),
    ("--review-timeout", "0"), ("--review-timeout", "86401"),
])
def test_cli_invalid_budget_precedes_settings_and_startup(monkeypatch, flag, value):
    settings_load = Mock(side_effect=AssertionError("Settings must not load"))
    monkeypatch.setattr(Settings, "load", settings_load)

    with pytest.raises(SystemExit) as error:
        cli.main(["research-feed", "run", "--from", "2026-08-20", "--to", "2026-08-29", flag, value])

    assert error.value.code == 2
    settings_load.assert_not_called()


def test_saved_profile_future_version_rejected_without_rewrite(tmp_path):
    path = tmp_path / "research_feed" / "profile.json"
    path.parent.mkdir()
    before = b'{"schema_version":999,"themes":[],"projects":[]}'
    path.write_bytes(before)

    with pytest.raises(ValueError):
        load_profile(tmp_path)

    assert path.read_bytes() == before


@pytest.mark.parametrize("field,value", [("card_budget", True), ("shortlist_budget", 2.0)])
def test_profile_uses_the_same_strict_integer_budget(field, value):
    with pytest.raises(ValueError):
        ResearchProfile(themes=[], projects=[], **{field: value})


@pytest.mark.parametrize("explicit,expected", [(False, 2), (True, 1)])
def test_real_weekly_run_uses_profile_defaults_or_exact_override(tmp_path, monkeypatch, explicit, expected):
    settings = _settings(tmp_path)
    _seed(settings, "paper-a", "Agent evaluation A")
    _seed(settings, "paper-bb", "Agent evaluation B")
    profile = load_profile(settings.data_dir)
    profile.card_budget = 2
    from zotero_summarizer.services._common import write_json_atomic
    write_json_atomic(settings.data_dir / "research_feed" / "profile.json", profile.model_dump())
    ensure = Mock()
    monkeypatch.setattr(runner, "_ensure_reviews", ensure)
    loader = Mock(side_effect=_review)
    overrides = {"shortlist_budget": 100, "card_budget": 1} if explicit else {}

    result = runner.run_weekly(settings, start=START, end=END, generate_reviews=True,
                               review_timeout_seconds=1, review_loader=loader, **overrides)

    assert result["cards_generated"] == expected
    assert loader.call_count == expected
    assert len(ensure.call_args.args[1]) == expected
    assert ensure.call_args.args[2] == 1


def test_cli_maximum_budgets_reach_handler(monkeypatch, tmp_path):
    from zotero_summarizer.services import lifecycle, research_feed
    startup = Mock()
    run = Mock(return_value={"cards_generated": 0})
    monkeypatch.setattr(lifecycle, "startup", startup)
    monkeypatch.setattr(research_feed, "run_weekly", run)

    assert cli.main(["research-feed", "run", "--from", "2026-08-20", "--to", "2026-08-29",
                     "--project-root", str(tmp_path), "--shortlist-budget", "100", "--card-budget", "20",
                     "--review-timeout", "86400", "--cached-only"]) == 0

    assert run.call_args.kwargs["shortlist_budget"] == 100
    assert run.call_args.kwargs["card_budget"] == 20
    assert run.call_args.kwargs["review_timeout_seconds"] == 86400
    assert run.call_args.kwargs["generate_reviews"] is False


def test_short_review_wait_is_not_silently_raised(monkeypatch, tmp_path):
    from zotero_summarizer.services.library import deep_review
    from zotero_summarizer.models import ResearchCandidate
    monkeypatch.setattr(deep_review, "get_current_review", lambda _key: None)
    monkeypatch.setattr(deep_review, "start", Mock())
    monkeypatch.setattr(deep_review, "status", Mock(return_value={"status": "running"}))
    monkeypatch.setattr(runner.time, "monotonic", Mock(side_effect=[0, 2]))
    sleep = Mock(side_effect=AssertionError("wait deadline has already elapsed"))
    monkeypatch.setattr(runner.time, "sleep", sleep)

    runner._ensure_reviews(Settings.load(project_root=tmp_path),
                           [ResearchCandidate(source_id="P1", source="rss", title="Paper")], 1)

    sleep.assert_not_called()


def test_maximum_card_budget_bounds_actual_loader_work(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    for index in range(21):
        _seed(settings, "x" * (index + 1), f"Agent evaluation {index}")
    loader = Mock(side_effect=_review)
    ensure = Mock()
    monkeypatch.setattr(runner, "_ensure_reviews", ensure)

    result = runner.run_weekly(settings, start=START, end=END, source_limit=5000,
                               shortlist_budget=100, card_budget=20, generate_reviews=True,
                               review_timeout_seconds=86400, review_loader=loader)

    assert result["shortlisted"] == 21
    assert result["cards_generated"] == loader.call_count == 20
    assert len(ensure.call_args.args[1]) == 20


@pytest.mark.parametrize("venue,limit,expected", [
    ("neurIPS", 2, {"bb", "dddd"}), ("", 3, {"a", "bb", "dddd"}),
])
def test_source_budget_counts_unique_matching_papers_newest_first(tmp_path, venue, limit, expected):
    settings = _settings(tmp_path)
    for key, title in [("a", "Unrelated"), ("bb", "Same paper"), ("ccc", "Same paper"),
                       ("dddd", "Unique paper")]:
        _seed(settings, key, title)
    with sqlite3.connect(settings.triage_db_path) as conn:
        for index, key in enumerate(["a", "bb", "ccc", "dddd"]):
            conn.execute("UPDATE rss_items SET publication_title=?, publication_date=? WHERE stable_feed_key=?",
                         ("Other" if key == "a" else "NeurIPS", f"2026-08-{25 - index}T00:00:00Z", key))

    result = runner.run_weekly(settings, start=START, end=END, source_limit=limit,
                               venue=venue, review_loader=_review)

    assert result["discovered"] == result["deduplicated"] == result["cards_generated"] == limit
    payload = json.loads(Path(result["json_path"]).read_text())
    assert {row["candidate"]["source_id"] for row in payload["cards"]} == expected


def test_cli_cached_run_publishes_exact_budget_from_real_database(tmp_path, monkeypatch, capsys):
    from functools import partial
    from zotero_summarizer.services import lifecycle, research_feed
    settings = _settings(tmp_path)
    _seed(settings, "a", "Agent evaluation A")
    _seed(settings, "bb", "Agent evaluation B")
    monkeypatch.setattr(lifecycle, "startup", Mock())
    monkeypatch.setattr(research_feed, "run_weekly", partial(runner.run_weekly, review_loader=_review))

    assert cli.main(["research-feed", "run", "--from", START.isoformat(), "--to", END.isoformat(),
                     "--project-root", str(tmp_path), "--shortlist-budget", "2", "--card-budget", "1",
                     "--cached-only"]) == 0

    result = json.loads(capsys.readouterr().out)
    payload = json.loads(Path(result["json_path"]).read_text())
    assert result["shortlisted"] == 2
    assert result["cards_generated"] == len(payload["cards"]) == 1
    assert Path(result["markdown_path"]).is_file()
    assert payload["metadata"]["dry_run"] is True
    assert result["writebacks_queued"] == 0


def test_malformed_source_timestamp_is_not_silently_discarded(tmp_path):
    settings = _settings(tmp_path)
    _seed(settings, "a", "Agent evaluation")
    with sqlite3.connect(settings.triage_db_path) as conn:
        conn.execute("UPDATE rss_items SET updated_at='not a timestamp'")

    with pytest.raises(ValueError):
        runner.run_weekly(settings, start=START, end=END, review_loader=_review)

    assert not (settings.data_dir / "research_feed" / "weekly-2026-08-29.json").exists()
