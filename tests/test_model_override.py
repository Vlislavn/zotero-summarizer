"""Phase 1.6: lifecycle.startup override_model parameter."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from zotero_summarizer.services.llm import factory

_MINIMAL_GOALS_YAML = """
research_goals:
  - Test research goal
triage_criteria:
  - Test relevance criterion
summary_structure:
  - Test summary
relevance_scale:
  1: low
  2: low-mid
  3: mid
  4: high-mid
  5: high
llm:
  draft_model: test
  refine_model: test
  api_base: http://localhost:9999/v1
  api_key_env: TEST_KEY
"""


def _bootstrap_minimal_settings(project: Path, monkeypatch):
    from zotero_summarizer.runtime import AppContext, set_context
    from zotero_summarizer.services import lifecycle
    from zotero_summarizer.settings import Settings

    project.mkdir(parents=True, exist_ok=True)
    (project / "goals.yaml").write_text(_MINIMAL_GOALS_YAML, encoding="utf-8")
    monkeypatch.setenv("TEST_KEY", "test-key-not-used")
    monkeypatch.setenv("ZOTERO_DATA_DIR", str(project / "zotero"))
    monkeypatch.setenv("PDF_ROOT", str(project / "pdfs"))
    monkeypatch.setenv("APP_LOG_FILE", "server.log")
    monkeypatch.setenv("ZS_OFFLINE", "1")
    settings = Settings.load(project_root=project)
    set_context(AppContext(settings=settings))
    lifecycle.startup(background=False)
    return settings


@pytest.fixture
def startup_boundaries(tmp_path, monkeypatch):
    from zotero_summarizer.services import lifecycle
    from zotero_summarizer.services.library import deep_review_prewarm
    from zotero_summarizer.services.library.review_fleet import prewarm

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name in ("ZOTERO_DATA_DIR", "PDF_ROOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_LOG_FILE", "server.log")
    native = Mock(return_value=object())
    monkeypatch.setattr(lifecycle, "ZoteroReader", native)
    monkeypatch.setattr(lifecycle, "ZoteroWriter", native)
    background = Mock(return_value=False)
    for name in ("_schedule_startup_gate_refresh", "_load_persisted_jobs",
                 "_resume_interrupted_jobs", "_schedule_startup_rss_refresh"):
        monkeypatch.setattr(lifecycle, name, background)
    monkeypatch.setattr(deep_review_prewarm, "schedule_on_startup", background)
    monkeypatch.setattr(prewarm, "schedule_on_startup", background)
    return native, background


@pytest.mark.parametrize("ambient_paths", [False, True])
def test_startup_fixture_owns_native_and_pdf_paths(tmp_path, monkeypatch, startup_boundaries, ambient_paths):
    project = tmp_path / "proj"
    if ambient_paths:
        monkeypatch.setenv("ZOTERO_DATA_DIR", str(tmp_path / "ambient-zotero"))
        monkeypatch.setenv("PDF_ROOT", str(tmp_path / "ambient-pdfs"))
    native, _ = startup_boundaries

    def require_owned_path(path):
        assert Path(path).is_relative_to(project), "startup native path escaped the test project"
        return object()

    native.side_effect = require_owned_path
    settings = _bootstrap_minimal_settings(project, monkeypatch)

    assert native.call_count == 2
    assert settings.zotero_data_dir == project / "zotero"
    assert settings.pdf_root == project / "pdfs"
    assert settings.app_log_file.is_relative_to(project)
    from zotero_summarizer.services._common import state
    assert state().openalex_client is None and state().openreview_client is None


def test_startup_fixture_leaves_background_work_unscheduled(tmp_path, monkeypatch, startup_boundaries):
    _, background = startup_boundaries

    _bootstrap_minimal_settings(tmp_path / "proj", monkeypatch)

    background.assert_not_called()


def test_startup_never_resolves_or_builds_stage_clients(tmp_path: Path, monkeypatch):
    forbidden = Mock(side_effect=AssertionError("startup must not construct stage clients"))
    for name in ("resolve_stage_client", "build_client_for_stage", "build_client_for_provider"):
        monkeypatch.setattr(factory, name, forbidden)

    _bootstrap_minimal_settings(tmp_path / "proj", monkeypatch)

    forbidden.assert_not_called()


def test_startup_uses_yaml_model_by_default(tmp_path: Path, monkeypatch):
    """Without override, startup uses the model configured in goals.yaml."""
    _bootstrap_minimal_settings(tmp_path / "proj", monkeypatch)
    from zotero_summarizer.services._common import state

    llm = factory.resolve_stage_client(state(), "feed")
    # _MINIMAL_GOALS_YAML sets draft_model/refine_model to "test-model"
    assert llm._inner.model == "test"


def test_startup_overrides_model_when_specified(tmp_path: Path, monkeypatch):
    """override_model replaces the YAML-configured model."""
    settings = _bootstrap_minimal_settings(tmp_path / "proj", monkeypatch)
    # Re-run startup with override.
    from zotero_summarizer.runtime import AppContext, set_context
    from zotero_summarizer.services import lifecycle
    from zotero_summarizer.services._common import state

    set_context(AppContext(settings=settings))
    lifecycle.startup(override_model="fast-local-model", background=False)

    llm = factory.resolve_stage_client(state(), "feed")
    assert llm._inner.model == "fast-local-model"


def test_startup_override_model_none_uses_yaml(tmp_path: Path, monkeypatch):
    """Explicit None falls back to the YAML model."""
    settings = _bootstrap_minimal_settings(tmp_path / "proj", monkeypatch)
    from zotero_summarizer.runtime import AppContext, set_context
    from zotero_summarizer.services import lifecycle
    from zotero_summarizer.services._common import state

    set_context(AppContext(settings=settings))
    lifecycle.startup(override_model=None, background=False)

    llm = factory.resolve_stage_client(state(), "feed")
    assert llm._inner.model == "test"
