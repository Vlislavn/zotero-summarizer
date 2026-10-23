"""Authored publication regressions; NOT RUN by the static-only source owner.

Native fixtures use scripted semantic verdicts, not scientific gold. The consumer
is the real write_outputs/audit publisher, never a substitute renderer or runner.
"""
import builtins
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock
from xml.etree import ElementTree as ET

import pytest

from tests import test_interpretation_binding as binding
from tests.test_interpretation_binding import C, _native_entry, config
from tests.test_interpretation_producer import _base, _card
from tests.test_paper_read_brief import CONTENT, DIGEST, GOALS, QUALITY
from tests.test_review_cache import _isolate_cache
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.library import (
    _digest_audit, _interpretation_view as iv, _paper_read_html as renderer,
    _review_cache, deep_review, quality_review,
)
from zotero_summarizer.services.library.review_eligibility import usable_review
from zotero_summarizer.services.library.review_fleet import propose

KEY = "HTML0049"


def _packet(tmp_path, config, **kwargs):
    entry = _native_entry(tmp_path, config, **kwargs)
    _review_cache._write_one(KEY, entry)
    packet = iv.current_review_view(KEY, interpretation_schema=C)
    assert packet is not None
    return packet


def _publish(tmp_path, packet, *, content=None, **kwargs):
    directory = tmp_path / "published"
    staging = directory / ".owned-staging"
    staging.mkdir(parents=True, exist_ok=True)
    result = renderer.write_outputs(directory, deepcopy(CONTENT) if content is None else content,
                                    staging=staging, interpretation_review=packet, **kwargs)
    html = Path(result["presentation_path"]).read_text(encoding="utf-8")
    assert Path(result["audit_path"]).parent == directory
    assert json.loads(Path(result["audit_path"]).read_text()) == result["audit"]
    assert result["audit"]["status"] == "passed" and result["audit"]["blocking"] == []
    return html, result


def _section(html):
    start = html.index('<section id="interpretation"')
    return ET.fromstring(html[start:html.index('</section>', start) + len('</section>')])


def _groups(section):
    return {group.find('h3').text: group for group in section.findall('div[@class="interpretation-group"]')}


def _rows(item):
    return {row.find('dt').text: row.find('dd') for row in item.findall('dl/div')}


def _text(element):
    return ''.join(element.itertext())


def _snapshot(directory):
    return {str(path.relative_to(directory)): path.read_bytes() for path in directory.rglob('*') if path.is_file()}


def test_real_native_packet_reaches_existing_audited_publisher_once(tmp_path, config, monkeypatch):
    entry = _native_entry(tmp_path, config, optional_passage=True)
    entry.update(quality=deepcopy(QUALITY), goal_summaries=deepcopy(GOALS), code_link={"found": False})
    _review_cache._write_one(KEY, entry)
    packet = iv.current_review_view(KEY, interpretation_schema=C)
    before = deepcopy(packet)
    cache_before = _review_cache._cache_path().read_bytes()
    audit = Mock(wraps=renderer._audit_presentation)
    forbidden = Mock(side_effect=AssertionError("The renderer must consume, not reread/requalify/regenerate"))
    monkeypatch.setattr(renderer, '_audit_presentation', audit)
    for owner, name in ((deep_review, 'get_current_review'), (_review_cache, '_read_all'),
                        (iv, 'current_review_view'), (iv, 'build_interpretation_view'),
                        (_digest_audit, 'qualify_interpretation'), (quality_review, 'assess_digest')):
        monkeypatch.setattr(owner, name, forbidden)
    html, result = _publish(tmp_path, packet)
    section = _section(html)
    groups = _groups(section)
    assert section.find('h2').text == 'Finding / Scope / Unproven'
    assert list(groups) == ['Finding', 'Scope', 'Unproven']
    item = packet['interpretation_view']['groups']['Finding'][0]
    finding = _rows(groups['Finding'].find('ol/li'))
    assert _text(finding['Referenced finding']) == packet['digest']['key_findings'][0]
    assert _text(finding['Rationale']) == item['rationale']
    assert _text(finding['Inference strength']) == item['inference_strength']
    assert groups['Scope'].find('ol/li/dl/div/dd') is not None
    assert packet['digest']['key_findings'][0] not in _text(groups['Scope'])
    assert packet['digest']['key_findings'][0] not in _text(groups['Unproven'])
    assert packet['digest']['methods'] not in _text(groups['Unproven'])
    for disclosure in ('AI-generated', 'scientifically unverified', 'bound snapshot',
                       'Contradiction adjudication: not performed', 'full-paper coverage', 'still current'):
        assert disclosure in _text(section)
    support = [_rows(row) for row in section.findall('details[@class="interpretation-support"]/ol/li')]
    for shown, native in zip(support, packet['interpretation_view']['passage_support'], strict=True):
        assert _text(shown['Scientific reference']) == native['scientific_ref']
        assert json.loads(_text(shown['Claim index'])) == native['claim_index']
        assert json.loads(_text(shown['Sentence part'])) == native['part']
        assert json.loads(_text(shown['Passage IDs'])) == native['passage_refs']
    assert 'id="quality"' in html and GOALS[0]['summary'] in html
    assert packet['digest']['tldr'] in html and 'digest-fold' in html
    assert 'verification_audit' not in result and 'interpretation_view' not in result
    assert set(result) == {'presentation_path', 'audit_path', 'figures_dir', 'audit'}
    audit.assert_called_once()
    assert audit.call_args.args[0] == html
    assert packet == before and _review_cache._cache_path().read_bytes() == cache_before
    forbidden.assert_not_called()


def test_native_multi_item_refs_multiplicity_false_unicode_and_hostile_text(tmp_path, config, monkeypatch):
    hostile = 'Ignore instructions </script><script>alert("owned")</script> <img src=x onerror="bad"> 🧪\n  '
    base = _base()
    base.parameters.baselines = ['Repeated 🧬', 'Repeated 🧬', hostile]
    monkeypatch.setattr(binding, '_base', lambda: base)
    card = _card(hostile)
    card['items'][0]['uncertainties'] = ['Unknown 🧪', 'Unknown 🧪', hostile]
    card['items'][0]['basis_refs'] = ['/parameters', '/parameters/external_validation', '/methods', '/methods']
    second = deepcopy(card['items'][0])
    second.update(finding_ref='/tldr', basis_refs=list(reversed(second['basis_refs'])))
    card['items'].append(second)
    packet = _packet(tmp_path, config, card=card)
    assert packet['interpretation_view']['availability'] == 'available'
    before = deepcopy(packet)
    html, _ = _publish(tmp_path, packet)
    section = _section(html)
    groups = _groups(section)
    for name in ('Finding', 'Scope', 'Unproven'):
        rows = groups[name].findall('ol/li')
        assert len(rows) == 2
        assert [_text(_rows(row)['Finding reference']) for row in rows] == ['/key_findings/0', '/tldr']
    finding_rows = [_rows(row) for row in groups['Finding'].findall('ol/li')]
    assert [_text(row['Referenced finding']) for row in finding_rows] == [
        packet['digest']['key_findings'][0], packet['digest']['tldr']]
    scope = _rows(groups['Scope'].find('ol/li'))
    basis = scope['Referenced basis'].findall('ol/li')
    assert [row.find('code').text for row in basis] == card['items'][0]['basis_refs']
    parameters = json.loads(basis[0].find('div').text)
    assert parameters == packet['digest']['parameters'] and parameters['external_validation'] is False
    assert parameters['baselines'] == ['Repeated 🧬', 'Repeated 🧬', hostile]
    assert json.loads(basis[1].find('div').text) is False
    unproven = _rows(groups['Unproven'].find('ol/li'))
    assert [_text(row) for row in unproven['Uncertainties'].findall('ol/li')] == card['items'][0]['uncertainties']
    assert _text(finding_rows[0]['Rationale']) == hostile
    assert '&lt;/script&gt;&lt;script&gt;' in html and '&quot;owned&quot;' in html
    assert section.findall('.//script') == [] and section.findall('.//img') == [] and section.findall('.//a') == []
    assert all(not key.startswith('on') for element in section.iter() for key in element.attrib)
    assert packet == before


def test_unavailable_reason_and_native_path_strings_are_escaped(tmp_path, config):
    # Supplemental trusted-consumer escaping, not native qualification of these
    # mutations: the actual qualifier supplies controlled machine reason codes.
    hostile = '</section><script>"hostile" & 🧪</script>'
    packet = _packet(tmp_path, config, missing=True)
    packet['interpretation_view']['reason'] = hostile
    packet['interpretation_qualification']['reason'] = hostile
    html, _ = _publish(tmp_path, packet)
    section = _section(html)
    assert hostile in _text(section) and section.findall('.//script') == []
    assert '&lt;script&gt;&quot;hostile&quot; &amp; 🧪&lt;/script&gt;' in html
    packet = _packet(tmp_path, config)
    packet['interpretation_view']['passage_support'][0]['scientific_ref'] = hostile
    html, _ = _publish(tmp_path, packet)
    rows = _rows(_section(html).find('details[@class="interpretation-support"]/ol/li'))
    assert _text(rows['Scientific reference']) == hostile
    assert _section(html).findall('.//script') == []


def test_plain_json_nested_zero_preservation_is_not_a_native_qualification_oracle(tmp_path, config):
    # Current PaperParameters has no numeric-zero field. This supplementary trusted
    # consumer-shape check is NOT a claim that this mutated packet is qualified.
    packet = _packet(tmp_path, config)
    packet['digest']['parameters']['consumer_shape_only'] = [0, False, {'🧪': [0, False, None, '', '', 'same', 'same']}]
    packet['interpretation_view']['groups']['Scope'][0]['basis_refs'] = ['/parameters']
    packet['interpretation_view']['groups']['Unproven'][0]['basis_refs'] = ['/parameters']
    html, _ = _publish(tmp_path, packet)
    scope = _rows(_groups(_section(html))['Scope'].find('ol/li'))
    shown = scope['Referenced basis'].find('ol/li/div').text
    assert json.loads(shown) == packet['digest']['parameters']
    assert '0' in shown and 'false' in shown and '🧪' in shown


@pytest.mark.parametrize('case', ['missing', 'malformed', 'unsupported', 'tampered', 'policy_changed'])
def test_native_optional_unavailability_publishes_base_not_raw_card(tmp_path, config, monkeypatch, case):
    sentinel = 'Optional-only retained raw rationale.'
    card = {'items': []} if case == 'malformed' else _card('Unreported 99999%.' if case == 'unsupported' else sentinel)
    entry = _native_entry(tmp_path, config, card=card, missing=case == 'missing')
    if case == 'tampered':
        entry['interpretation']['items'][0]['rationale'] += ' tampered'
    if case == 'policy_changed':
        policy = propose.apply_reading_policy

        def changed(*args):
            digest, raw, flags = policy(*args)
            digest = deepcopy(digest)
            digest['methods'] += ' Current policy change.'
            return digest, raw, flags

        monkeypatch.setattr(propose, 'apply_reading_policy', changed)
    _review_cache._write_one(KEY, entry)
    cache_before = _review_cache._cache_path().read_bytes()
    packet = iv.current_review_view(KEY, interpretation_schema=C)
    assert packet['interpretation_view']['availability'] == 'unavailable'
    assert 'interpretation' not in packet and 'verification_audit' not in packet
    assert usable_review(packet)
    html, _ = _publish(tmp_path, packet)
    section = _section(html)
    assert _groups(section) == {}
    assert packet['interpretation_view']['reason'] in _text(section)
    assert 'base review remains available' in _text(section)
    assert sentinel not in html and '99999%' not in html and 'tampered' not in html
    assert packet['digest']['tldr'] in html and 'digest-fold' in html
    assert _review_cache._cache_path().read_bytes() == cache_before


@pytest.mark.parametrize('case', ['absent', 'stale_base', 'source_changed', 'audit_tampered'])
def test_current_getter_none_never_selects_cached_raw_interpretation(tmp_path, config, case):
    if case != 'absent':
        entry = _native_entry(tmp_path, config, card=_card('Never revive this raw optional body.'))
        if case == 'stale_base':
            entry['digest']['tldr'] += '!'
        elif case == 'source_changed':
            Path(entry['review_identity']['source_path']).write_bytes(b'Changed owned source')
        else:
            entry['verification_audit']['source']['selected_text_sha256'] = 'a' * 64
        _review_cache._write_one(KEY, entry)
    packet = iv.current_review_view(KEY, interpretation_schema=C)
    assert packet is None
    html, _ = _publish(tmp_path, packet)
    assert 'id="interpretation"' not in html and 'Never revive' not in html
    assert 'No figures or review are available yet.' in html


@pytest.mark.parametrize('defect', ['raw', 'loose_view', 'empty_base', 'groups', 'refs', 'basis', 'reason', 'support', 'disclosure', 'resurrection'])
def test_malformed_packet_fails_before_audit_or_any_publication(tmp_path, config, monkeypatch, defect):
    packet = _packet(tmp_path, config)
    _, result = _publish(tmp_path, None, digest=DIGEST)
    old = _snapshot(Path(result['presentation_path']).parent)
    if defect == 'raw':
        packet.pop('interpretation_view')
    elif defect == 'loose_view':
        packet = packet['interpretation_view']
    elif defect == 'empty_base':
        packet['digest'] = {}
    elif defect == 'groups':
        packet['interpretation_view']['groups']['Scope'] = []
    elif defect in {'refs', 'basis'}:
        group = 'Finding' if defect == 'refs' else 'Scope'
        key = 'finding_ref' if defect == 'refs' else 'basis_refs'
        packet['interpretation_view']['groups'][group][0][key] = '/missing' if defect == 'refs' else ['/missing']
    elif defect == 'reason':
        packet['interpretation_view']['reason'] = None
    elif defect == 'support':
        packet['interpretation_view']['passage_support'][0]['part'] = False
    elif defect == 'disclosure':
        packet['interpretation_view']['scientific_verification'] = 'verified'
    else:
        packet['interpretation_view'].update(availability='unavailable', reason='Unavailable <script> 🧪')
        packet['interpretation_qualification'].update(status='unavailable', reason='Unavailable <script> 🧪')
    audit = Mock(side_effect=AssertionError('Malformed packets must fail before auditing/publication'))
    monkeypatch.setattr(renderer, '_audit_presentation', audit)
    with pytest.raises((ValueError, KeyError, TypeError)):
        _publish(tmp_path, packet)
    assert _snapshot(Path(result['presentation_path']).parent) == old
    audit.assert_not_called()


@pytest.mark.parametrize('key,value', [('digest', {}), ('quality', {}), ('goal_summaries', []), ('code_link', {}),
                                       ('digest', False), ('quality', 0)])
def test_every_non_none_mixed_projection_is_rejected_even_empty_false_or_zero(tmp_path, config, key, value):
    packet = _packet(tmp_path, config)
    before = deepcopy(packet)
    with pytest.raises(ValueError, match='mixed'):
        _publish(tmp_path, packet, **{key: value})
    assert packet == before
    assert not list((tmp_path / 'published').rglob('paper_*'))


def test_even_the_identical_snapshot_projection_is_ambiguous_mixing(tmp_path, config):
    packet = _packet(tmp_path, config)
    with pytest.raises(ValueError, match='mixed'):
        _publish(tmp_path, packet, digest=packet['digest'])
    assert not list((tmp_path / 'published').rglob('paper_*'))


@pytest.mark.parametrize('failure', ['blocking', 'write'])
def test_real_audit_and_staging_failure_preserve_packet_and_old_outputs(tmp_path, config, monkeypatch, failure):
    packet = _packet(tmp_path, config)
    _, result = _publish(tmp_path, None, digest=DIGEST)
    directory = Path(result['presentation_path']).parent
    before, saved = deepcopy(packet), _snapshot(directory)
    content = deepcopy(CONTENT)
    if failure == 'blocking':
        content['render_sections'] = []
        error = APIError
    else:
        write = Path.write_text

        def fail_audit(path, *args, **kwargs):
            if path.name == 'paper_audit.json':
                raise OSError('Owned staged audit write failure')
            return write(path, *args, **kwargs)

        monkeypatch.setattr(Path, 'write_text', fail_audit)
        error = OSError
    with pytest.raises(error) as caught:
        _publish(tmp_path, packet, content=content)
    if failure == 'blocking':
        assert caught.value.error == 'paper_audit_failed' and caught.value.details['audit']['blocking']
    # A failed staging write may leave a private staged HTML; no published bytes change.
    assert {name: data for name, data in _snapshot(directory).items() if not name.startswith('.owned-staging/')} == saved
    assert packet == before


@pytest.mark.parametrize('explicit_none', [False, True], ids=['omitted', 'none'])
def test_off_import_io_shape_and_legacy_output_preserved(tmp_path, config, monkeypatch, explicit_none):
    _packet(tmp_path, config)  # Stored C content cannot activate rendering.
    before_cache = _review_cache._cache_path().read_bytes()
    expected = renderer._render_presentation(CONTENT, DIGEST, QUALITY, GOALS, {'found': False})
    render = Mock(wraps=renderer._render_presentation)
    forbidden = Mock(side_effect=AssertionError('OFF must not read optional state or a file'))
    original_import = builtins.__import__

    def no_optional_import(name, globals=None, locals=None, fromlist=(), level=0):
        assert name not in {'zotero_summarizer.models.interpretation',
                            'zotero_summarizer.services.library._interpretation_html'}
        assert not {'_interpretation_html', '_interpretation_view'} & set(fromlist or ())
        return original_import(name, globals, locals, fromlist, level)

    directory = tmp_path / 'off'
    staging = directory / '.owned-staging'
    staging.mkdir(parents=True)
    with monkeypatch.context() as scoped:
        scoped.setattr(builtins, '__import__', no_optional_import)
        scoped.setattr(_review_cache, '_read_all', forbidden)
        scoped.setattr(renderer, '_render_presentation', render)
        scoped.setattr(Path, 'read_text', forbidden)
        scoped.setattr(Path, 'read_bytes', forbidden)
        scoped.setattr(renderer, 'now_iso_z', lambda: 'owned-fixed-audit-time')
        extra = {'interpretation_review': None} if explicit_none else {}
        result = renderer.write_outputs(directory, deepcopy(CONTENT), staging=staging, digest=deepcopy(DIGEST),
                                        quality=deepcopy(QUALITY), goal_summaries=deepcopy(GOALS),
                                        code_link={'found': False}, **extra)
    render.assert_called_once_with(CONTENT, DIGEST, QUALITY, GOALS, {'found': False})
    assert Path(result['presentation_path']).read_text() == expected
    assert set(result) == {'presentation_path', 'audit_path', 'figures_dir', 'audit'}
    assert json.loads(Path(result['audit_path']).read_text()) == result['audit']
    assert result['audit'] == {'status': 'passed', 'blocking': [], 'minor': [], 'checked_at': 'owned-fixed-audit-time'}
    assert 'id="interpretation"' not in expected and '.interpretation-review' not in expected
    assert _review_cache._cache_path().read_bytes() == before_cache
    forbidden.assert_not_called()


@pytest.mark.parametrize('optional_passage', [False, True], ids=['shared-passage', 'additional-passage'])
def test_native_trace_is_one_closed_disclosure_with_visible_groups_and_caveats(tmp_path, config, monkeypatch, optional_passage):
    packet = _packet(tmp_path, config, optional_passage=optional_passage)
    assert packet['interpretation_view']['availability'] == 'available'
    before = deepcopy(packet)
    cache = _review_cache._cache_path()
    cache_before = cache.read_bytes(), cache.stat().st_mtime_ns
    audit = Mock(wraps=renderer._audit_presentation)
    monkeypatch.setattr(renderer, '_audit_presentation', audit)

    html, result = _publish(tmp_path, packet)
    section = _section(html)
    traces = section.findall('.//details[@class="interpretation-support"]')
    assert len(traces) == 1, 'Native support must have one closed native disclosure'
    trace = traces[0]
    assert section.findall('.//details') == [trace]
    assert section.findall('.//*[@class="interpretation-support"]') == [trace]
    assert section.find('details') is trace and 'open' not in trace.attrib
    assert trace.attrib == {'class': 'interpretation-support'}
    summary = list(trace)[0]
    assert summary.tag == 'summary' and _text(summary) == 'Native support references'
    assert section.findall('.//summary') == [summary] and summary.attrib == {}
    assert trace.find('p').text == (
        'Passage IDs belong to the accompanying review snapshot. Original passages, pages and '
        'working paper anchors are not embedded here.')
    parents = {child: parent for parent in section.iter() for child in parent}
    groups = _groups(section)
    assert list(groups) == ['Finding', 'Scope', 'Unproven']
    assert section.findall('.//div[@class="interpretation-group"]') == list(groups.values())
    paragraphs = section.findall('p')
    assert [_text(paragraph) for paragraph in paragraphs] == [
        'AI-generated · scientifically unverified · bound snapshot.',
        'Contradiction adjudication: not performed. Native support is not scientific, human or author '
        'approval, causal warrant or full-paper coverage. This renderer does not authenticate the packet '
        'or guarantee that it is still current.',
    ]
    for visible in [*groups.values(), *paragraphs]:
        assert parents[visible] is section
    assert not any(node.tag in {'script', 'button', 'input', 'select', 'textarea', 'a', 'iframe'}
                   for node in section.iter())
    assert all(not key.startswith('on') and key not in {'role', 'tabindex', 'aria-expanded'}
               for node in section.iter() for key in node.attrib)

    expected = packet['interpretation_view']['passage_support']
    rows = trace.findall('ol/li')
    for row, native in zip(rows, expected, strict=True):
        shown = _rows(row)
        assert list(shown) == ['Claim index', 'Scientific reference', 'Sentence part', 'Passage IDs']
        actual = {
            'scientific_ref': _text(shown['Scientific reference']),
            'claim_index': json.loads(_text(shown['Claim index'])),
            'part': json.loads(_text(shown['Sentence part'])),
            'passage_refs': json.loads(_text(shown['Passage IDs'])),
        }
        assert json.dumps(actual, ensure_ascii=False) == json.dumps(
            {key: native[key] for key in actual}, ensure_ascii=False)
    assert len(section.findall('.//dt[.="Scientific reference"]')) == len(expected)
    assert packet == before and (cache.read_bytes(), cache.stat().st_mtime_ns) == cache_before
    audit.assert_called_once()
    assert audit.call_args.args[0] == html and result['audit']['status'] == 'passed'
    assert Path(result['presentation_path']).read_bytes() == html.encode('utf-8')


@pytest.mark.parametrize('state', ['absent', 'unavailable'])
def test_absent_or_unavailable_packet_has_no_native_trace_disclosure(tmp_path, config, state):
    qualified = _packet(tmp_path, config, missing=state == 'unavailable')
    packet = qualified if state == 'unavailable' else None
    before = deepcopy(qualified)
    cache = _review_cache._cache_path()
    cache_before = cache.read_bytes(), cache.stat().st_mtime_ns

    html, result = _publish(tmp_path, packet)
    assert '<details class="interpretation-support"' not in html
    assert 'Native support references' not in html
    if state == 'unavailable':
        section = _section(html)
        assert section.findall('.//details') == []
        assert section.findall('.//*[@class="interpretation-support"]') == []
        assert _text(section.find('dl/div/dd')) == qualified['interpretation_view']['reason']
        assert qualified['digest']['tldr'] in html and usable_review(qualified)
    else:
        assert 'id="interpretation"' not in html
        assert html == renderer._render_presentation(CONTENT)
    assert qualified == before and (cache.read_bytes(), cache.stat().st_mtime_ns) == cache_before
    assert result['audit']['status'] == 'passed' and result['audit']['blocking'] == []
