"""Wire and CLI protocol regressions from actual live research-feed failures."""
import json
from types import SimpleNamespace

import pytest

from zotero_summarizer.models.providers import ProviderConfig
from zotero_summarizer.services.llm import factory
from zotero_summarizer.services.library import deep_review
from zotero_summarizer.services.setup.bootstrap import _default_goals_config


@pytest.mark.parametrize('extra,effort,expected', [
    (None, 'high', {'reasoning_effort': 'none'}),
    ({'reasoning_effort': 'high', 'keep': 1}, None, {'reasoning_effort': 'none', 'keep': 1}),
    ({'chat_template_kwargs': {'enable_thinking': True}}, 'high',
     {'chat_template_kwargs': {'enable_thinking': False}}),
    (None, None, None),  # Do not invent a wire parameter for unsupported models.
])
def test_explicit_verifier_off_honors_advertised_dialect(monkeypatch, extra, effort, expected):
    seen = {}
    monkeypatch.setattr(factory, 'resolve_api_key', lambda provider: 'synthetic-unit-key')
    monkeypatch.setattr(factory, 'build_llm', lambda *args, **kwargs: seen.update(kwargs) or object())
    provider = ProviderConfig(name='synthetic', base_url='https://example.invalid/v1',
                              api_key_env='SYNTHETIC_KEY', thinking_effort=effort, extra_body=extra)
    before = provider.model_dump()
    factory.build_client_for_provider(provider, 'synthetic-model', enable_thinking=False)
    assert seen['extra_body'] == expected
    assert provider.model_dump() == before


def test_generator_retains_configured_effort(monkeypatch):
    seen = {}
    monkeypatch.setattr(factory, 'resolve_api_key', lambda provider: 'synthetic-unit-key')
    monkeypatch.setattr(factory, 'build_llm', lambda *args, **kwargs: seen.update(kwargs) or object())
    provider = ProviderConfig(name='synthetic', base_url='https://example.invalid/v1',
                              api_key_env='SYNTHETIC_KEY', thinking_effort='high')
    factory.build_client_for_provider(provider, 'synthetic-model', enable_thinking=True)
    assert seen['extra_body'] == {'reasoning_effort': 'high'}


def test_deep_context_separates_verifier_from_generator_effort(monkeypatch):
    config = _default_goals_config()
    provider = SimpleNamespace(is_local=False, thinking_on=True, lean_deep_review=False,
                               structured_output=False, name='synthetic')
    calls = []
    app = SimpleNamespace(app_state=SimpleNamespace(config=config), pdf_extractor=object(),
                          resolve_stage_provider=lambda stage: provider,
                          resolve_stage_client=lambda stage, **kwargs: calls.append((stage, kwargs)) or object())
    monkeypatch.setattr(deep_review, 'get_state', lambda: app)
    monkeypatch.setattr(deep_review, '_load_prestige_context', lambda: ({}, None))
    deep_review._build_ctx(reader=object())
    assert ('deep_review', {'enable_thinking': False}) in calls
    assert ('deep_review', {'enable_thinking': True}) in calls


def test_research_feed_stdout_is_json_when_runtime_prints_diagnostics(tmp_path, monkeypatch, capsys):
    from zotero_summarizer import cli
    from zotero_summarizer.services import lifecycle, research_feed

    def startup(**kwargs):
        print('Synthetic startup diagnostic')

    def run_weekly(*args, **kwargs):
        print('Synthetic CompletionGuard diagnostic')
        return {'cards_generated': 0, 'failed': 1}

    monkeypatch.setattr(lifecycle, 'startup', startup)
    monkeypatch.setattr(research_feed, 'run_weekly', run_weekly)
    code = cli.main(['research-feed', 'run', '--from', '2026-08-22', '--to', '2026-08-29',
                     '--project-root', str(tmp_path)])
    captured = capsys.readouterr()
    assert code == 1 and json.loads(captured.out) == {'cards_generated': 0, 'failed': 1}
    assert 'Synthetic startup diagnostic' in captured.err
    assert 'Synthetic CompletionGuard diagnostic' in captured.err
