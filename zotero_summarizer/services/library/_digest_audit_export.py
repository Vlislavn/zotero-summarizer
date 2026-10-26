"""Read-time qualification of retained receipts; never re-extract or repair on read."""
from __future__ import annotations

from typing import Any

from zotero_summarizer.services.library._digest_audit import (
    CONTRACT, canonical_hash, contract_hashes, digest_contract_payload, payload_hash,
    reading_policy_inputs_hash, retained_claims,
)


def _span(value: dict, limit: int) -> tuple[int, int]:
    start, end = value['start'], value['end']
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= limit:
        raise ValueError('Invalid audit coordinates')
    return start, end


def _source_locations(source: dict) -> None:
    original, selected = source['original_characters'], source['selected_characters']
    if type(original) is not int or type(selected) is not int or min(original, selected) <= 0:
        raise ValueError('Invalid extraction extent')
    cursor = 0
    for location in source['locations']:
        start, end = _span(location['selected_span'], selected)
        if start != cursor:
            raise ValueError('Incomplete audit selection map')
        origin = location['original_span']
        if origin is None:
            if location['mapping'] != 'synthetic_separator' or end - start != 2:
                raise ValueError('Invalid audit separator')
        else:
            left, right = _span(origin, original)
            if location['mapping'] != 'exact' or end - start != right - left:
                raise ValueError('Invalid original audit map')
        cursor = end
    complete = (source['original_text_sha256'] == source['selected_text_sha256'] and original == selected
                and len(source['locations']) == 1
                and source['locations'][0]['original_span'] == source['locations'][0]['selected_span'])
    expected_scope = 'complete_extracted_text' if complete else 'selected_original'
    if cursor != selected or source['scope'] != expected_scope or source['paper_completeness'] != 'not_established':
        raise ValueError('Invalid extraction scope')


def _passage(passage: dict, source: dict) -> None:
    start, end = _span(passage['selected_span'], source['selected_characters'])
    text = passage['text']
    if not isinstance(text, str) or not text.strip() or len(text) != end - start:
        raise ValueError('Invalid retained passage')
    expected = []
    for location in source['locations']:
        selected, original = location['selected_span'], location['original_span']
        left, right = max(start, selected['start']), min(end, selected['end'])
        if left >= right:
            continue
        offset = None if original is None else original['start'] + left - selected['start']
        fragment = text[left - start:right - start]
        if offset is None and fragment != '\n\n'[:right - left]:
            raise ValueError('Invalid retained separator')
        expected.append({'selected_span': {'start': left, 'end': right},
                         'original_span': None if offset is None else {'start': offset, 'end': offset + right - left},
                         'mapping': location['mapping'], 'text': fragment})
    if canonical_hash(expected) != canonical_hash(passage['original_fragments']):
        raise ValueError('Invalid retained original fragments')
    if passage['pdf_page'] is not None or passage['page_mapping_reason'] != 'text_extraction_has_no_page_mapping':
        raise ValueError('Invalid retained page provenance')


def _validate_claims(audit: dict, digest: Any, final: dict) -> None:
    from zotero_summarizer.services.library._digest_verification import _Checks

    records = audit['claims']
    checks = _Checks.model_validate({'checks': [
        {'index': row['index'], 'supported': True, 'evidence': row['evidence']} for row in records
    ]})
    if len({row['index'] for row in records}) != len(records):
        raise ValueError('Duplicate retained indices')
    if any(len(set(check.evidence)) != len(check.evidence) for check in checks.checks):
        raise ValueError('Duplicate retained evidence IDs')
    expected = retained_claims(digest, checks)
    for row in expected:
        row['matches_cached_field'] = audit['verified_digest'][row['field']] == final.get(row['field'])
    if canonical_hash(expected) != canonical_hash(records):
        raise ValueError('Invalid retained claims')
    ids = set()
    for passage in audit['passages']:
        index = passage['id']
        if type(index) is not int or index < 0 or index in ids:
            raise ValueError('Invalid retained passage IDs')
        _passage(passage, audit['source'])
        ids.add(index)
    if ids != {i for row in expected for i in row['evidence']}:
        raise ValueError('Invalid retained passage references')


def _audit_shape(audit: dict) -> None:
    required = {'contract', 'status', 'source', 'identity', 'verified_digest', 'claims',
                'passages', 'lineage', 'contradiction_adjudication', 'post_verification_changes'}
    if set(audit) != required:
        raise ValueError('Invalid audit envelope')
    for name in ('source', 'identity', 'verified_digest'):
        if not isinstance(audit[name], dict):
            raise ValueError('Invalid audit object')
    for name in ('claims', 'passages', 'lineage', 'post_verification_changes'):
        if not isinstance(audit[name], list):
            raise ValueError('Invalid audit array')
    if any(not isinstance(row, dict) for name in ('claims', 'passages', 'lineage') for row in audit[name]):
        raise ValueError('Invalid audit row')
    if not isinstance(audit['source'].get('locations'), list):
        raise ValueError('Invalid audit locations')
    if any(not isinstance(row, dict) for row in audit['source']['locations']):
        raise ValueError('Invalid audit location')


def _validate_lineage(audit: dict) -> None:
    lineage = audit['lineage']
    if not 1 <= len(lineage) <= 2 or lineage[-1]['outcome'] != 'accepted':
        raise ValueError('Invalid correction lineage')
    if lineage[-1]['digest_sha256'] != audit['identity']['verified_digest_sha256']:
        raise ValueError('Invalid accepted attempt identity')
    if len(lineage) == 2 and lineage[0]['outcome'] != 'support_not_established':
        raise ValueError('Invalid rejected attempt')
    for index, row in enumerate(lineage):
        if type(row['correction']) is not bool or row['correction'] != (index == 1):
            raise ValueError('Invalid correction order')
        if row['verifier_route'] not in {'primary', 'generator_fallback'}:
            raise ValueError('Invalid verifier route')
        if row.get('unavailable_routes', []) not in ([], ['primary']):
            raise ValueError('Invalid operational fallback')
    if type(lineage[-1]['verifier_repairs']) is not int or lineage[-1]['verifier_repairs'] not in (0, 1):
        raise ValueError('Invalid accepted verifier repair count')


def _validate_audit(entry: dict, audit: dict) -> None:
    import re
    from zotero_summarizer.models import PaperDigest

    if audit['contract'] != CONTRACT or audit['status'] != 'accepted_verifier_evidence':
        raise ValueError('Invalid digest audit contract')
    verified = PaperDigest.model_validate(audit['verified_digest'])
    final = PaperDigest.model_validate(entry['digest'])
    if (canonical_hash(verified.model_dump(mode='json')) != canonical_hash(audit['verified_digest'])
        or canonical_hash(final.model_dump(mode='json')) != canonical_hash(digest_contract_payload(entry['digest']))):
        raise ValueError('Invalid retained digest schema')
    if canonical_hash(audit['verified_digest']) != audit['identity']['verified_digest_sha256']:
        raise ValueError('Invalid verified digest identity')
    for name in ('original_text_sha256', 'selected_text_sha256'):
        if not re.fullmatch('[a-f0-9]{64}', audit['source'][name]):
            raise ValueError('Invalid extraction hash')
    if audit['contradiction_adjudication'] != 'not_performed':
        raise ValueError('Unrecorded human adjudication')
    _source_locations(audit['source'])
    _validate_claims(audit, verified, entry['digest'])
    final_fields = digest_contract_payload(entry['digest'])
    changed = sorted(key for key in audit['verified_digest'].keys() | final_fields.keys()
                     if audit['verified_digest'].get(key) != final_fields.get(key))
    if audit['post_verification_changes'] != changed:
        raise ValueError('Invalid post-verification lineage')
    _validate_lineage(audit)


def retained_audit_status(entry: dict, *, current_identity: dict | None = None) -> tuple[str, str]:
    if entry.get('digest') is not None and not isinstance(entry['digest'], dict):
        raise ValueError('Invalid cached digest')
    audit = entry.get('verification_audit')
    if audit is None:
        return 'unavailable', 'source_unavailable' if entry.get('needs_pdf') else 'legacy_audit_unavailable'
    if not isinstance(audit, dict):
        raise ValueError('Invalid digest verification audit')
    try:
        _audit_shape(audit)
        identity = audit['identity']
        intact = payload_hash(audit) == identity['audit_sha256']
        bound = canonical_hash(digest_contract_payload(entry['digest'])) == identity['cached_digest_sha256']
        bound = bound and canonical_hash(entry.get('review_identity')) == identity['review_identity_sha256']
        if not intact or not bound:
            return 'stale', 'cached_artifact_changed'
        current = all(identity[key] == value for key, value in contract_hashes().items())
        if not current:
            return 'stale', 'audit_contract_changed'
        if reading_policy_inputs_hash(entry) != identity['reading_policy_inputs_sha256']:
            return 'stale', 'reading_policy_inputs_changed'
        _validate_audit(entry, audit)
        if any(not claim['matches_cached_field'] for claim in audit['claims']):
            return 'stale', 'post_verification_claim_changed'
        review = entry['review_identity']
        if not isinstance(review, dict) or not review.get('source_path'):
            return 'unavailable', 'source_identity_unavailable'
        from zotero_summarizer.services.library.paper_render import _allowed_paper_path
        from zotero_summarizer.services.library._review_identity import build_review_identity
        from zotero_summarizer.services._common import state

        if (audit['source']['kind'] != review['source_kind']
            or identity['review_source_sha256'] != review['source_sha256']
            or identity['review_generation_sha256'] != review['generation_sha256']):
            raise ValueError('Invalid review-source binding')
        now = current_identity
        if now is None:
            path = _allowed_paper_path(review['source_path'])
            if not path.is_file():
                return 'unavailable', 'source_unavailable'
            now = build_review_identity(config=state().app_state.config, pdf_path=str(path),
                                        source_kind=review['source_kind'], focus_prompt=review['focus_prompt'])
        if now['source_sha256'] != review['source_sha256']:
            return 'stale', 'source_bytes_changed'
        if now['generation_sha256'] != review['generation_sha256']:
            return 'stale', 'review_configuration_changed'
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError('Malformed digest verification audit') from exc
    return 'available', ''
