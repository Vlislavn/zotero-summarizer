"""Weekly CLI owns only its requested work, not the server's startup jobs."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from zotero_summarizer import cli
from zotero_summarizer.runtime import AppContext, set_context
from zotero_summarizer.services import lifecycle, readiness
from zotero_summarizer.services._common import write_user_config
from zotero_summarizer.services.library import _flight, _review_cache, _review_identity, deep_review
from zotero_summarizer.services.setup.bootstrap import _default_goals_config
from zotero_summarizer.storage import repositories
from tests.test_research_feed import _review, _seed, _settings


@pytest.fixture(autouse=True)
def _stable_review_identity(monkeypatch):
    monkeypatch.setattr(_review_identity, "current_review_identity", lambda key, stored: stored)


def _args(settings, *extra):
    return ["research-feed", "run", "--project-root", str(settings.project_root),
            "--from", "2026-08-20", "--to", "2026-08-29", "--card-budget", "1", *extra]


@pytest.mark.parametrize("queue", [False, True])
def test_cached_cli_needs_no_startup_config_or_classifier(tmp_path, monkeypatch, capsys, queue):
    settings = _settings(tmp_path)
    _seed(settings, "a", "Agent evaluation", materialized="Z1")
    set_context(AppContext(settings=settings))
    _review_cache._write_one("a", _review("a"))
    assert not settings.config_path.exists()
    assert not settings.golden_csv_path.exists()
    startup = Mock(side_effect=AssertionError("cached-only must not initialize models or jobs"))
    monkeypatch.setattr(lifecycle, "startup", startup)

    assert cli.main(_args(settings, "--cached-only", *(["--queue-zotero"] if queue else []))) == 0

    startup.assert_not_called()
    result = json.loads(capsys.readouterr().out)
    payload = json.loads(Path(result["json_path"]).read_text())
    assert result["cards_generated"] == 1
    assert payload["cards"][0]["candidate"]["source_id"] == "a"
    assert result["writebacks_queued"] == int(queue)
    with repositories.with_db_path(settings.triage_db_path):
        assert len(repositories.get_pending_changes(status=None)) == int(queue)


def _isolate_startup_integrations(monkeypatch, settings):
    config = _default_goals_config()
    write_user_config(settings.config_path, config)
    for name in ("setup_logging", "_init_models", "_init_database", "_init_metadata_clients", "_init_zotero"):
        monkeypatch.setattr(lifecycle, name, Mock())
    monkeypatch.setattr(readiness, "all_statuses", lambda: [])
    monkeypatch.setattr(lifecycle, "_load_persisted_jobs", Mock(side_effect=AssertionError("no job recovery")))
    monkeypatch.setattr(_flight, "run_in_background", Mock(side_effect=AssertionError("no prewarm")))


def test_generated_cli_uses_real_non_background_startup_and_only_selected_review(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    _seed(settings, "a", "Agent A")
    _seed(settings, "bb", "Agent B")
    _isolate_startup_integrations(monkeypatch, settings)
    gate = Mock()
    monkeypatch.setattr(lifecycle, "_init_classifier_gate", gate)

    def complete_review(**kwargs):
        for key in kwargs["item_keys"]:
            _review_cache._write_one(key, _review(key))
        return {"accepted": True, "status": "ready"}

    start = Mock(side_effect=complete_review)
    monkeypatch.setattr(deep_review, "start", start)
    monkeypatch.setattr(deep_review, "status", lambda _key: {"status": "ready"})

    assert cli.main(_args(settings)) == 0

    assert gate.call_args.kwargs == {"background": False}
    assert start.call_args.kwargs["item_keys"] == ["bb"]
    result = json.loads(capsys.readouterr().out)
    assert result["cards_generated"] == 1
    assert _review_cache.get_current_review("bb") is not None
    assert _review_cache.get_current_review("a") is None


def test_generation_missing_enabled_classifier_fails_without_training(tmp_path, monkeypatch):
    from zotero_summarizer.services.triage import feeds
    settings = _settings(tmp_path)
    _isolate_startup_integrations(monkeypatch, settings)
    settings.golden_csv_path.write_text("item_key,user_priority\n", encoding="utf-8")
    training = Mock(side_effect=AssertionError("weekly must not train a classifier"))
    monkeypatch.setattr(feeds, "schedule_gate_retrain_async", training)

    with pytest.raises(RuntimeError, match="No compatible cached classifier"):
        cli.main(_args(settings))

    training.assert_not_called()
    assert not (settings.data_dir / "research_feed").exists()
