"""Opt-in A/B/C review invocation; ordinary review caches and defaults are untouched."""
from copy import deepcopy

from zotero_summarizer.integrations.llm import build_response_format
from zotero_summarizer.models import PaperDigest
from zotero_summarizer.models.interpretation import InterpretationDigest, InterpretationItem
from zotero_summarizer.services._common import extract_json_blob, to_text
from zotero_summarizer.services.faithbench._corpus import chunk_text, sha256_text
from zotero_summarizer.services.library._digest_audit import canonical_hash, contract_hashes
from zotero_summarizer.services.library._digest_audit_export import _validate_audit
from zotero_summarizer.services.library._digest_verification import (
    DigestSourceRejected, DigestVerifierUnavailable, _ScientificChecks, _claim_parts, _parse_checks, _unsupported_literals, verify_digest,
)
from zotero_summarizer.services.library._map_reduce import digest_for_strategy
from zotero_summarizer.services.library._paper_read_meta import qa_body_text

CAUTION = ("\nSeparate observed findings from causal, scope and practice recommendations. "
           "State the studied population and setting, material uncertainties and unsupported extrapolations. "
           "Missing evidence is not contradiction. Do not infer causal strength from design labels alone.")
CARD_REQUEST = ("\nAdditionally return optional interpretation: at most five objects following the response schema. "
                "Bind each to an exact whole key_findings entry and nonempty methods/limitations/parameters basis_refs. "
                "Include studied_context, inference_strength, rationale, uncertainties and unsupported_extrapolations. "
                "These are AI drafts, never author metadata or verification. Omit when the source cannot justify them.")


class _Client:
    def __init__(self, client, condition, trace):
        self.client, self.condition, self.trace, self.draft = client, condition, trace, None

    def prompt(self, prompt):
        return self.trace.invoke(self.client, 'prompt', prompt, {})

    def pydantic_prompt(self, *, prompt, pydantic_model, **kwargs):
        generating = pydantic_model is PaperDigest
        if generating and self.condition != 'A':
            prompt += CAUTION
        if generating and self.condition == 'C':
            self.draft = None
            pydantic_model = InterpretationDigest
            prompt += CARD_REQUEST
            if 'response_format' in kwargs:
                kwargs['response_format'] = build_response_format(pydantic_model)
        raw = self.trace.invoke(self.client, 'pydantic_prompt', prompt, {'pydantic_model': pydantic_model, **kwargs})
        if not generating or self.condition != 'C':
            return raw
        payload = (raw.model_dump(mode='json', exclude_unset=True, warnings=False) if hasattr(raw, 'model_dump')
                   else raw if isinstance(raw, dict) else extract_json_blob(to_text(raw)))
        self.draft = payload.get('interpretation')
        return PaperDigest.model_validate({key: value for key, value in payload.items() if key != 'interpretation'})


def _scientific_parts(path, value):
    if isinstance(value, dict):
        return [part for key, child in value.items() for part in _scientific_parts(f'{path}.{key}', child)]
    if isinstance(value, list):
        return [part for index, child in enumerate(value) for part in _scientific_parts(f'{path}[{index}]', child)]
    return [] if value is None else _claim_parts(path, value)


def scientific_claims(items, digest):
    claims, indices = [], set()
    for index, raw in enumerate(items):
        item = InterpretationItem.model_validate(raw).model_dump(mode='json')
        finding = item['finding_index']
        if (finding in indices or finding >= len(digest['key_findings']) or
            item['finding'] != digest['key_findings'][finding] or len(set(item['basis_refs'])) != len(item['basis_refs'])):
            raise ValueError('Interpretation requires unique, exact current findings and basis references')
        indices.add(finding)
        for field in ('finding', 'studied_context', 'inference_strength', 'rationale', 'uncertainties',
                      'unsupported_extrapolations'):
            claims.extend(_scientific_parts(f'interpretation[{index}].{field}', item[field]))
        for ref in item['basis_refs']:
            basis = _scientific_parts(ref, digest[ref])
            if not basis:
                raise ValueError('Interpretation basis is unavailable')
            claims.extend(basis)
    return claims


def selected_original(result):
    audit, text = result['verification_audit'], result['text']
    if qa_body_text(result['content']) != text:
        raise ValueError('Original body differs from the qualified record')
    source, identity = audit['source'], audit['identity']
    if source['original_text_sha256'] != sha256_text(text) or source['original_characters'] != len(text):
        raise ValueError('Original extraction changed')
    selected = ''.join('\n\n' if loc['original_span'] is None else
                       text[loc['original_span']['start']:loc['original_span']['end']] for loc in source['locations'])
    if len(selected) != source['selected_characters'] or sha256_text(selected) != source['selected_text_sha256']:
        raise ValueError('Selected original changed')
    verified, final = audit['verified_digest'], result['digest']
    if (any(identity.get(key) != value for key, value in contract_hashes().items()) or
        canonical_hash(final) != canonical_hash(verified | {'basis': final['basis']}) or
        final['basis'] not in {'full_text', 'map_reduce'}):
        raise ValueError('Current digest or verifier contract changed')
    retained = deepcopy(audit) | {'post_verification_changes': []}
    for row in retained['claims']:
        row['matches_cached_field'] = verified[row['field']] == final.get(row['field'])
    _validate_audit({'digest': verified}, retained)
    for passage in audit['passages']:
        span = passage['selected_span']
        if passage['text'] != selected[span['start']:span['end']]:
            raise ValueError('Retained base evidence changed')
    return selected


def qualify(result, *, strict=True):
    selected = selected_original(result)
    try:
        card = result['interpretation']
        if card['status'] not in ('accepted', 'disabled', 'unavailable', 'invalid', 'support_not_established', 'proof_unavailable'):
            raise ValueError('Invalid interpretation state')
        if card['status'] != 'accepted':
            return card
        items = [InterpretationItem.model_validate(item).model_dump(mode='json') for item in card['items']]
        claims = scientific_claims(items, result['digest'])
        if (not items or card['origin'] != 'ai_draft' or card['digest_sha256'] != canonical_hash(result['digest']) or
            card['source'] != result['verification_audit']['source'] or claims != card['claims'] or
            result['scientific_call']['scientific_binding'] != canonical_hash(
                [claims, card['source'], chunk_text(selected), card['checks']]) or _unsupported_literals(claims, selected)):
            raise ValueError('Interpretation proof binding changed')
        checks = _parse_checks(_ScientificChecks.model_validate(card['checks']), len(claims), chunk_text(selected), selected)
        if any(not check.supported for check in checks.checks):
            raise ValueError('Interpretation has unaccepted scientific claims')
        return card
    except (ValueError, KeyError, TypeError):
        if strict:
            raise
        return {'status': 'proof_unavailable'}


def review(paper, config, clients, budget, condition, trace):
    if condition not in {'A', 'B', 'C'}:
        raise ValueError('Unknown interpretation condition')
    wrapped = {id(client): _Client(client, condition, trace) for client in clients}
    mapper, reducer = (wrapped[id(client)] for client in clients)
    audit = {}
    digest = digest_for_strategy(paper['title'], paper['text'], config, map_llm=mapper, reduce_llm=reducer,
                                 budget=budget, verification_audit=audit)
    result = {'digest': digest.model_dump(mode='json'), 'verification_audit': audit, 'text': paper['text'],
              'content': deepcopy(paper['content']),
              'interpretation': {'status': 'disabled' if condition != 'C' else 'unavailable'}}
    selected = selected_original(result)
    if condition != 'C' or reducer.draft is None:
        return result
    try:
        if not isinstance(reducer.draft, list) or not 1 <= len(reducer.draft) <= 5:
            raise ValueError('Optional card must contain one to five items')
        items = [InterpretationItem.model_validate(item).model_dump(mode='json') for item in reducer.draft]
        claims = scientific_claims(items, result['digest'])
        checks = verify_digest(digest, selected, mapper, research_goals='; '.join(config.research_goals),
                               scientific_claims=claims)
    except Exception as exc:  # Optional-only I/O; independent base qualification stays outside.
        status = 'support_not_established' if isinstance(exc, DigestSourceRejected) else 'unavailable'
        if isinstance(exc, ValueError) and not isinstance(exc, (DigestSourceRejected, DigestVerifierUnavailable)):
            status = 'invalid'
        result['interpretation'] = {'status': status, 'failure_type': type(exc).__name__, 'draft': reducer.draft}
        return result
    trace.calls[-1]['scientific_binding'] = canonical_hash([claims, audit['source'], chunk_text(selected), checks.model_dump(mode='json')])
    result['scientific_call'] = deepcopy(trace.calls[-1])
    result['interpretation'] = {'status': 'accepted', 'origin': 'ai_draft', 'items': items, 'claims': claims,
                                'checks': checks.model_dump(mode='json'), 'source': audit['source'],
                                'digest_sha256': canonical_hash(result['digest'])}
    qualify(result)
    return result
