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


def test_cli_doctor_saved_custom_provider_is_authorized_without_live_startup(tmp_path, monkeypatch, capsys):
    import json
    import pytest
    from zotero_summarizer import cli, runtime
    from zotero_summarizer.api.errors import APIError
    from zotero_summarizer.integrations import llm_models
    from zotero_summarizer.models.providers import LLMRoutingConfig, ProviderConfig
    from zotero_summarizer.services._common import write_user_config
    from zotero_summarizer.services.llm import model_list

    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    config = read_config(settings.config_path)
    provider = ProviderConfig(name="saved-custom", base_url="https://saved-custom.invalid/v1", api_key_env="SYNTHETIC_KEY")
    config.llm_enabled = True
    config.llm_routing = LLMRoutingConfig(providers=[provider],
        default={"provider": provider.name, "model": "synthetic-model"})
    write_user_config(settings.config_path, config)
    calls = []
    monkeypatch.setattr(model_list, "resolve_api_key", lambda p: calls.append(("credential", p.name)) or "synthetic")
    monkeypatch.setattr(llm_models, "list_openai_models",
                        lambda base, key: calls.append(("inventory", base, key)) or ["synthetic-model"])
    result = cli.main(["doctor", "--project-root", str(tmp_path), "--check", "runtime_model", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert result == 1  # only inventory was selected, not full readiness
    assert runtime.get_context().state.app_state is None
    assert next(row for row in payload["checks"] if row["id"] == "runtime_model")["status"] == "ready"
    assert calls == [("credential", provider.name), ("inventory", provider.base_url, "synthetic")]
    unsaved = provider.model_copy(update={"base_url": "https://unsaved-custom.invalid/v1"})
    with pytest.raises(APIError) as error:
        model_list.list_models_for_provider(unsaved)
    assert error.value.error == "unsafe_provider_discovery" and len(calls) == 2
