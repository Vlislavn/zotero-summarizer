"""The headless setup modes persist the choice they report."""

from __future__ import annotations

from zotero_summarizer.cli import _setup
from zotero_summarizer.services._common import read_config
from zotero_summarizer.services.setup.bootstrap import bootstrap_phase0
from zotero_summarizer.settings import Settings


def test_hosted_provider_is_saved_before_optional_probe(tmp_path, monkeypatch):
    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    answers = iter(["anthropic", "ANTHROPIC_API_KEY", "claude-test"])
    monkeypatch.setattr(_setup, "_prompt", lambda *_args, **_kwargs: next(answers))
    monkeypatch.setattr(_setup, "_confirm", lambda *_args, **_kwargs: False)

    _setup._step_provider(settings)

    config = read_config(settings.config_path)
    assert config.llm_enabled is True
    assert config.llm_routing.default.provider == "hosted"
    assert config.llm_routing.default.model == "claude-test"
    assert config.llm_routing.providers[0].type.value == "anthropic"
