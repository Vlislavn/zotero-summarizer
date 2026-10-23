import json
from copy import deepcopy

import pytest

from tests._interpretation_experiment import CARD, PAYLOAD, ScriptedClient, inputs
from zotero_summarizer.models import PaperDigest
from zotero_summarizer.services.library._map_reduce import digest_for_strategy


def run(client, condition='C', **overrides):
    from zotero_summarizer.services.library.interpretation import review
    from zotero_summarizer.services.library._interpretation_trace import Trace

    paper, config, budget = inputs()
    return review(paper, config, (client, client), budget, condition, Trace(max_calls=30), **overrides)


def test_review_roundtrip_retains_scientific_proof_not_metadata():
    from zotero_summarizer.services.library.interpretation import qualify

    result = json.loads(json.dumps(run(ScriptedClient())))
    card = qualify(result)
    assert card['status'] == 'accepted'
    assert card['origin'] == 'ai_draft'
    assert card['items'] == [CARD]
    assert card['checks']['checks']
    assert any('external_validation: False' in claim for claim in card['claims'])
    assert not any('ai_draft' in claim for claim in card['claims'])
    assert result['digest']['key_findings'] == PAYLOAD['key_findings']
    assert result['verification_audit']['status'] == 'accepted_verifier_evidence'


@pytest.mark.parametrize('condition', ['A', 'B'])
def test_optional_absent_on_controls_and_a_matches_actual_baseline(condition):
    paper, config, budget = inputs()
    client = ScriptedClient(cards=False)
    baseline = digest_for_strategy(paper['title'], paper['text'], config,
                                   map_llm=client, reduce_llm=client, budget=budget, verification_audit={})
    other = ScriptedClient(cards=False)
    result = run(other, condition)
    assert result['digest'] == baseline.model_dump(mode='json')
    assert result['interpretation']['status'] == 'disabled'
    if condition == 'A':
        assert other.calls == client.calls


def test_optional_rejection_does_not_retry_or_erase_accepted_base():
    client = ScriptedClient(reject_optional=True)
    result = run(client)
    assert result['interpretation']['status'] == 'support_not_established'
    assert result['digest']['key_findings'] == PAYLOAD['key_findings']
    assert sum(issubclass(model, PaperDigest) for _, model, _ in client.calls if model is not None) == 1


def test_required_base_presence_survives_decorator_and_retry():
    malformed = {key: value for key, value in PAYLOAD.items() if key != 'read_decision'}
    client = ScriptedClient(responses=[malformed, malformed])
    with pytest.raises(ValueError, match='omitted required'):
        run(client)
    assert len(client.calls) == 2


def test_decoder_schema_replaced_on_initial_and_retry(monkeypatch):
    from zotero_summarizer.services.library import interpretation
    from zotero_summarizer.integrations.llm import build_response_format

    malformed = {key: value for key, value in PAYLOAD.items() if key != 'read_decision'}
    client = ScriptedClient(responses=[malformed, PAYLOAD])
    original = interpretation.digest_for_strategy

    def decoder(*args, **kwargs):
        return original(*args, **kwargs, response_format=build_response_format(PaperDigest))

    monkeypatch.setattr(interpretation, 'digest_for_strategy', decoder)
    run(client)
    generated = [(model, kwargs) for _, model, kwargs in client.calls if issubclass(model, PaperDigest)]
    assert len(generated) == 2
    assert all('interpretation' in kwargs['response_format']['json_schema']['schema']['properties']
               for model, kwargs in generated)
    assert all(model is not PaperDigest for model, kwargs in generated)


@pytest.mark.parametrize('field', ['finding', 'studied_context', 'basis_refs'])
def test_resealed_optional_tampering_cannot_borrow_base_evidence(field):
    from zotero_summarizer.services.library.interpretation import qualify

    result = deepcopy(run(ScriptedClient()))
    result['interpretation']['items'][0][field] = [] if field == 'basis_refs' else 'different'
    with pytest.raises(ValueError):
        qualify(result)


def test_missing_optional_is_explicit_without_extra_verification():
    client = ScriptedClient(cards=False)
    result = run(client)
    assert result['interpretation']['status'] == 'unavailable'
    assert len(client.calls) == 2


@pytest.mark.parametrize('field,value', [('supported', 'true'), ('index', False)])
def test_optional_verifier_protocol_rejects_coercible_values_without_changing_base(field, value):
    class CoercibleOptional(ScriptedClient):
        def pydantic_prompt(self, *, prompt, **kwargs):
            result = super().pydantic_prompt(prompt=prompt, **kwargs)
            if 'interpretation[' in prompt:
                result['checks'][0][field] = value
            return result

    client = CoercibleOptional()
    result = run(client)
    assert result['digest']['key_findings'] == PAYLOAD['key_findings']
    assert result['interpretation']['status'] == 'unavailable'
    assert len(client.calls) == 4


def test_same_client_alias_does_not_enable_new_verifier_fallback():
    class Unavailable(ScriptedClient):
        def pydantic_prompt(self, *, pydantic_model, **kwargs):
            if not issubclass(pydantic_model, PaperDigest):
                self.calls.append(('invalid verifier', pydantic_model, {}))
                return {}
            return super().pydantic_prompt(pydantic_model=pydantic_model, **kwargs)

    client = Unavailable()
    with pytest.raises(ValueError, match='invalid response twice'):
        run(client)
    assert len(client.calls) == 3
