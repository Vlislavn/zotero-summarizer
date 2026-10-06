import json
from types import SimpleNamespace

import pytest

from zotero_summarizer.cli import build_parser


def test_sensitive_capture_is_explicit_cli_only():
    parser = build_parser()
    assert not parser.parse_args(['verify-deep-review']).capture_local
    assert parser.parse_args(['verify-deep-review', '--capture-local']).capture_local


@pytest.mark.parametrize('denied', [False, True])
def test_readonly_cli_captures_actual_strategy_and_keeps_source_unchanged(tmp_path, monkeypatch, denied):
    from test_digest_verification import PAPER, _Verifier, _digest
    from test_source_admission import DENIAL
    from zotero_summarizer.api.errors import APIError
    from zotero_summarizer.cli import _app
    from zotero_summarizer.services import _common
    from zotero_summarizer.services.library import quality_eval
    from zotero_summarizer.services.llm import factory
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config

    class Client(_Verifier):
        def pydantic_prompt(self, *, prompt, pydantic_model, **kwargs):
            if pydantic_model.__name__ == 'PaperDigest':
                return _digest()
            return super().pydantic_prompt(prompt=prompt, pydantic_model=pydantic_model, **kwargs)

    source = tmp_path / 'paper.pdf'
    source.write_bytes(b'local fixture fingerprint only')
    state_path = tmp_path / 'paper_render' / 'A' / 'paper_read.json'
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({'qa_text': DENIAL if denied else PAPER, 'title': 'Paper', 'pdf_path': str(source)}))
    original = state_path.read_bytes()
    config = _default_goals_config()
    config.quality_review.chunk_strategy = 'rank'
    monkeypatch.setattr(_app.Settings, 'load', lambda **kwargs: SimpleNamespace(data_dir=tmp_path, config_path=tmp_path / 'config'))
    monkeypatch.setattr(_common, 'read_config', lambda path: config)
    monkeypatch.setattr(factory, 'build_client_for_stage', lambda *a, **k: Client())
    monkeypatch.setattr(factory, 'build_client_for_provider', lambda *a, **k: Client())
    monkeypatch.setattr(quality_eval, 'evaluate_quality', lambda **kwargs: SimpleNamespace(quality_band='Fair', grade='C'))
    args = build_parser().parse_args(['verify-deep-review', '--item-key', 'A', '--capture-local'])
    if denied:
        with pytest.raises(APIError, match='operational response'):
            _app._verify_deep_review(args)
    else:
        assert _app._verify_deep_review(args) == 0
    capture = json.loads(next((tmp_path / 'deep_review_attempts').glob('*/attempt.json')).read_text())
    counts = capture['metadata']['stage_counts']
    assert counts.get('generator:prompt', 0) == (0 if denied else 1)
    assert counts.get('verifier:parsed_response', 0) == (0 if denied else 1)
    assert counts['config:identity'] >= 1
    if denied:
        terminal = next(event for event in capture['metadata']['events'] if event['stage'] == 'terminal')
        assert terminal['identity']['code'] == 'source_unusable'
    assert any(event['stage'] == 'pdf' for event in capture['events'])
    assert state_path.read_bytes() == original
    assert source.read_bytes() == b'local fixture fingerprint only'
