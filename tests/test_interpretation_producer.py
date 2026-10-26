"""Current producer/decorator contracts; scripted checks are not scientific warrant."""
import builtins
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests._interpretation_experiment import CARD, PAYLOAD, TEXT, ScriptedClient, inputs, reply, run_review
from zotero_summarizer.integrations.llm import InstrumentedLLMClient, build_response_format
from zotero_summarizer.models import PaperDigest
from zotero_summarizer.models.interpretation import InterpretationDigest, InterpretationItem
from zotero_summarizer.services.library import interpretation, quality_review
from zotero_summarizer.services.library._digest_audit import digest_contract_payload
from zotero_summarizer.services.library._interpretation_trace import Trace
from zotero_summarizer.services.library._prompt_security import untrusted_input

REQUIRED = ('read_decision', 'read_why', 'read_parts', 'skip_parts', 'estimated_read_minutes',
            'original_value', 'writing_friction', 'writing_reasons')


def test_off_keeps_base_prompt_model_serialization_calls_and_no_extra_io(monkeypatch):
    _, config, _ = inputs()
    config.prompts.paper_digest = 'Title: {title}\nGoals: {research_goals}\nOriginal: {full_text}'
    client = ScriptedClient(cards=False)
    forbidden = Mock(side_effect=AssertionError('ordinary generation must not import optional owners or read files'))
    original_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert 'interpretation' not in name
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', guarded)
    monkeypatch.setattr(Path, 'open', forbidden)
    digest = quality_review.assess_digest(title='Paper', full_text=TEXT, config=config, llm=client,
                                         focus_prompt='Methods')
    goals = '; '.join(config.research_goals) or '(not specified)'
    assert client.calls[0] == (quality_review._digest_prompt(config.prompts.paper_digest, 'Paper', TEXT, goals,
                                                            'Methods'), PaperDigest, {})
    assert digest.model_dump(mode='json') == PaperDigest.model_validate(PAYLOAD).model_dump(mode='json')
    assert len(client.calls) == 2 and 'interpretation' not in digest.model_dump()
    forbidden.assert_not_called()


@pytest.mark.parametrize('format_arg', [None, {}, {'type': 'json_object'}])
def test_off_preserves_caller_response_format(format_arg):
    _, config, _ = inputs()
    client = ScriptedClient(cards=False)
    quality_review.assess_digest(title='Paper', full_text=TEXT, config=config, llm=client,
                                 response_format=format_arg)
    assert client.calls[0][2] == ({'response_format': format_arg} if format_arg else {})


def test_c_schema_is_typed_and_cannot_authorize_origin_or_verification():
    schema = InterpretationDigest.model_json_schema()
    item = schema['$defs']['InterpretationItem']
    assert set(item['properties']) == set(CARD)
    assert set(item['required']) == set(CARD)
    assert item['additionalProperties'] is False
    assert item['properties']['basis_refs']['items']['enum'] == ['methods', 'limitations', 'parameters']
    assert schema['properties']['interpretation']['anyOf'][0]['maxItems'] == 5
    for name in ('origin', 'scientific_verification', 'status'):
        with pytest.raises(ValueError):
            InterpretationItem.model_validate(CARD | {name: 'author_approved'})


def test_off_projects_an_unexpected_c_subclass_without_payload_leak():
    raw = InterpretationDigest.model_validate(PAYLOAD | {'interpretation': [CARD]})
    base = digest_contract_payload(raw)
    assert base == digest_contract_payload(PaperDigest.model_validate(PAYLOAD))
    assert 'interpretation' not in base
    assert raw.interpretation == [CARD]  # retained subtype metadata is not base authority


@pytest.mark.parametrize('form', ['dict', 'json', 'fenced', 'prose', 'parsed'])
def test_c_capture_precedes_plain_base_projection_and_retains_explicit_presence(form):
    raw = reply(PAYLOAD | {'interpretation': [CARD]}, form)
    provider = ScriptedClient(cards=False, responses=[raw])
    adapter = interpretation._Client(provider, 'C', Trace(max_calls=2))
    digest = adapter.pydantic_prompt(prompt='Owned source', pydantic_model=PaperDigest)
    assert type(digest) is PaperDigest and 'interpretation' not in digest.model_dump()
    assert digest.model_fields_set == set(PAYLOAD)
    assert digest.model_dump() == PaperDigest.model_validate(PAYLOAD).model_dump()
    assert adapter.draft == [CARD]


@pytest.mark.parametrize('form', ['dict', 'json', 'fenced', 'prose', 'parsed'])
@pytest.mark.parametrize('card,status', [
    (None, 'unavailable'), ([], 'invalid'), ({'items': [CARD]}, 'invalid'),
    ([dict(CARD, rationale=42)], 'invalid'), ([dict(CARD, uncertainties='unknown')], 'invalid'),
    ([dict(CARD, origin='author_approved')], 'invalid'), ([dict(CARD, basis_refs=['confidence'])], 'invalid'),
    ([dict(CARD, finding_index=False)], 'invalid'), ([dict(CARD, finding_index=99)], 'invalid'),
    ([dict(CARD, finding='different')], 'invalid'), ([CARD, CARD], 'invalid'),
    ([dict(CARD, basis_refs=['methods', 'methods'])], 'invalid'),
])
def test_optional_failure_preserves_known_valid_base_explicitly(form, card, status):
    client = ScriptedClient(cards=False, responses=[reply(PAYLOAD | {'interpretation': card}, form)])
    result = run_review(client)
    assert result['interpretation']['status'] == status
    assert result['digest']['key_findings'] == PAYLOAD['key_findings']
    assert result['verification_audit']['status'] == 'accepted_verifier_evidence'
    assert len(client.calls) == 2


@pytest.mark.parametrize('form', ['dict', 'parsed'])
def test_missing_optional_content_is_not_a_silent_empty_card(form):
    result = run_review(ScriptedClient(cards=False, responses=[reply(PAYLOAD, form)]))
    assert result['interpretation'] == {'status': 'unavailable'}
    assert result['digest']['key_findings'] == PAYLOAD['key_findings']


@pytest.mark.parametrize('finding,index', [('', 0), ('not current', 0), (CARD['finding'], 1)])
def test_binding_requires_actual_nonempty_current_finding(finding, index):
    with pytest.raises(ValueError):
        interpretation.scientific_claims([dict(CARD, finding=finding, finding_index=index)], PAYLOAD)


@pytest.mark.parametrize('ref,basis', [('methods', ''), ('limitations', ''), ('parameters', None),
                                      ('parameters', {'external_validation': None})])
def test_whole_referenced_basis_must_be_present_without_optional_retry(ref, basis):
    payload = PAYLOAD | {ref: basis, 'interpretation': [dict(CARD, basis_refs=[ref])]}
    client = ScriptedClient(cards=False, responses=[payload])
    result = run_review(client)
    assert result['interpretation']['status'] == 'invalid'
    assert result['digest']['key_findings'] == PAYLOAD['key_findings'] and len(client.calls) == 2


@pytest.mark.parametrize('field', REQUIRED)
@pytest.mark.parametrize('form', ['dict', 'json', 'parsed'])
def test_c_does_not_fabricate_required_fields_from_typed_dump_defaults(field, form):
    payload = {key: value for key, value in PAYLOAD.items() if key != field} | {'interpretation': [CARD]}
    client = ScriptedClient(cards=False, responses=[reply(payload, form)] * 2)
    with pytest.raises(ValueError, match='omitted required'):
        run_review(client)
    assert len(client.calls) == 2


@pytest.mark.parametrize('field,value', [('soundness', 0), ('read_decision', 'maybe'), ('writing_friction', 'high')])
@pytest.mark.parametrize('form', ['dict', 'parsed'])
def test_optional_failure_never_masks_invalid_base(form, field, value):
    payload = PAYLOAD | {field: value, 'interpretation': []}
    raw = InterpretationDigest.model_construct(**payload) if form == 'parsed' else payload
    client = ScriptedClient(cards=False, responses=[raw, raw])
    with pytest.raises(ValueError):
        run_review(client)
    assert len(client.calls) == 2


@pytest.mark.parametrize('format_route', ['prompt', 'caller', 'instrumented'])
def test_c_uses_same_selected_schema_on_generation_parse_retry_and_correction(format_route):
    provider = ScriptedClient(cards=False, responses=['', PAYLOAD | {'tldr': 'Unreported 99999%',
                'interpretation': [dict(CARD, rationale='First attempt')]}, PAYLOAD | {'interpretation': [CARD]}])
    generator = InstrumentedLLMClient(provider, structured_output=True) if format_route == 'instrumented' else provider
    adapter = interpretation._Client(generator, 'C', Trace(max_calls=10))
    _, config, _ = inputs()
    digest = quality_review.assess_digest(title='Paper', full_text=TEXT, config=config, llm=adapter,
        response_format=build_response_format(PaperDigest) if format_route == 'caller' else None)
    generated = [(prompt, model, kwargs) for prompt, model, kwargs in provider.calls if issubclass(model, PaperDigest)]
    assert len(generated) == 3 and all(model is InterpretationDigest for _, model, _ in generated)
    assert all(interpretation.CARD_REQUEST in prompt for prompt, _, _ in generated)
    assert 'previous reply was not valid' in generated[1][0]
    assert 'failed source verification' in generated[2][0]
    if format_route != 'prompt':
        assert all(kwargs['response_format'] == build_response_format(InterpretationDigest) for _, _, kwargs in generated)
    assert digest.key_findings == PAYLOAD['key_findings'] and adapter.draft == [CARD]


@pytest.mark.parametrize('card', [None, [], [CARD]])
@pytest.mark.parametrize('typed_client', [False, True])
def test_optional_content_never_adds_generator_retry(card, typed_client):
    payload = PAYLOAD | {'interpretation': card}
    client = ScriptedClient(cards=False, responses=[reply(payload, 'parsed' if typed_client else 'dict')])
    result = run_review(client)
    assert result['digest']['key_findings'] == PAYLOAD['key_findings']
    assert sum(issubclass(model, PaperDigest) for _, model, _ in client.calls) == 1
    assert len(client.calls) == (3 if card else 2)


def test_base_evidence_and_original_spans_cannot_certify_optional_scientific_content():
    client = ScriptedClient(cards=False, responses=[PAYLOAD | {'interpretation': [dict(CARD, rationale='Unreported 99999%')]}])
    result = run_review(client)
    assert result['interpretation']['status'] == 'support_not_established'
    audit = result['verification_audit']
    assert audit['status'] == 'accepted_verifier_evidence'
    assert {claim['field'] for claim in audit['claims']} == {'tldr', 'key_findings'}
    assert len(client.calls) == 2 and 'scientific_call' not in result
    for passage in audit['passages']:
        span = passage['selected_span']
        assert passage['text'] == TEXT[span['start']:span['end']]


@pytest.mark.parametrize('failure', ['source', 'parse', 'generator', 'base_rejected', 'verifier', 'correction', 'second_verifier'])
def test_assessment_failure_clears_existing_collector_and_never_exposes_first_attempt(failure):
    # There is no public optional collector: a failed invocation cannot return a record.
    client = ScriptedClient(cards=False, responses=[PAYLOAD | {'tldr': 'Unreported 99999%', 'interpretation': [CARD]}] * 2)
    paper, _, _ = inputs()
    if failure == 'source':
        paper['text'] = ''
    elif failure in {'parse', 'correction'}:
        client.responses = ['', ''] if failure == 'parse' else [client.responses[0], '']
    elif failure == 'generator':
        client.pydantic_prompt = Mock(side_effect=RuntimeError('generator unavailable'))
    elif failure in {'verifier', 'second_verifier'}:
        client.responses = [PAYLOAD | {'interpretation': [CARD]}] * 2
        original = client.pydantic_prompt
        attempts = []
        def unavailable(**kwargs):
            value = original(**kwargs)
            if issubclass(kwargs['pydantic_model'], PaperDigest):
                return value
            attempts.append(value)
            if failure == 'second_verifier' and len(attempts) == 1:
                value['checks'][0] = dict(value['checks'][0], supported=False, evidence=[])
                return value
            return {}
        client.pydantic_prompt = unavailable
    with pytest.raises((ValueError, RuntimeError)):
        run_review(client, paper=paper)
    if failure == 'source':
        assert client.calls == []


def test_correction_missing_card_replaces_not_reuses_first_attempt():
    client = ScriptedClient(cards=False, responses=[PAYLOAD | {'tldr': 'Unreported 99999%', 'interpretation': [CARD]}, PAYLOAD])
    result = run_review(client)
    assert result['interpretation'] == {'status': 'unavailable'}
    assert result['digest']['key_findings'] == PAYLOAD['key_findings']
    assert len(client.calls) == 3
