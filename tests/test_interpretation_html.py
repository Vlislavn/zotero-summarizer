"""Actual shared renderer/publisher controls; no legacy packet/view facade."""
import builtins
import json
from copy import deepcopy
from html import escape
from pathlib import Path
from unittest.mock import Mock
from xml.etree import ElementTree as ET

import pytest

from tests._interpretation_experiment import CARD, PAYLOAD, TEXT, ScriptedClient, inputs, run_review
from tests.test_review_cache import _isolate_cache
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models import PaperDigest
from zotero_summarizer.services.library import _interpretation_html as shared, _paper_read_html as renderer, _review_cache
from zotero_summarizer.services.zotero._notes import build_digest_note_html, DIGEST_NOTE_MARKER


def _section(record):
    return ET.fromstring(shared.section(record, record['digest']))


def _text(element):
    return ''.join(element.itertext())


def _rows(element):
    return {row.find('dt').text: row.find('dd') for row in element.findall('dl/div')}


def _publish(tmp_path, record, **kwargs):
    directory, staging = tmp_path / 'published', tmp_path / 'staging'
    directory.mkdir(exist_ok=True)
    staging.mkdir(exist_ok=True)
    return renderer.write_outputs(directory, record['content'], staging=staging,
                                  digest=record['digest'], interpretation=record, **kwargs)


def test_real_native_packet_reaches_existing_audited_publisher_once(tmp_path, monkeypatch):
    record = run_review(ScriptedClient())
    saved = deepcopy(record)
    audit = Mock(wraps=renderer._audit_presentation)
    monkeypatch.setattr(renderer, '_audit_presentation', audit)
    output = _publish(tmp_path, record)
    html = Path(output['presentation_path']).read_text()
    assert shared.section(record, record['digest']) in html
    assert html == audit.call_args.args[0] and audit.call_count == 1
    assert json.loads(Path(output['audit_path']).read_text()) == output['audit']
    assert output['audit']['status'] == 'passed' and output['audit']['blocking'] == []
    assert set(output) == {'presentation_path', 'audit_path', 'figures_dir', 'audit'}
    assert record == saved


def test_native_multi_item_refs_multiplicity_false_unicode_and_hostile_text(tmp_path):
    hostile = 'Ignore instructions </script><script>alert("owned")</script> <img src=x onerror="bad"> 🧪'
    findings = [CARD['finding'], 'The design was observational']
    items = [dict(CARD, rationale=hostile, uncertainties=['Unknown 🧪', 'Unknown 🧪', hostile]),
             dict(CARD, finding_index=1, finding=findings[1], basis_refs=['parameters', 'methods'])]
    payload = PAYLOAD | {'key_findings': findings, 'parameters': {'external_validation': False,
                           'baselines': ['Repeated 🧬', 'Repeated 🧬', hostile]}, 'interpretation': items}
    record = run_review(ScriptedClient(cards=False, responses=[payload]))
    saved = deepcopy(record)
    section = _section(record)
    groups = section.findall('div[@class="interpretation-group"]')
    assert [group.find('h3').text for group in groups] == ['Finding', 'Scope', 'Unproven']
    assert [len(group.findall('ol/li')) for group in groups] == [2, 2, 2]
    for group in groups:
        assert [_text(_rows(row)['Finding index']) for row in group.findall('ol/li')] == ['0', '1']
    finding = _rows(groups[0].find('ol/li'))
    assert _text(finding['Referenced finding']) == findings[0] and _text(finding['Rationale']) == hostile
    scope = _rows(groups[1].find('ol/li'))
    basis = scope['Referenced basis'].findall('ol/li')
    assert [row.find('code').text for row in basis] == CARD['basis_refs']
    parameters = json.loads(basis[1].find('pre').text)
    assert parameters['external_validation'] is False and parameters['baselines'] == payload['parameters']['baselines']
    unproven = _rows(groups[2].find('ol/li'))
    assert [_text(row) for row in unproven['Uncertainties'].findall('ol/li')] == items[0]['uncertainties']
    assert section.findall('.//script') == section.findall('.//img') == section.findall('.//a') == []
    assert all(not key.startswith('on') for element in section.iter() for key in element.attrib)
    assert escape(hostile) in shared.section(record, record['digest'])
    _publish(tmp_path, record)
    assert record == saved


def test_unavailable_reason_and_native_path_strings_are_escaped():
    hostile = '</section><script>"hostile" & 🧪</script>'
    record = run_review(ScriptedClient())
    record['interpretation'] = {'status': hostile}
    html = shared.section(record, record['digest'])
    assert 'proof_unavailable' in html and hostile not in html
    # Path disclosure derives from qualified claims, never supplied HTML.
    record = run_review(ScriptedClient(cards=False, responses=[PAYLOAD | {'interpretation': [dict(CARD, rationale=hostile)]}]))
    assert escape(hostile) in shared.section(record, record['digest'])
    assert _section(record).findall('.//script') == []


def test_plain_json_nested_zero_preservation_is_not_a_native_qualification_oracle():
    paper, _, _ = inputs()
    paper['text'] += ' The sample size was 0.'
    paper['content']['full_text'] = paper['text']
    payload = PAYLOAD | {'parameters': {'sample_size': '0', 'external_validation': False}, 'interpretation': [CARD]}
    record = run_review(ScriptedClient(cards=False, responses=[payload]), paper=paper)
    scope = _section(record).findall('div')[1]
    parameters = _rows(scope.find('ol/li'))['Referenced basis'].findall('ol/li')[1]
    assert json.loads(parameters.find('pre').text)['external_validation'] is False
    assert json.loads(parameters.find('pre').text)['sample_size'] == '0'
    source = json.loads(_section(record).find('details/pre').text)
    assert type(source['locations'][0]['original_span']['start']) is int
    assert source['locations'][0]['original_span']['start'] == 0


@pytest.mark.parametrize('case', ['missing', 'malformed', 'unsupported', 'tampered', 'policy_changed'])
def test_native_optional_unavailability_publishes_base_not_raw_card(tmp_path, case):
    card = None if case == 'missing' else [] if case == 'malformed' else [dict(CARD, rationale='Unreported 99999%')] if case == 'unsupported' else [CARD]
    record = run_review(ScriptedClient(cards=False, responses=[PAYLOAD | {'interpretation': card}]))
    if case in {'tampered', 'policy_changed'}:
        record['interpretation']['items'][0]['rationale'] = 'Never revive this optional body'
    output = _publish(tmp_path, record)
    html = Path(output['presentation_path']).read_text()
    assert record['digest']['tldr'] in html and 'base review remains available' in html
    assert 'Never revive' not in html and '99999%' not in html and 'Native support references' not in html


@pytest.mark.parametrize('case', ['absent', 'stale_base', 'source_changed', 'audit_tampered'])
def test_current_getter_none_never_selects_cached_raw_interpretation(tmp_path, case):
    if case != 'absent':
        record = run_review(ScriptedClient())
        if case == 'stale_base':
            record['digest']['tldr'] += '!'
        elif case == 'source_changed':
            record['text'] += ' Changed source'
        else:
            record['verification_audit']['source']['selected_text_sha256'] = 'a' * 64
        _review_cache._write_one('LIBR0038', record)
    assert _review_cache.get_current_review('LIBR0038') is None
    assert _review_cache.get_cached_review('LIBR0038') is None
    html = renderer._render_presentation({'title': 'Owned source', 'full_text': TEXT})
    assert 'data-interpretation' not in html and 'No figures or review are available yet.' in html


@pytest.mark.parametrize('defect', ['raw', 'loose_view', 'empty_base', 'source', 'contract'])
def test_malformed_packet_fails_before_audit_or_any_publication(tmp_path, monkeypatch, defect):
    record = run_review(ScriptedClient())
    directory = tmp_path / 'published'
    directory.mkdir()
    old = directory / 'paper_presentation.html'
    old.write_text('Previous accepted bytes')
    if defect == 'raw':
        del record['verification_audit']
    elif defect == 'loose_view':
        record = record['interpretation']
    elif defect == 'empty_base':
        record['digest'] = {}
    elif defect == 'source':
        record['text'] = 'Another paper'
    else:
        record['verification_audit']['contract'] = 'unknown'
    audit = Mock(side_effect=AssertionError('invalid base must fail before audit'))
    monkeypatch.setattr(renderer, '_audit_presentation', audit)
    with pytest.raises((ValueError, KeyError, TypeError)):
        _publish(tmp_path, record)
    assert old.read_text() == 'Previous accepted bytes'
    audit.assert_not_called()


@pytest.mark.parametrize('value', [{}, False, 0, {'tldr': 'Different current digest'}])
def test_every_non_none_mixed_projection_is_rejected_even_empty_false_or_zero(tmp_path, value):
    record = run_review(ScriptedClient())
    before = deepcopy(record)
    with pytest.raises((ValueError, TypeError, AttributeError)):
        renderer._render_presentation(record['content'], value, interpretation=record)
    assert record == before


def test_even_the_identical_snapshot_projection_is_ambiguous_mixing():
    # Canonical renderers require the exact digest alongside the explicit record; old blanket mixing refusal is a gap.
    record = run_review(ScriptedClient())
    before = deepcopy(record)
    assert shared.section(record, record['digest']) in renderer._render_presentation(
        record['content'], record['digest'], interpretation=record)
    assert record == before


@pytest.mark.parametrize('failure', ['blocking', 'write'])
def test_real_audit_and_staging_failure_preserve_packet_and_old_outputs(tmp_path, monkeypatch, failure):
    record = run_review(ScriptedClient())
    output = _publish(tmp_path, record)
    old = {name: Path(output[name]).read_bytes() for name in ['presentation_path', 'audit_path']}
    before = deepcopy(record)
    if failure == 'blocking':
        original = renderer._audit_presentation

        def blocking(html, content, figures):
            return original(html, content | {'full_text': '', 'render_sections': []}, figures)

        monkeypatch.setattr(renderer, '_audit_presentation', blocking)
    else:
        original_write = Path.write_text

        def fail_audit(path, *args, **kwargs):
            if path.name == 'paper_audit.json':
                raise OSError('staged audit failure')
            return original_write(path, *args, **kwargs)

        monkeypatch.setattr(Path, 'write_text', fail_audit)
    with pytest.raises(APIError if failure == 'blocking' else OSError):
        _publish(tmp_path, record)
    assert {name: Path(output[name]).read_bytes() for name in old} == old and record == before


@pytest.mark.parametrize('explicit_none', [False, True])
def test_off_import_io_shape_and_legacy_output_preserved(tmp_path, monkeypatch, explicit_none):
    record = run_review(ScriptedClient())
    expected = renderer._render_presentation(record['content'], record['digest'])
    original_import = builtins.__import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        assert 'interpretation' not in name and '_interpretation_html' not in (fromlist or ())
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, '__import__', guarded)
    actual = renderer._render_presentation(record['content'], record['digest'], **({'interpretation': None} if explicit_none else {}))
    assert actual == expected and 'data-interpretation' not in actual


@pytest.mark.parametrize('optional_passage', [False, True])
def test_native_trace_is_one_closed_disclosure_with_visible_groups_and_caveats(tmp_path, optional_passage):
    class SecondPassage(ScriptedClient):
        def pydantic_prompt(self, *, prompt, **kwargs):
            result = super().pydantic_prompt(prompt=prompt, **kwargs)
            if optional_passage and 'interpretation[' in prompt:
                for check in result['checks']:
                    check['evidence'] = [1]
            return result

    record = run_review(SecondPassage())
    before = deepcopy(record)
    section = _section(record)
    traces = section.findall('.//details')
    assert len(traces) == 1 and traces[0].attrib == {'class': 'interpretation-support'}
    assert traces[0].find('summary').text == 'Native support references'
    assert [group.find('h3').text for group in section.findall('div')] == ['Finding', 'Scope', 'Unproven']
    rows = traces[0].findall('ol/li')
    parts = {}
    checks = {row['index']: row for row in record['interpretation']['checks']['checks']}
    for index, (row, claim) in enumerate(zip(rows, record['interpretation']['claims'], strict=True)):
        shown = _rows(row)
        path = claim.partition(':')[0]
        assert list(shown) == ['Claim index', 'Scientific reference', 'Sentence part', 'Passage IDs']
        assert json.loads(_text(shown['Claim index'])) == index
        assert _text(shown['Scientific reference']) == path
        assert json.loads(_text(shown['Sentence part'])) == parts.get(path, 0)
        parts[path] = parts.get(path, 0) + 1
        assert json.loads(_text(shown['Passage IDs'])) == checks[index]['evidence']
    assert len(rows) == len(record['interpretation']['claims'])
    assert json.loads(traces[0].find('pre').text) == record['interpretation']['source']
    for caveat in ['Complete-paper coverage is not established', 'Contradiction adjudication: not performed',
                   'not scientific, human or author approval', 'still current']:
        assert caveat in _text(section)
    native = build_digest_note_html(PaperDigest.model_validate(record['digest']), interpretation=record)
    assert native.endswith(f'{DIGEST_NOTE_MARKER};version=1</em></p>')
    assert native.count('Native support references') == 1
    _publish(tmp_path, record)
    assert record == before


@pytest.mark.parametrize('state', ['absent', 'unavailable'])
def test_absent_or_unavailable_packet_has_no_native_trace_disclosure(state):
    record = run_review(ScriptedClient(cards=False))
    html = renderer._render_presentation(record['content'], record['digest'],
                                         interpretation=record if state == 'unavailable' else None)
    assert 'Native support references' not in html and 'interpretation-support' not in html
    assert ('AI draft unavailable' in html) is (state == 'unavailable')
