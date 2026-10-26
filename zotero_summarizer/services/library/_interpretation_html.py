"""One qualified projection shared by experimental briefs and native-note HTML."""
import json
from html import escape

from zotero_summarizer.services.library._digest_audit import canonical_hash, digest_contract_payload
from zotero_summarizer.services.library.interpretation import qualify


def _rows(values):
    return '<dl>' + ''.join('<div><dt>' + escape(label) + '</dt><dd>' + value + '</dd></div>'
                           for label, value in values) + '</dl>'


def _values(values):
    return '<ol>' + ''.join('<li>' + escape(value) + '</li>' for value in values) + '</ol>'


def _json_html(value):
    return '<pre style="white-space:pre-wrap;overflow-wrap:anywhere">' + escape(json.dumps(value, ensure_ascii=False)) + '</pre>'


def _support(card):
    parts, rows = {}, []
    checks = {row['index']: row for row in card['checks']['checks']}
    for index, claim in enumerate(card['claims']):
        path = claim.partition(':')[0]
        part = parts.get(path, 0)
        parts[path] = part + 1
        rows.append('<li>' + _rows([
            ('Claim index', str(index)), ('Scientific reference', escape(path)),
            ('Sentence part', str(part)), ('Passage IDs', escape(json.dumps(checks[index]['evidence']))),
        ]) + '</li>')
    return ('<details class="interpretation-support"><summary>Native support references</summary>'
            '<p>Passage IDs belong to the selected original extraction in the accompanying experiment record. '
            'Pages and working paper anchors are not established.</p><ol>' + ''.join(rows) + '</ol>'
            '<p>Source selection</p>' + _json_html(card['source']) + '</details>')


def section(record, digest, *, content=None):
    card = qualify(record, strict=False)
    if content is not None and content != record['content']:
        raise ValueError('Renderer content differs from the qualified record')
    if canonical_hash(digest_contract_payload(digest)) != canonical_hash(digest_contract_payload(record['digest'])):
        raise ValueError('Renderer digest differs from the qualified experiment')
    if card['status'] == 'disabled':
        return ''
    if card['status'] != 'accepted':
        return ('<section><h2>Interpretation</h2><p>AI draft unavailable: ' + escape(card['status'])
                + '</p><p>The independently qualified base review remains available.</p></section>')
    groups = {'Finding': [], 'Scope': [], 'Unproven': []}
    for item in card['items']:
        reference = ('Finding index', str(item['finding_index']))
        groups['Finding'].append(_rows([
            reference, ('Referenced finding', escape(item['finding'])),
            ('Inference strength', escape(item['inference_strength'])), ('Rationale', escape(item['rationale'])),
        ]))
        basis = '<ol>' + ''.join('<li><code>' + escape(ref) + '</code>'
                                + _json_html(record['digest'][ref]) + '</li>' for ref in item['basis_refs']) + '</ol>'
        groups['Scope'].append(_rows([
            reference, ('Studied context', escape(item['studied_context'])), ('Referenced basis', basis),
        ]))
        groups['Unproven'].append(_rows([
            reference, ('Uncertainties', _values(item['uncertainties'])),
            ('Unsupported extrapolations', _values(item['unsupported_extrapolations'])),
        ]))
    source = card['source']
    scope = f"{source['scope']}: {source['selected_characters']}/{source['original_characters']} extracted characters"
    body = ''.join('<div class="interpretation-group"><h3>' + title + '</h3><ol>'
                   + ''.join('<li>' + value + '</li>' for value in values) + '</ol></div>'
                   for title, values in groups.items())
    return ('<section data-interpretation="ai-draft" style="overflow-wrap:anywhere"><h2>Finding / Scope / Unproven</h2>'
            '<p>AI draft; checked against selected original passages, not author metadata or independent endorsement. '
            + escape(scope) + '. Complete-paper coverage is not established. Contradiction adjudication: not performed. '
            'Retained support is not scientific, human or author approval, causal warrant, authentication or a guarantee '
            'that the record is still current.</p>' + body + _support(card) + '</section>')
