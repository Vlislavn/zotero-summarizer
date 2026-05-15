"""Phase 1.6: lifecycle.startup override_model parameter."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_daemon_loop import _MINIMAL_GOALS_YAML, _bootstrap_minimal_settings


def test_startup_uses_yaml_model_by_default(tmp_path: Path, monkeypatch):
    """Without override, startup uses the model configured in goals.yaml."""
    _bootstrap_minimal_settings(tmp_path / "proj", monkeypatch)
    from zotero_summarizer.services._common import state

    llm = state().llm_refine
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

    llm = state().llm_refine
    assert llm._inner.model == "fast-local-model"


def test_startup_override_model_none_uses_yaml(tmp_path: Path, monkeypatch):
    """Explicit None falls back to the YAML model."""
    settings = _bootstrap_minimal_settings(tmp_path / "proj", monkeypatch)
    from zotero_summarizer.runtime import AppContext, set_context
    from zotero_summarizer.services import lifecycle
    from zotero_summarizer.services._common import state

    set_context(AppContext(settings=settings))
    lifecycle.startup(override_model=None)

    llm = state().llm_refine
    assert llm._inner.model == "test"
