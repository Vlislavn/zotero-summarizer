"""Retain accepted existing checks; original extraction is the coordinate authority."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

CONTRACT = 'digest-verification-audit-v1'


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def contract_hashes() -> dict[str, str]:
    root = Path(__file__).parents[2]
    sources = {
        'extraction': ('integrations/pdf.py', 'services/library/_review_text.py',
                       'services/faithbench/_corpus.py', 'services/faithbench/_constants.py',
                       'storage/corpus_bm25.py'),
        'verifier': ('services/library/_digest_verification.py', 'services/library/_grounding.py',
                     'services/library/_prompt_security.py', 'services/faithbench/_build_claims.py',
                     'models/triage.py', 'services/library/quality_review.py', 'services/library/_map_reduce.py',
                     'services/library/_review_identity.py', 'services/library/_review_cache.py',
                     'services/library/deep_review.py', 'services/library/_digest_audit.py',
                     'services/library/_digest_audit_export.py'),
    }
    return {kind + '_contract_sha256': canonical_hash({
        name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names
    }) for kind, names in sources.items()}


def source_context(full_text: str, cap: int, *, prefix: bool, verification_text: str | None) -> tuple[str, dict]:
    from zotero_summarizer.services.library._review_text import _location, select_review_text

    locations: list[dict] = []
    text = full_text[:cap] if prefix else select_review_text([], full_text, budget=cap, locations=locations)
    selected = text if verification_text is None else verification_text
    original = full_text if verification_text is None else verification_text
    if prefix or verification_text is not None:
        locations = [_location(0, len(selected), 0)]
    for location in locations:
        span, origin = location['selected_span'], location['original_span']
        fragment = selected[span['start']:span['end']]
        if origin is None:
            if fragment != '\n\n':
                raise ValueError('Invalid synthetic digest separator')
        elif fragment != original[origin['start']:origin['end']]:
            raise ValueError('Digest selection differs from original extraction')
    return text, {'original_text': original, 'selected_text': selected, 'locations': locations}


def retained_claims(digest: Any, checks: Any) -> list[dict]:
    from zotero_summarizer.services.library._digest_verification import _claims

    checked = {check.index: check for check in checks.checks}
    retained, parts = [], {}
    for index, claim in enumerate(_claims(digest)):
        path, _, text = claim.partition(': ')
        field = path.split('[', 1)[0].split('.', 1)[0]
        if field not in {'tldr', 'key_findings'}:
            continue
        part = parts.get(path, 0)
        parts[path] = part + 1
        check = checked[index]
        if not check.supported or not check.evidence:
            raise ValueError('Cannot retain unaccepted digest checks')
        retained.append({'index': index, 'field': field, 'path': path, 'part': part, 'claim': text,
                         'support': 'supported_by_existing_verifier',
                         'numeric_outcome': 'passed_existing_literal_preflight', 'evidence': check.evidence})
    return retained


def _fragments(span: tuple[int, int], source: dict) -> list[dict]:
    fragments = []
    for location in source['locations']:
        selected = location['selected_span']
        start, end = max(span[0], selected['start']), min(span[1], selected['end'])
        if start >= end:
            continue
        original = location['original_span']
        offset = None if original is None else original['start'] + start - selected['start']
        text = source['selected_text'][start:end]
        if offset is not None and text != source['original_text'][offset:offset + len(text)]:
            raise ValueError('Digest evidence differs from original extraction')
        fragments.append({'selected_span': {'start': start, 'end': end},
                          'original_span': None if offset is None else {'start': offset, 'end': offset + len(text)},
                          'mapping': location['mapping'], 'text': text})
    if ''.join(fragment['text'] for fragment in fragments) != source['selected_text'][span[0]:span[1]]:
        raise ValueError('Digest audit mapping omitted evidence characters')
    return fragments


def build_verification_audit(digest: Any, checks: Any, source: dict, lineage: list[dict]) -> dict:
    from zotero_summarizer.services.faithbench._corpus import chunk_spans, sha256_text
    from zotero_summarizer.services.library._digest_verification import _Checks
    from zotero_summarizer.services.library._review_text import _location

    if not isinstance(checks, _Checks):
        raise ValueError('Digest audit requires accepted verifier checks')
    retained = retained_claims(digest, checks)
    spans = chunk_spans(source['selected_text'])
    used = {i for claim in retained for i in claim['evidence']}
    passages = [{'id': i, 'text': source['selected_text'][spans[i][0]:spans[i][1]],
                 'selected_span': {'start': spans[i][0], 'end': spans[i][1]},
                 'original_fragments': _fragments(spans[i], source), 'pdf_page': None,
                 'page_mapping_reason': 'text_extraction_has_no_page_mapping'} for i in sorted(used)]
    complete = (source['original_text'] == source['selected_text']
                and source['locations'] == [_location(0, len(source['selected_text']), 0)])
    verified = digest.model_dump(mode='json')
    return {'contract': CONTRACT, 'status': 'accepted_verifier_evidence', 'source': {
        'original_text_sha256': sha256_text(source['original_text']),
        'selected_text_sha256': sha256_text(source['selected_text']),
        'original_characters': len(source['original_text']), 'selected_characters': len(source['selected_text']),
        'scope': 'complete_extracted_text' if complete else 'selected_original',
        'paper_completeness': 'not_established', 'locations': source['locations'],
    }, 'identity': {'verified_digest_sha256': canonical_hash(verified), **contract_hashes()},
        'verified_digest': verified, 'claims': retained, 'passages': passages, 'lineage': lineage,
        'contradiction_adjudication': 'not_performed'}


def payload_hash(audit: dict) -> str:
    payload = deepcopy(audit)
    payload['identity'].pop('audit_sha256', None)
    return canonical_hash(payload)


def bind_cached_audit(entry: dict) -> dict:
    if not entry.get('verification_audit'):
        return entry
    result = deepcopy(entry)
    audit = result['verification_audit']
    verified, final = audit['verified_digest'], result['digest']
    audit['post_verification_changes'] = sorted(key for key in verified.keys() | final.keys()
                                              if verified.get(key) != final.get(key))
    for claim in audit['claims']:
        claim['matches_cached_field'] = verified.get(claim['field']) == final.get(claim['field'])
    review = result['review_identity']
    audit['source']['kind'] = review['source_kind']
    audit['identity'].update(cached_digest_sha256=canonical_hash(final),
                             review_source_sha256=review['source_sha256'],
                             review_generation_sha256=review['generation_sha256'],
                             review_identity_sha256=canonical_hash(review))
    audit['identity']['audit_sha256'] = payload_hash(audit)
    return result
