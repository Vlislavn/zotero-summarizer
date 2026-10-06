"""Read a diagnostic source without acquisition, model construction or state writes."""
from __future__ import annotations

import json

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.library.paper_render import _state_path


def read_source(settings, item_key):
    path = _state_path(item_key, root=settings.data_dir / 'paper_render')
    if not path.exists():
        raise APIError('paper_state_missing', 'Build the paper brief before diagnostic review', 404,
                       {'stage': 'source_admission', 'recovery': 'Build the paper brief'})
    state = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(state, dict):
        raise APIError('paper_state_invalid', 'Paper state must be an object', 422,
                       {'stage': 'source_admission', 'recovery': 'Rebuild the paper brief'})
    body = state.get('qa_text')
    scope = 'cached_qa_text'
    if body is None:
        body = state.get('full_text', '')
        scope = 'legacy_full_text_unverified'
    if not isinstance(body, str):
        raise APIError('paper_state_invalid', 'Paper source text must be a string', 422,
                       {'stage': 'source_admission', 'recovery': 'Rebuild the paper brief'})
    return path, state, body.strip(), str(state.get('title') or item_key), scope
