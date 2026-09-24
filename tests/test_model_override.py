"""Phase 1.6: lifecycle.startup override_model parameter."""
from __future__ import annotations

from pathlib import Path

import pytest

_MINIMAL_GOALS_YAML = """
research_goals:
  - Test research goal
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
    import asyncio

    project.mkdir(parents=True, exist_ok=True)
    (project / "goals.yaml").write_text(_MINIMAL_GOALS_YAML, encoding="utf-8")
    monkeypatch.setenv("TEST_KEY", "test-key-not-used")
    settings = Settings.load(project_root=project)
    set_context(AppContext(settings=settings))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    lifecycle.startup()
    return settings


def test_startup_uses_yaml_model_by_default(tmp_path: Path, monkeypatch):
    """Without override, startup uses the model configured in goals.yaml."""
    _bootstrap_minimal_settings(tmp_path / "proj", monkeypatch)
    from zotero_summarizer.services._common import state

    llm = state().resolve_stage_client("feed")
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
    lifecycle.startup(override_model="fast-local-model")

    llm = state().resolve_stage_client("feed")
    assert llm._inner.model == "fast-local-model"


def test_startup_override_model_none_uses_yaml(tmp_path: Path, monkeypatch):
    """Explicit None falls back to the YAML model."""
    settings = _bootstrap_minimal_settings(tmp_path / "proj", monkeypatch)
    from zotero_summarizer.runtime import AppContext, set_context
    from zotero_summarizer.services import lifecycle
    from zotero_summarizer.services._common import state

    set_context(AppContext(settings=settings))
    lifecycle.startup(override_model=None)

    llm = state().resolve_stage_client("feed")
    assert llm._inner.model == "test"
