"""Real ordinary worker/prior publication; experimental-C workers remain a separate gap."""
import builtins
from unittest.mock import Mock

import pytest

from tests._generated_write_fixtures import config, paper_io
from tests._interpretation_experiment import ScriptedClient
from tests.test_review_cache import _isolate_cache
from zotero_summarizer.services.library import _review_cache, deep_review
from zotero_summarizer.services.library.review_eligibility import usable_review


@pytest.mark.parametrize('stale', [False, True], ids=['ordinary-current', 'ordinary-stale'])
def test_worker_no_pdf_preserves_current_prior_but_can_replace_stale_prior(config, paper_io, monkeypatch, stale):
    key = 'LIBR0038'
    client = ScriptedClient(cards=False)
    ctx = {'reader': paper_io.reader, 'config': config, 'extractor': paper_io.extractor,
           'llm': client, 'quality_enabled': True}
    prior = deep_review._review_one({'item_key': key, 'pdf_path': str(paper_io.source)}, **ctx)
    assert prior['digest'] and not prior['needs_pdf'] and prior['zotero_note_written'] is False
    assert prior['review_identity']['source_kind'] == 'override'
    assert prior['verification_audit']['identity']['review_source_sha256'] == prior['review_identity']['source_sha256']
    _review_cache._write_one(key, prior)
    path = _review_cache._cache_path()
    before = path.read_bytes(), path.stat().st_mtime_ns
    assert _review_cache.review_is_current(prior, key) and usable_review(_review_cache.get_current_review(key))
    source = paper_io.source.read_bytes()
    paper_io.reader.get_item_detail.return_value['pdf_path'] = ''
    if stale:
        paper_io.source.write_bytes(b'R' * len(source))
    assert _review_cache.review_is_current(prior, key) is (not stale)
    rebuild = Mock()
    monkeypatch.setattr(deep_review, '_try_rebuild_render', rebuild)
    monkeypatch.setattr(deep_review, '_JOBS', {})
    original_import = builtins.__import__
    def ordinary_import(name, globals=None, locals=None, fromlist=(), level=0):
        assert '_feed_assessment' not in name and '_feed_assessment' not in (fromlist or ())
        assert 'interpretation' not in name
        return original_import(name, globals, locals, fromlist, level)
    monkeypatch.setattr(builtins, '__import__', ordinary_import)
    deep_review._review_worker({'item_key': key}, ctx, '')
    job = deep_review.status(key)
    assert job['status'] == 'ready' and job['completed'] == job['total'] == 1
    assert job['error'] is None and job['diagnostic'] is None and job['progress'] == {}
    terminal = [event for event in job['attempt']['events'] if event['stage'] == 'terminal']
    assert len(terminal) == 1 and terminal[0]['identity'] == {
        'outcome': 'source_unavailable', 'counts_as_verified_review': False, 'availability': 'not_observed'}
    stored = _review_cache._read_all(readonly=True)[key]
    assert 'interpretation' not in stored and 'interpretation_schema' not in stored['review_identity']
    assert stored['zotero_note_written'] is False
    if stale:
        assert stored['digest'] is None and stored['needs_pdf'] is True and stored['verification_audit'] is None
        assert not usable_review(stored) and path.read_bytes() != before[0]
        rebuild.assert_called_once_with(key)
    else:
        assert stored == prior and (path.read_bytes(), path.stat().st_mtime_ns) == before
        assert usable_review(_review_cache.get_current_review(key))
        rebuild.assert_not_called()
    assert len(client.calls) == 2
    paper_io.extractor.extract_text.assert_called_once_with(str(paper_io.source))
    paper_io.layers.assert_called_once()
    paper_io.note_stage.assert_not_called()
    assert paper_io.source.read_bytes() == (b'R' * len(source) if stale else source)
