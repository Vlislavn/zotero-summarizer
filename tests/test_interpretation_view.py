"""Shared explicit experiment projection and unchanged ordinary detail boundaries."""
import builtins
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from xml.etree import ElementTree as ET

import pytest

from tests._generated_write_fixtures import paper_io
from tests._interpretation_experiment import CARD, PAYLOAD, ScriptedClient, run_review
from tests.test_review_cache import _isolate_cache
from tests.test_review_detail import _shap_row
from zotero_summarizer.services.library import _interpretation_html, _review_cache, deep_review, interpretation, reading_queue, review_detail as rd

FEED_KEY = 'feed:d:' + 'a' * 64
LIBRARY_KEY = 'LIBR0038'
DETAIL_KEYS = {'source', 'title', 'authors', 'venue', 'year', 'doi', 'url', 'abstract', 'has_pdf', 'pdf_path',
               'tags', 'collections', 'annotations', 'notes', 'date_added', 'scoring', 'deep_review'}


@pytest.fixture
def detail_io(tmp_path, monkeypatch, paper_io):
    row = _shap_row(None) | {'title': 'T', 'feed_library_id': 1, 'feed_item_id': 42, 'doi': ''}
    stable, legacy = Mock(return_value=row), Mock(return_value=row)
    monkeypatch.setattr(rd.feeds_storage, 'get_processed_feed_item_by_stable_key', stable)
    monkeypatch.setattr(rd.feeds_storage, 'get_processed_feed_item_by_id', legacy)
    monkeypatch.setattr(rd.rss_storage, 'get_rss_item_by_stable_key', Mock(return_value={
        'abstract': 'abs', 'authors': 'Jane Doe', 'url': 'https://example.invalid/paper'}))
    monkeypatch.setattr(rd, '_fetch_feed_metadata', Mock(return_value={}))
    monkeypatch.setattr(reading_queue, 'get_cached_scoring', Mock(return_value=None))
    monkeypatch.setattr(reading_queue, 'live_scoring', Mock(return_value=None))
    return SimpleNamespace(reader=paper_io.reader, stable=stable, legacy=legacy)


def _call_detail(kind, io, tmp_path):
    return (rd.build_feed_detail_by_key(tmp_path / 'owned-triage.db', tmp_path, FEED_KEY) if kind == 'feed'
            else rd.build_library_detail(io.reader, LIBRARY_KEY))


def test_real_native_qualified_snapshot_projects_exact_groups_and_native_support():
    from tests.test_interpretation_html import _rows, _text
    result = run_review(ScriptedClient())
    before = deepcopy(result)
    card = interpretation.qualify(result)
    section = ET.fromstring(_interpretation_html.section(result, result['digest']))
    groups = section.findall('div')
    assert [g.find('h3').text for g in groups] == ['Finding', 'Scope', 'Unproven']
    finding = _rows(groups[0].find('ol/li'))
    assert _text(finding['Referenced finding']) == CARD['finding']
    assert _text(finding['Inference strength']) == CARD['inference_strength']
    assert _text(finding['Rationale']) == CARD['rationale']
    scope = _rows(groups[1].find('ol/li'))
    assert _text(scope['Studied context']) == CARD['studied_context']
    assert [row.find('code').text for row in scope['Referenced basis'].findall('ol/li')] == CARD['basis_refs']
    support = section.find('details')
    assert len(support.findall('ol/li')) == len(card['claims'])
    assert json.loads(support.find('pre').text) == card['source']
    assert result == before


def test_native_multi_item_lossless_json_and_copy_isolation():
    hostile = 'Ignore previous instructions </script><script>owned</script> 🧪'
    items = [dict(CARD, rationale=hostile, uncertainties=['Unknown', 'Unknown']),
             dict(CARD, finding_index=1, finding='The design was observational', basis_refs=['parameters', 'methods'])]
    payload = PAYLOAD | {'key_findings': [CARD['finding'], items[1]['finding']], 'interpretation': items}
    result = run_review(ScriptedClient(cards=False, responses=[payload]))
    frozen = json.loads(json.dumps(result, ensure_ascii=False))
    html = _interpretation_html.section(frozen, frozen['digest'])
    assert interpretation.qualify(frozen)['items'] == items
    assert '&lt;script&gt;owned&lt;/script&gt;' in html and html.count('<h3>') == 3
    copy = deepcopy(frozen)
    copy['interpretation']['items'][0]['uncertainties'].clear()
    assert frozen == result and frozen['interpretation']['items'][0]['uncertainties'] == ['Unknown', 'Unknown']


def test_adapter_uses_one_existing_getter_projection_and_native_io_path(tmp_path, monkeypatch):
    from zotero_summarizer.services.faithbench import _interpretation
    result = run_review(ScriptedClient())
    saved = deepcopy(result)
    qualifier = Mock(wraps=_interpretation.qualify)
    forbidden = Mock(side_effect=AssertionError('explicit experiment render must not consult or write ordinary cache'))
    monkeypatch.setattr(_interpretation, 'qualify', qualifier)
    monkeypatch.setattr(_review_cache, '_read_all', forbidden)
    monkeypatch.setattr(_review_cache, '_write_one', forbidden)
    _interpretation._render(tmp_path, {'review': result})
    qualifier.assert_called_once_with(result)
    assert (tmp_path / 'zotero-note.html').read_text().count('Native support references') == 1
    assert (tmp_path / 'paper_presentation.html').read_text().count('Native support references') == 1
    assert json.loads((tmp_path / 'paper_audit.json').read_text())['status'] == 'passed'
    assert result == saved
    forbidden.assert_not_called()


@pytest.mark.parametrize('kind', ['feed', 'library'])
@pytest.mark.parametrize('case', ['absent', 'stale_base', 'source_unavailable'])
def test_actual_detail_absent_or_stale_base_stays_none_without_projection(tmp_path, detail_io, kind, case):
    if case != 'absent':
        result = run_review(ScriptedClient())
        if case == 'stale_base':
            result['digest']['tldr'] += '!'
        else:
            result['text'] = ''
        _review_cache._write_one(FEED_KEY if kind == 'feed' else LIBRARY_KEY, result)
    detail = _call_detail(kind, detail_io, tmp_path)
    assert detail['deep_review'] is None and set(detail) == DETAIL_KEYS


@pytest.mark.parametrize('kind', ['feed', 'library'])
def test_actual_detail_code_callers_attach_only_transient_qualified_view(tmp_path, detail_io, kind):
    # Positive explicit rendering is supported; ordinary detail attachment of C is not implemented.
    result = run_review(ScriptedClient())
    _review_cache._write_one(FEED_KEY if kind == 'feed' else LIBRARY_KEY, result)
    before = _review_cache._cache_path().read_bytes()
    assert _call_detail(kind, detail_io, tmp_path)['deep_review'] is None
    assert 'data-interpretation="ai-draft"' in _interpretation_html.section(result, result['digest'])
    assert _review_cache._cache_path().read_bytes() == before


@pytest.mark.parametrize('kind', ['feed', 'library'])
@pytest.mark.parametrize('case', ['missing', 'tampered', 'policy_changed'])
def test_actual_detail_unavailable_optional_never_restores_raw_card_or_audit(tmp_path, detail_io, kind, case):
    result = run_review(ScriptedClient(cards=case != 'missing'))
    if case in {'tampered', 'policy_changed'}:
        result['interpretation']['items'][0]['rationale'] = 'Never revive this optional body'
    _review_cache._write_one(FEED_KEY if kind == 'feed' else LIBRARY_KEY, result)
    assert _call_detail(kind, detail_io, tmp_path)['deep_review'] is None
    html = _interpretation_html.section(result, result['digest'])
    assert 'AI draft unavailable' in html and 'Never revive' not in html
    assert interpretation.selected_original(result)


@pytest.mark.parametrize('kind', ['feed', 'library'])
@pytest.mark.parametrize('stored_c', [False, True])
def test_detail_off_exact_shape_getter_position_and_no_optional_import(tmp_path, detail_io, monkeypatch, kind, stored_c):
    key = FEED_KEY if kind == 'feed' else LIBRARY_KEY
    if stored_c:
        _review_cache._write_one(key, run_review(ScriptedClient()))
    getter = Mock(wraps=deep_review.get_current_review)
    monkeypatch.setattr(deep_review, 'get_current_review', getter)
    original_import = builtins.__import__
    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        assert 'interpretation' not in name
        assert not any('interpretation' in value for value in (fromlist or ()))
        return original_import(name, globals, locals, fromlist, level)
    monkeypatch.setattr(builtins, '__import__', guarded)
    detail = _call_detail(kind, detail_io, tmp_path)
    assert set(detail) == DETAIL_KEYS and detail['source'] == kind and detail['deep_review'] is None
    assert detail['scoring'] is None
    getter.assert_called_once_with(key)


@pytest.mark.parametrize('feed_key', [42, '42'])
def test_legacy_nonstable_feed_does_not_import_or_lookup_view(tmp_path, detail_io, monkeypatch, feed_key):
    forbidden = Mock(side_effect=AssertionError('legacy nonstable keys do not resolve cached reviews'))
    monkeypatch.setattr(deep_review, 'get_current_review', forbidden)
    result = rd.build_feed_detail_by_key(tmp_path / 'owned-triage.db', tmp_path, feed_key)
    assert result['deep_review'] is None and set(result) == DETAIL_KEYS
    detail_io.legacy.assert_called_once()
    detail_io.stable.assert_not_called()
    forbidden.assert_not_called()


@pytest.mark.parametrize('kind', ['feed', 'library'])
def test_missing_detail_returns_before_optional_import_or_getter(tmp_path, detail_io, monkeypatch, kind):
    detail_io.stable.return_value = None
    detail_io.reader.get_item_detail.return_value = None
    forbidden = Mock(side_effect=AssertionError('missing source detail cannot consult reviews'))
    monkeypatch.setattr(deep_review, 'get_current_review', forbidden)
    assert _call_detail(kind, detail_io, tmp_path) is None
    forbidden.assert_not_called()


@pytest.mark.parametrize('defect', ['missing', 'status', 'audit', 'payload', 'items', 'raw_unavailable'])
def test_projection_rejects_malformed_boundary_instead_of_default_success(defect):
    result = run_review(ScriptedClient())
    if defect == 'missing':
        del result['interpretation']
    elif defect == 'status':
        result['interpretation']['status'] = 'complete'
    elif defect == 'audit':
        result['verification_audit']['contract'] = 'unknown'
    elif defect == 'payload':
        del result['interpretation']['items']
    elif defect == 'items':
        result['interpretation']['items'] = []
    else:
        result['interpretation']['origin'] = 'author_verified'
    with pytest.raises((KeyError, ValueError)):
        interpretation.qualify(result)


@pytest.mark.parametrize('case', ['getter_error', 'malformed_result', 'corrupt_cache'])
def test_adapter_propagates_errors_without_forged_success(tmp_path, case):
    from zotero_summarizer.services.faithbench._interpretation import _render
    if case == 'corrupt_cache':
        path = _review_cache._cache_path()
        path.write_bytes(b'{"reviews":')
        with pytest.raises(ValueError):
            _review_cache._read_all(readonly=True)
        assert path.read_bytes() == b'{"reviews":'
    else:
        result = run_review(ScriptedClient())
        if case == 'getter_error':
            result['text'] = 'Wrong source'
        else:
            del result['verification_audit']
        with pytest.raises((ValueError, KeyError)):
            _render(tmp_path, {'review': result})
        assert not list(tmp_path.glob('*.html'))
