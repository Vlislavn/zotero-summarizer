"""Current comments are private context, not paper evidence or positive-interest terms."""
from html.parser import HTMLParser

from zotero_summarizer.integrations._zotero_write_tags import _owns_note
from zotero_summarizer.services.library._digest_audit import canonical_hash
from zotero_summarizer.services.zotero._notes import (
    DIGEST_NOTE_MARKER, USER_NOTE_MARKER, VERDICT_NOTE_MARKER, NOTE_PROVENANCE_NAMESPACE,
)


class _NoteText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, text):
        self.parts.append(text)

    def handle_starttag(self, tag, attrs):
        if tag in {'p', 'br', 'div', 'li', 'h1', 'h2', 'h3'}:
            self.parts.append('\n')


def _plain_text(markup):
    parser = _NoteText()
    parser.feed(markup)
    parser.close()
    return ''.join(parser.parts).strip()


def _record(key, source, ident, text, revision, authorship, **extra):
    return {'id': f'{key}:{source}:{ident}', 'source': source, 'text': text,
            'revision': revision, 'authorship': authorship, **extra}


def collect_comments(item_key, detail, context):
    """Retain short/negative RU/EN verbatim; native authorship is never inferred."""
    records = []
    if context is not None:
        manual = context['manual']
        if manual.get('value'):
            records.append(_record(item_key, 'app_note', manual['item_key'], manual['value'],
                                   manual['revision'], 'user'))
        verdict = context.get('verdict')
        if verdict and verdict.get('comment'):
            records.append(_record(item_key, 'human_verdict', verdict['item_key'], verdict['comment'],
                                   verdict['revision'], 'user', priority=verdict['value']))
    excluded = 0
    for note in (detail or {}).get('notes', []):
        markup = note.get('note') or ''
        markers = (DIGEST_NOTE_MARKER, USER_NOTE_MARKER, VERDICT_NOTE_MARKER,
                   f'{NOTE_PROVENANCE_NAMESPACE}:note_type=triage')
        if any(_owns_note(markup, marker) for marker in markers):
            excluded += 1
            continue
        text = _plain_text(markup)
        if text:
            records.append(_record(item_key, 'native_note', note.get('note_key'), text,
                                   note.get('date_modified'), 'uncertain'))
    for annotation in (detail or {}).get('annotations', []):
        if annotation.get('comment'):
            records.append(_record(item_key, 'annotation_comment', annotation.get('annotation_key'),
                                   annotation['comment'], annotation.get('date_modified'), 'uncertain',
                                   attachment_key=annotation.get('attachment_key'),
                                   page_label=annotation.get('page_label'), paper_excerpt=annotation.get('text', '')))
    native_available = detail is not None and detail.get('native_comments_available', True)
    availability = ('available' if records else 'absent') if context is not None and native_available else 'partial'
    if context is None and not native_available:
        availability = 'unavailable'
    return {'availability': availability, 'app_available': context is not None,
            'native_available': bool(native_available), 'records': records,
            'excluded_owned_notes': excluded, 'used_for_ranking': False, 'used_context_ids': [],
            'fingerprint': canonical_hash({'context': context, 'detail_notes': (detail or {}).get('notes'),
                                           'annotations': (detail or {}).get('annotations')})}
