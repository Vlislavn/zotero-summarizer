"""One qualified projection shared by experimental briefs and native-note HTML."""
from html import escape

from zotero_summarizer.services.library._digest_audit import canonical_hash, digest_contract_payload
from zotero_summarizer.services.library.interpretation import qualify


def section(record, digest, *, content=None):
    card = qualify(record, strict=False)
    if content is not None and content != record['content']:
        raise ValueError('Renderer content differs from the qualified record')
    if canonical_hash(digest_contract_payload(digest)) != canonical_hash(digest_contract_payload(record['digest'])):
        raise ValueError('Renderer digest differs from the qualified experiment')
    if card['status'] == 'disabled':
        return ''
    if card['status'] != 'accepted':
        return '<section><h2>Interpretation</h2><p>AI draft unavailable: ' + escape(card['status']) + '</p></section>'
    groups = {'Finding': [], 'Scope': [], 'Unproven': []}
    for item in card['items']:
        label = f"Finding {item['finding_index'] + 1}: "
        groups['Finding'].append(label + item['finding'])
        groups['Scope'].append(label + '; '.join((item['studied_context'], item['inference_strength'], item['rationale'])))
        groups['Unproven'].extend(label + value for value in item['uncertainties'] + item['unsupported_extrapolations'])
    source = card['source']
    scope = f"{source['scope']}: {source['selected_characters']}/{source['original_characters']} extracted characters"
    body = ''.join('<h3>' + title + '</h3><ul>' + ''.join('<li>' + escape(value) + '</li>' for value in values)
                   + '</ul>' for title, values in groups.items())
    return ('<section data-interpretation="ai-draft"><h2>Finding / Scope / Unproven</h2>'
            '<p>AI draft; checked against selected original passages, not author metadata or independent endorsement. '
            + escape(scope) + '. Complete-paper coverage is not established.</p>' + body + '</section>')
