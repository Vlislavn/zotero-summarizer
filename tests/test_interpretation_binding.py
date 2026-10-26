"""Actual experiment qualification and ordinary-reader isolation, without an old C cache API."""
import builtins
import json
from copy import deepcopy
from unittest.mock import Mock

import pytest

from tests._interpretation_experiment import CARD, PAYLOAD, TEXT, ScriptedClient, run_review
from tests.test_review_cache import _isolate_cache
from zotero_summarizer.services.library import _review_cache, interpretation
from zotero_summarizer.services.library._digest_audit import canonical_hash
from zotero_summarizer.services.library._interpretation_trace import Trace
from zotero_summarizer.services.faithbench._corpus import chunk_text


def test_verification_captures_optional_native_evidence_without_changing_base_oracles():
    result = run_review(ScriptedClient())
    card, audit = interpretation.qualify(result), result['verification_audit']
    assert card['origin'] == 'ai_draft' and card['items'] == [CARD]
    assert {claim['field'] for claim in audit['claims']} == {'tldr', 'key_findings'}
    assert 'interpretation_evidence' not in audit and 'interpretation' not in result['digest']
    assert any('methods:' in claim for claim in card['claims'])
    assert any('parameters.external_validation: False' == claim for claim in card['claims'])
    selected = interpretation.selected_original(result)
    for passage in audit['passages']:
        span = passage['selected_span']
        assert passage['text'] == selected[span['start']:span['end']]
        for fragment in passage['original_fragments']:
            origin = fragment['original_span']
            if origin is not None:
                assert fragment['text'] == TEXT[origin['start']:origin['end']]


@pytest.mark.parametrize('field', ['finding', 'rationale', 'studied_context'])
def test_normalized_generation_and_json_roundtrip_remain_qualified(field):
    draft = dict(CARD)
    draft[field] = '  ' + draft[field] + '  '
    result = run_review(ScriptedClient(cards=False, responses=[PAYLOAD | {'interpretation': [draft]}]))
    assert result['interpretation']['items'] == [CARD]
    assert interpretation.qualify(json.loads(json.dumps(result)))['status'] == 'accepted'


def test_cache_write_to_current_c_read_qualifies_whole_finding_and_basis(tmp_path):
    # Explicit immutable experiment publication/reopen, never an ordinary-cache opt-in.
    from tests._interpretation_cohort import cohort
    from zotero_summarizer.services.faithbench._interpretation import execute, read_run
    from zotero_summarizer.services.faithbench._interpretation_inputs import inspect

    settings, path, _, config, profiles = cohort(tmp_path)
    root = execute(settings, inspect(settings, path), config, profiles, lambda role, profile: ScriptedClient())
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    _, records = read_run(root)
    for row in (row for row in records if row['assignment']['condition'] == 'C'):
        result = row['review']
        assert interpretation.qualify(result)['items'] == [CARD]
        assert result['scientific_call'] in row['calls']
        assert all(q['status'] == 'returned' for q in row['questions'])
        directory = root / row['assignment']['directory']
        assert 'Native support references' in (directory / 'zotero-note.html').read_text()
        assert json.loads((directory / 'paper_audit.json').read_text())['status'] == 'passed'
    assert {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()} == before


def test_one_readonly_raw_snapshot_survives_concurrent_file_replacement(tmp_path, monkeypatch):
    from pathlib import Path
    from tests._interpretation_cohort import cohort
    from zotero_summarizer.services.faithbench._interpretation import execute, read_run
    from zotero_summarizer.services.faithbench._interpretation_inputs import inspect

    settings, recipe, _, config, profiles = cohort(tmp_path)
    root = execute(settings, inspect(settings, recipe), config, profiles, lambda role, profile: ScriptedClient())
    target = next(p for p in sorted(root.glob('*/record.json'))
                  if json.loads(p.read_bytes())['assignment']['condition'] == 'C')
    original_bytes = target.read_bytes()
    original = json.loads(original_bytes)
    replacement = deepcopy(original) | {'seconds': original['seconds'] + 1}
    actual_read, reads = Path.read_bytes, []

    def replace_after_read(path):
        raw = actual_read(path)
        if path == target:
            reads.append(raw)
            if len(reads) == 1:
                target.write_text(json.dumps(replacement))  # replacement after the verified byte read
        return raw

    monkeypatch.setattr(Path, 'read_bytes', replace_after_read)
    _, records = read_run(root)
    captured = next(row for row in records if row['assignment'] == original['assignment'])
    assert captured == original
    assert reads == [original_bytes]
    assert json.loads(target.read_text()) == replacement
    assert interpretation.qualify(captured['review'])['status'] == 'accepted'


def test_policy_runs_after_raw_audit_and_cannot_falsely_stale_it(monkeypatch):
    from zotero_summarizer.services.library import _interpretation_html, _paper_read_html, _paper_read_brief
    result = run_review(ScriptedClient())
    saved = deepcopy(result)
    events = []
    section, brief = _interpretation_html.section, _paper_read_brief.brief_html

    def qualify_first(*args, **kwargs):
        events.append('raw')
        return section(*args, **kwargs)

    def project_second(*args, **kwargs):
        assert events == ['raw']
        events.append('presentation')
        return brief(*args, **kwargs)

    monkeypatch.setattr(_interpretation_html, 'section', qualify_first)
    monkeypatch.setattr(_paper_read_brief, 'brief_html', project_second)
    _paper_read_html._render_presentation(result['content'], result['digest'], interpretation=result)
    assert events == ['raw', 'presentation'] and result == saved
    assert interpretation.qualify(result)['status'] == 'accepted'


@pytest.mark.parametrize('mutation', ['character', 'whitespace', 'number', 'reorder', 'basis', 'false_basis', 'index'])
def test_exact_current_finding_basis_and_index_changes_withhold_card_not_valid_base(mutation):
    result = run_review(ScriptedClient())
    card = result['interpretation']
    item = card['items'][0]
    if mutation in {'character', 'whitespace', 'number'}:
        item['finding'] += {'character': '!', 'whitespace': ' ', 'number': ' 1'}[mutation]
    elif mutation == 'index':
        item['finding_index'] = 1
    elif mutation == 'reorder':
        card['claims'].reverse()
    elif mutation == 'basis':
        item['basis_refs'] = ['limitations']
    else:
        card['claims'] = [claim.replace('False', '0') for claim in card['claims']]
    assert interpretation.qualify(result, strict=False) == {'status': 'proof_unavailable'}
    assert interpretation.selected_original(result) == TEXT
    assert result['digest']['key_findings'] == PAYLOAD['key_findings']


@pytest.mark.parametrize('mutation', ['rationale', 'rationale_whitespace', 'native_check', 'origin', 'missing'])
def test_payload_only_tamper_cannot_leak_through_nested_audit_and_preserves_base(mutation):
    from zotero_summarizer.services.library._interpretation_html import section
    result = run_review(ScriptedClient())
    if mutation == 'rationale':
        result['interpretation']['items'][0]['rationale'] = 'Untrusted replacement'
    elif mutation == 'rationale_whitespace':
        result['interpretation']['items'][0]['rationale'] += ' '
    elif mutation == 'native_check':
        result['interpretation']['checks']['checks'][0]['evidence'] = [99]
    elif mutation == 'origin':
        result['interpretation']['origin'] = 'author_approved'
    else:
        del result['interpretation']
    assert interpretation.selected_original(result) == TEXT
    assert interpretation.qualify(result, strict=False) == {'status': 'proof_unavailable'}
    html = section(result, result['digest'])
    assert 'Untrusted replacement' not in html and 'Native support references' not in html
    assert 'proof_unavailable' in html


@pytest.mark.parametrize('card', [None, [], [dict(CARD, rationale='Unreported 99999%')], [CARD]])
def test_missing_malformed_rejected_or_unverified_optional_preserves_only_valid_base(card):
    result = run_review(ScriptedClient(cards=False, responses=[PAYLOAD | {'interpretation': card}]))
    if card == [CARD]:
        del result['scientific_call']
    assert interpretation.qualify(result, strict=False)['status'] != 'accepted'
    assert interpretation.selected_original(result) == TEXT


@pytest.mark.parametrize('component', ['base', 'audit', 'binding', 'optional_passage', 'source'])
def test_base_artifact_audit_binding_or_source_tamper_is_not_card_only_fallback(component):
    result = run_review(ScriptedClient())
    if component == 'base':
        result['digest']['tldr'] += '!'
    elif component == 'audit':
        result['verification_audit']['source']['selected_text_sha256'] = 'a' * 64
    elif component == 'binding':
        result['scientific_call']['scientific_binding'] = 'a' * 64
    elif component == 'optional_passage':
        result['interpretation']['checks']['checks'][0]['evidence'] = [1]
    else:
        result['text'] += ' Changed source'
    with pytest.raises((ValueError, KeyError)):
        interpretation.qualify(result)
    if component in {'base', 'audit', 'source'}:
        with pytest.raises(ValueError):
            interpretation.qualify(result, strict=False)


@pytest.mark.parametrize('defect', ['missing_claim', 'invalid_evidence', 'invalid_digest', 'bad_contract'])
def test_malformed_base_audit_raises_instead_of_optional_failure(defect):
    result = run_review(ScriptedClient())
    audit = result['verification_audit']
    if defect == 'missing_claim':
        audit['claims'] = audit['claims'][1:]
    elif defect == 'invalid_evidence':
        audit['claims'][0]['evidence'] = [999]
    elif defect == 'invalid_digest':
        result['digest']['soundness'] = 0
    else:
        audit['contract'] = 'unknown'
    with pytest.raises(ValueError):
        interpretation.qualify(result, strict=False)


@pytest.mark.parametrize('corrupt', [b'{"reviews":', b'{"reviews": []}', b'\xff'])
def test_c_current_read_is_readonly_on_corrupt_cache(tmp_path, corrupt):
    path = _review_cache._cache_path()
    path.write_bytes(corrupt)
    before = path.stat().st_mtime_ns
    with pytest.raises(ValueError, match='deep-review cache'):
        _review_cache._read_all(readonly=True)
    assert path.read_bytes() == corrupt and path.stat().st_mtime_ns == before
    assert list(tmp_path.iterdir()) == [path]


def test_off_stored_c_is_not_a_schema_import_or_optional_output_request(monkeypatch):
    result = run_review(ScriptedClient())
    _review_cache._write_one('LIBR0038', result)
    before = _review_cache._cache_path().read_bytes()
    no_work = Mock(side_effect=AssertionError('ordinary refusal must precede optional qualification or policy'))
    original_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert 'interpretation' not in name
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', guarded)
    monkeypatch.setattr(interpretation, 'qualify', no_work)
    monkeypatch.setattr('zotero_summarizer.services.library.review_fleet.propose.project_review', no_work)
    assert _review_cache.get_cached_review('LIBR0038') is None
    assert _review_cache.get_current_review('LIBR0038') is None
    assert not _review_cache.review_is_current(result, 'LIBR0038')
    exported = _review_cache.export_review_audit('LIBR0038')
    assert exported == {'item_key': 'LIBR0038', 'digest': None, 'audit_status': 'unavailable',
                        'reason': 'review_condition_mismatch', 'verification_audit': None, 'retained_evidence': None}
    assert _review_cache._cache_path().read_bytes() == before
    no_work.assert_not_called()
