"""Project selection reaches model/library persistence and nested CLI services."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from zotero_summarizer.runtime import AppContext, get_context, set_context
from zotero_summarizer.services.library import _review_cache, reading_queue
from zotero_summarizer.services.library.review_fleet import verdict_store
from zotero_summarizer.settings import Settings


@pytest.mark.parametrize("store", [_review_cache, reading_queue, verdict_store])
def test_cache_path_tracks_selected_project_after_import(tmp_path, store):
    for name in ("one", "two"):
        settings = Settings.load(project_root=tmp_path / name)
        set_context(AppContext(settings=settings))
        assert store._cache_path().is_relative_to(settings.data_dir)


def test_cli_installs_selected_root_before_nested_provider_build(tmp_path, monkeypatch):
    from zotero_summarizer import cli
    from zotero_summarizer.models.providers import ProviderConfig
    from zotero_summarizer.services.llm import factory

    default = tmp_path / "default"
    requested = tmp_path / "requested"
    default.mkdir()
    requested.mkdir()
    (default / ".env").write_text("SUMMARY_TIMEOUT_SECONDS=91\n")
    (requested / ".env").write_text("SUMMARY_TIMEOUT_SECONDS=17\n")
    monkeypatch.setenv("ZOTERO_SUMMARIZER_HOME", str(default))
    monkeypatch.setenv("SUMMARY_TIMEOUT_SECONDS", "91")
    set_context(AppContext(settings=Settings.load(project_root=default)))
    observed = []
    monkeypatch.setattr(factory, "resolve_api_key", lambda _: "test")
    monkeypatch.setattr(factory, "build_llm", lambda *a, **kw: observed.append(kw))

    def handler(args):
        Settings.load(project_root=args.project_root)
        provider = ProviderConfig(name="test", api_key_env="TEST_KEY", base_url="http://localhost:1234/v1")
        factory.build_client_for_provider(provider, "model")
        return 0

    # Use the real parser/dispatcher while replacing only the command's work.
    from zotero_summarizer.cli import _goldenset
    monkeypatch.setattr(_goldenset, "_goldenset_train_classifier", handler)
    assert cli.main(["goldenset", "train-classifier", "--project-root", str(requested)]) == 0
    assert observed[0]["request_timeout_seconds"] == 17
    assert get_context().settings.project_root == requested


def test_library_cache_roundtrip_is_isolated_between_projects(tmp_path):
    one, two = (Settings.load(project_root=tmp_path / name) for name in ("one", "two"))
    set_context(AppContext(settings=one))
    _review_cache._write_one("KEY", {"title": "one"})
    reading_queue._write_cache("gate", {"KEY": {"score": 4}})
    verdict_store.upsert("KEY", {"priority": "must_read"})
    set_context(AppContext(settings=two))
    assert _review_cache._read_all() == {}
    assert reading_queue._read_cache("gate") == {}
    assert verdict_store.read_all() == {}
    _review_cache._write_one("KEY", {"title": "two"})
    set_context(AppContext(settings=one))
    assert _review_cache.get_cached_review("KEY") == {"title": "one"}
    assert reading_queue._read_cache("gate") == {"KEY": {"score": 4}}
    assert verdict_store.read_all() == {"KEY": {"priority": "must_read"}}


def test_model_loading_and_tuning_follow_runtime_root(tmp_path, monkeypatch):
    import joblib
    from zotero_summarizer.services.model import classifier_persistence, tune
    from zotero_summarizer.services.model.classifier_inputs import load_training_inputs

    def objective_factory(*a, **kw):
        return lambda trial: float(trial.suggest_int("n_estimators", 1, 2))

    monkeypatch.setattr(tune, "_objective_factory", objective_factory)
    for name in ("one", "two"):
        settings = Settings.load(project_root=tmp_path / name)
        set_context(AppContext(settings=settings))
        assert tune.load_tuned_params() == ({}, None)
        tune.tune_lightgbm([], corpus_db_path=settings.corpus_db_path, goals_config=None, n_trials=1)
        assert tune.load_tuned_params()[0]["n_estimators"] in (1, 2)
        assert settings.tuned_params_path.is_file()
        settings.golden_csv_path.write_text(name)
        settings.model_dir.mkdir()
        inputs = load_training_inputs(
            settings.golden_csv_path, classifier_name="fake", corpus_db_path=settings.corpus_db_path,
            goals_config=None, n_folds=5, pca_dim=100,
        )
        gate = SimpleNamespace(golden_csv_sha256=inputs.csv_sha256, name=name,
                               training_metadata={"training_input_sha256": inputs.sha256})
        joblib.dump(gate, settings.model_dir / "fake.joblib")
        loaded = classifier_persistence.load_or_train(
            settings.golden_csv_path, classifier_name="fake",
            corpus_db_path=settings.corpus_db_path, goals_config=None,
        )
        assert loaded.name == name


def test_border_route_reads_only_selected_project(tmp_path):
    from zotero_summarizer.api.routes._golden_border import border_suggestions
    from zotero_summarizer.services.library import border_cache
    from zotero_summarizer.services import run_log

    for name in ("one", "two"):
        settings = Settings.load(project_root=tmp_path / name)
        set_context(AppContext(settings=settings))
        settings.data_dir.mkdir(parents=True)
        settings.golden_csv_path.write_text("same golden content")
        sha = run_log.file_sha256(settings.golden_csv_path, prefix_len=64)
        border_cache.write_cache(settings.model_dir, sha, [{"item_key": name}])
        result = asyncio.run(border_suggestions())
        assert result["items"] == [{"item_key": name}]


def test_library_and_search_share_selected_pdf_cache(tmp_path, monkeypatch):
    from zotero_summarizer.integrations import pdf_fetch
    from zotero_summarizer.models import AppState
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config
    from zotero_summarizer.services.library import _pdf_acquire
    from zotero_summarizer.services.search import _fulltext
    from zotero_summarizer.services.search._models import Candidate

    monkeypatch.setattr(pdf_fetch, "validate_rss_url", lambda url: url)
    downloads = []

    def download(*a, **kw):
        downloads.append(True)
        return b"%PDF test"

    monkeypatch.setattr(pdf_fetch, "_download_public_pdf", download)
    paths = []
    for name in ("one", "two"):
        settings = Settings.load(project_root=tmp_path / name)
        context = AppContext(settings=settings)
        context.state.app_state = AppState(config=_default_goals_config())
        set_context(context)
        url = "https://example.org/paper.pdf"
        acquired = _pdf_acquire.acquire_pdf_for("KEY", {"url": url})
        assert acquired.path.parent == settings.pdf_cache_dir
        extractor = SimpleNamespace(extract_text=lambda path: paths.append(path) or "text")
        assert _fulltext.acquire_full_text(Candidate(title="paper", url=url), extractor=extractor) == "text"
        assert paths[-1] == acquired.path
    assert paths[0] != paths[1]
    assert len(downloads) == 2  # one download per project; Search reuses that project's PDF


def test_source_pdf_serving_rejects_another_projects_cache(tmp_path, monkeypatch):
    from zotero_summarizer.api.errors import APIError
    from zotero_summarizer.services.library import paper_render

    monkeypatch.setenv("PDF_ROOT", str(tmp_path / "zotero-pdfs"))
    settings = Settings.load(project_root=tmp_path / "one")
    other = Settings.load(project_root=tmp_path / "two")
    set_context(AppContext(settings=settings))
    state = {"status": "completed"}
    monkeypatch.setattr(paper_render, "_read_state", lambda _: state)
    for current in (settings, other):
        current.pdf_cache_dir.mkdir(parents=True)
        path = current.pdf_cache_dir / "paper.pdf"
        path.write_bytes(b"%PDF test")
        state["pdf_path"] = str(path)
        if current is settings:
            assert paper_render.source_pdf_path("KEY") == path
        else:
            with pytest.raises(APIError, match="outside allowed roots"):
                paper_render.source_pdf_path("KEY")


@pytest.mark.parametrize("web_article", [False, True])
def test_browser_acquisition_receives_selected_cache(tmp_path, monkeypatch, web_article):
    from zotero_summarizer.integrations import browser_fetch
    from zotero_summarizer.models import AppState
    from zotero_summarizer.services.library import _pdf_acquire
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config

    settings = Settings.load(project_root=tmp_path)
    context = AppContext(settings=settings)
    config = _default_goals_config()
    config.university_access.enabled = True
    config.quality_review.review_web_articles = True
    context.state.app_state = AppState(config=config)
    set_context(context)
    called = []

    def fetch(url, *, cache_dir, **kw):
        called.append(cache_dir)
        return cache_dir / "paper.pdf"

    monkeypatch.setattr(browser_fetch, "fetch_pdf_via_browser", fetch)
    monkeypatch.setattr(browser_fetch, "render_article_pdf", fetch)
    result = _pdf_acquire._browser_acquire(
        "KEY", [], "https://example.org/article", "", not web_article, False,
    )
    assert called == [settings.pdf_cache_dir]
    assert result.path == settings.pdf_cache_dir / "paper.pdf"
