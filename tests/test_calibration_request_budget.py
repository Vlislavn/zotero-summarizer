"""Owned full-review deadlines differ from tiny probes; configured caps still apply."""
from types import SimpleNamespace

import pytest

from zotero_summarizer.models.providers import ProviderConfig
from zotero_summarizer.services.llm import factory


@pytest.fixture
def budget_builder(monkeypatch):
    configuration = SimpleNamespace(summary_timeout_seconds=420)
    monkeypatch.setattr(factory, 'settings', lambda: configuration)
    monkeypatch.setattr(factory, 'resolve_api_key', lambda _provider: None)
    monkeypatch.setattr(factory, '_verification_key', lambda _provider, before: (None, before))
    monkeypatch.setattr(factory, 'build_llm', lambda *_args, **options: options)
    from zotero_summarizer.services.llm import operational_check
    monkeypatch.setattr(operational_check, 'probe_timeout', lambda _provider: 30)
    return ProviderConfig(name='hosted', base_url='https://example.invalid/v1',
                          api_key_env='UNUSED', auth_mode='none')


def test_owned_review_uses_explicit_finite_budget_without_changing_probe_default(budget_builder):
    client = factory.build_client_for_provider(budget_builder, 'fixture', verification=True,
                                               request_timeout_seconds=360)
    assert client['verification'] is True
    assert client['request_timeout_seconds'] == 360
    assert client['request_owner'] is not None and callable(client['before_request'])
    probe = factory.build_client_for_provider(budget_builder, 'fixture', verification=True)
    assert probe['request_timeout_seconds'] == 30


def test_explicit_request_cannot_exceed_configured_summary_budget(budget_builder):
    client = factory.build_client_for_provider(budget_builder, 'fixture', request_timeout_seconds=900)
    assert client['request_timeout_seconds'] == 420


@pytest.mark.parametrize('budget', [0, -1, True, '360', float('inf'), float('nan')])
def test_invalid_request_budget_refuses_before_credentials_or_adapter(budget_builder, monkeypatch, budget):
    monkeypatch.setattr(factory, 'resolve_api_key', lambda *_a: pytest.fail('Invalid budget looked up credentials'))
    monkeypatch.setattr(factory, 'build_llm', lambda *_a, **_k: pytest.fail('Invalid budget built an adapter'))
    with pytest.raises(ValueError, match='timeout'):
        factory.build_client_for_provider(budget_builder, 'fixture', request_timeout_seconds=budget)
