"""Conservative recognition of complete operational response templates."""
from __future__ import annotations

import re
from xml.etree import ElementTree

from zotero_summarizer.api.errors import APIError


_DENIAL_HEADINGS = frozenset({
    'access denied', 'accès refusé', 'acceso denegado', 'zugriff verweigert',
    'доступ запрещен', 'доступ запрещён',
})
_EDGE_TEMPLATE = re.compile(
    r'(?P<header>[^\n]+)\n(?P<message>[^\n]+)\nReference #(?P<reference>[\w.]+)\n'
    r'https://errors\.edgesuite\.net/(?P=reference)', re.IGNORECASE,
)


def operational_template(text: str) -> str | None:
    """Recognize whole response envelopes, not words embedded in a paper."""
    normalized = '\n'.join(line.strip() for line in text.strip().splitlines() if line.strip())
    match = _EDGE_TEMPLATE.fullmatch(normalized)
    if match and match['header'].casefold() in _DENIAL_HEADINGS:
        return 'edge_denial_template'
    from zotero_summarizer.services.library._auth_envelope import authentication_envelope

    if authentication_envelope(normalized):
        return 'authentication_control_envelope'
    if not normalized.startswith('<'):
        return None
    try:
        root = ElementTree.fromstring(normalized)
    except ElementTree.ParseError:
        return None
    # An Error root plus request correlation is an operational API envelope.
    # No HTTP status or authentication cause is inferred from its message/code.
    if root.tag != 'Error':
        return None
    fields = {child.tag: (child.text or '').strip() for child in root}
    if all(fields.get(key) for key in ('Code', 'Message', 'RequestId')):
        return 'request_error_envelope'
    return None


def admit_render_sources(content: dict, pdf_content: dict) -> None:
    """Admit selected presentation independently from the optional PDF Q&A body."""
    selected = str(content.get('full_text') or '')
    if not selected.strip():
        # The renderer's existing audit owns empty-extraction errors and details.
        return
    admit_source(selected)
    if content is pdf_content:
        return
    body = str(pdf_content.get('full_text') or '')
    if body.strip() and operational_template(body) is None:
        content['qa_text'] = body
        content['render_sections'] = pdf_content.get('sections') or []
    else:
        content['qa_text'] = ''
        content['render_sections'] = []
        content['qa_diagnostic'] = {
            'code': 'source_unusable' if body.strip() else 'extraction_empty',
            'stage': 'source_admission', 'recovery': 'Provide an original paper PDF for Q&A',
        }


def admit_source(text: str) -> None:
    from zotero_summarizer.services.library._review_attempt import record

    record('original', text)
    if not text.strip():
        raise ValueError('Digest assessment requires non-empty source text')
    template = operational_template(text)
    if template:
        raise APIError(
            error='source_unusable', message='The selected source is an operational response, not a paper',
            status_code=422, details={
                'stage': 'source_admission', 'recovery': 'Provide an original paper PDF or TeX source',
                'template': template,
            },
        )
