"""Calibration must consume the retained source and the actual review strategy contract."""
import json
from types import SimpleNamespace

import pytest

from test_digest_verification import _digest
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models.providers import LLMRoutingConfig, ProviderConfig
from zotero_summarizer.services.setup import calibration
from zotero_summarizer.services.setup.bootstrap import _default_goals_config


@pytest.mark.parametrize('artifact, expected', [
    ({'full_text': 'PDF original body'}, [('Paper', 'PDF original body')]),
    ({'full_text': 'presentation source', 'qa_text': 'PDF original body'}, [('Paper', 'PDF original body')]),
    ({'full_text': 'presentation source', 'qa_text': ''}, []),
])
def test_calibration_uses_canonical_pdf_body_without_overriding_explicit_empty(tmp_path, artifact, expected):
    directory = tmp_path / 'PAPER'
    directory.mkdir()
    (directory / 'paper_read.json').write_text(json.dumps({'title': 'Paper', **artifact}))
    assert calibration.load_review_papers(SimpleNamespace(paper_render_dir=tmp_path)) == expected


def test_calibration_refuses_operational_source_before_model_work(tmp_path):
    directory = tmp_path / 'PAPER'
    directory.mkdir()
    denial = '<Error><Code>Denied</Code><Message>Unavailable</Message><RequestId>fixed</RequestId></Error>'
    (directory / 'paper_read.json').write_text(json.dumps({'full_text': denial}))
    with pytest.raises(APIError) as raised:
        calibration.load_review_papers(SimpleNamespace(paper_render_dir=tmp_path))
    assert raised.value.error == 'source_unusable'


def test_calibration_uses_strategy_audit_and_separate_production_verifier(tmp_path, monkeypatch):
    from zotero_summarizer.services import _common
    from zotero_summarizer.services.library import _map_reduce
    from zotero_summarizer.services.llm import factory
    from zotero_summarizer.settings import Settings

    settings = Settings.load(project_root=tmp_path)
    directory = settings.paper_render_dir / 'PAPER'
    directory.mkdir(parents=True)
    (directory / 'paper_read.json').write_text(json.dumps({'title': 'Paper', 'qa_text': 'Original paper body'}))
    config = _default_goals_config()
    config.llm_enabled = True
    provider = ProviderConfig(name='remote', base_url='https://example.invalid/v1', api_key_env='UNUSED', thinking_effort='high')
    config.llm_routing = LLMRoutingConfig(providers=[provider], default={'provider': 'remote', 'model': 'synthetic'})
    monkeypatch.setattr(_common, 'read_config', lambda *_a: config)
    clients = []

    def build(_provider, _model, **options):
        client = SimpleNamespace(options=options, closed=False)
        client.close = lambda: setattr(client, 'closed', True)
        clients.append(client)
        return client

    monkeypatch.setattr(factory, 'build_client_for_provider', build)
    seen = {}

    def strategy(title, text, actual_config, **kwargs):
        seen.update(title=title, text=text, config=actual_config, **kwargs)
        return _digest()

    def sweep(_config, _path, papers, *, run_digest, **_kwargs):
        digest, elapsed = run_digest(*papers[0], 12000)
        assert digest.read_decision == 'skip' and elapsed >= 0
        return {'status': 'calibrated'}

    monkeypatch.setattr(_map_reduce, 'digest_for_strategy', strategy)
    monkeypatch.setattr(calibration, 'tier2_calibrate', sweep)
    calibration.run_full_calibration(settings, item_keys=['PAPER'])
    assert seen['title'] == 'Paper' and seen['text'] == 'Original paper body'
    assert seen['config'] is config and seen['budget'].max_chars == 12000
    assert seen['reduce_llm'].options['enable_thinking'] is True
    assert seen['map_llm'].options['enable_thinking'] is False
    assert seen['reduce_llm'] is not seen['map_llm']
    assert seen['verification_audit'] == {}
    assert all(client.closed for client in clients)
