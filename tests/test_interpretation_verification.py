"""Independent base/scientific verification; no combined offsets or exception facades."""
from copy import deepcopy

import pytest

from tests._interpretation_experiment import CARD, PAYLOAD, TEXT, ScriptedClient, run_review
from zotero_summarizer.models import PaperDigest
from zotero_summarizer.services.library import interpretation
from zotero_summarizer.services.library._digest_verification import (
    DigestSourceRejected, DigestVerifierUnavailable, _ScientificChecks, verify_digest,
)


def _table(count, *, rejected=()):
    return [{'index': i, 'supported': i not in rejected, 'evidence': [] if i in rejected else [0]}
            for i in range(count)]


def test_closed_projection_uses_five_roles_and_native_sentence_parts_in_supplied_order():
    item = dict(CARD, rationale='First rationale. Second rationale.',
                uncertainties=['First uncertainty.', 'First uncertainty.'], basis_refs=['limitations', 'parameters'])
    claims = interpretation.scientific_claims([item], PAYLOAD)
    assert claims == [
        'interpretation[0].finding: The sample retained alpha',
        'interpretation[0].studied_context: The studied cohort was adults',
        'interpretation[0].inference_strength: association',
        'interpretation[0].rationale: First rationale.', 'interpretation[0].rationale: Second rationale.',
        'interpretation[0].uncertainties[0]: First uncertainty.',
        'interpretation[0].uncertainties[1]: First uncertainty.',
        'interpretation[0].unsupported_extrapolations[0]: Treatment recommendations were not established',
        'limitations: External validation was not performed', 'parameters.external_validation: False',
    ]
    verifier = ScriptedClient()
    checks = verify_digest(PaperDigest.model_validate(PAYLOAD), TEXT, verifier, scientific_claims=claims)
    assert len(checks.checks) == len(claims) and verifier.calls[0][1] is _ScientificChecks
    assert '[0] ' + claims[0] in verifier.calls[0][0]
    assert all('origin' not in claim and 'ai_draft' not in claim for claim in claims)


def test_numeric_rejection_is_typed_sorted_and_has_no_model_call_or_checks():
    verifier = ScriptedClient()
    with pytest.raises(DigestSourceRejected) as caught:
        verify_digest(PaperDigest.model_validate(PAYLOAD), TEXT, verifier,
                      scientific_claims=['interpretation[0].rationale: Unreported 99999%'])
    assert caught.value.failure_kind == 'numeric_preflight' and verifier.calls == []


def test_semantic_rejection_exposes_a_complete_deep_copy_with_global_indices():
    # Local optional indices replace the removed combined global-offset DTO.
    verifier = ScriptedClient(reject_optional=True)
    raw = list(reversed(_table(2, rejected=(1,))))
    verifier.pydantic_prompt = lambda **kwargs: {'checks': deepcopy(raw)}
    with pytest.raises(DigestSourceRejected) as caught:
        verify_digest(PaperDigest.model_validate(PAYLOAD), TEXT, verifier,
                      scientific_claims=['interpretation[0].finding: alpha', 'interpretation[0].rationale: beta'])
    assert caught.value.failure_kind == 'verifier_support_not_established'
    assert raw == list(reversed(_table(2, rejected=(1,))))


def test_optional_numeric_rejection_removes_whole_card_then_verifies_base_once():
    client = ScriptedClient(cards=False, responses=[PAYLOAD | {'interpretation': [dict(CARD, rationale='Unreported 99999%')]}])
    result = run_review(client)
    assert result['interpretation']['status'] == 'support_not_established'
    assert len(client.calls) == 2  # accepted base precedes the optional numeric guard
    assert result['verification_audit']['lineage'][-1]['outcome'] == 'accepted'
    assert all('interpretation[' not in prompt for prompt, _, _ in client.calls[1:])


def test_reversed_optional_semantic_rejection_reuses_only_complete_supported_base():
    class Reversed(ScriptedClient):
        def pydantic_prompt(self, *, prompt, **kwargs):
            result = super().pydantic_prompt(prompt=prompt, **kwargs)
            if 'interpretation[' in prompt:
                result['checks'] = list(reversed(result['checks']))
            return result

    client = Reversed(reject_optional=True)
    result = run_review(client)
    assert result['interpretation']['status'] == 'support_not_established'
    assert len(client.calls) == 3
    assert result['digest']['key_findings'] == PAYLOAD['key_findings']
    assert {row['field'] for row in result['verification_audit']['claims']} == {'tldr', 'key_findings'}


def test_accepted_optional_support_is_separate_from_base_audit_and_scientific_state():
    result = run_review(ScriptedClient())
    card, audit = result['interpretation'], result['verification_audit']
    assert card['status'] == 'accepted' and card['origin'] == 'ai_draft'
    assert 'interpretation' not in result['digest'] and 'native_support' not in audit
    assert 'interpretation_evidence' not in audit
    assert card['checks']['checks'] == _table(len(card['claims']))
    assert card['source'] == audit['source']
    assert audit['contradiction_adjudication'] == 'not_performed'


@pytest.mark.parametrize('phase', ['numeric', 'semantic'])
@pytest.mark.parametrize('mixed', [False, True])
def test_base_or_mixed_rejection_uses_one_correction_with_fresh_projection_and_offset(phase, mixed):
    first = PAYLOAD | {'interpretation': [dict(CARD, rationale='Unreported 88888%' if mixed else 'First rationale')]}
    if phase == 'numeric':
        first['tldr'] = 'Unreported 99999%'

    class FirstBaseRejects(ScriptedClient):
        rejected = False

        def pydantic_prompt(self, *, prompt, pydantic_model, **kwargs):
            result = super().pydantic_prompt(prompt=prompt, pydantic_model=pydantic_model, **kwargs)
            if phase == 'semantic' and not issubclass(pydantic_model, PaperDigest) and not self.rejected:
                self.rejected = True
                result['checks'][0] = dict(result['checks'][0], supported=False, evidence=[])
            return result

    client = FirstBaseRejects(cards=False, responses=[first, PAYLOAD | {'interpretation': [CARD]}])
    result = run_review(client)
    assert result['interpretation']['items'] == [CARD]
    assert sum(issubclass(model, PaperDigest) for _, model, _ in client.calls) == 2
    assert len(client.calls) == (4 if phase == 'numeric' else 5)
    assert [row['outcome'] for row in result['verification_audit']['lineage']] == ['support_not_established', 'accepted']
    assert all('First rationale' not in claim for claim in result['interpretation']['claims'])


@pytest.mark.parametrize('phase', ['numeric', 'semantic'])
def test_second_base_rejection_fails_closed_and_clears_both_public_collectors(phase):
    payload = PAYLOAD | {'tldr': 'Unreported 99999%' if phase == 'numeric' else PAYLOAD['tldr'], 'interpretation': [CARD]}
    client = ScriptedClient(cards=False, responses=[payload, payload])
    original = client.pydantic_prompt

    def reject(**kwargs):
        result = original(**kwargs)
        if not issubclass(kwargs['pydantic_model'], PaperDigest):
            result['checks'][0] = dict(result['checks'][0], supported=False, evidence=[])
        return result

    client.pydantic_prompt = reject
    with pytest.raises(DigestSourceRejected):
        run_review(client)
    assert len(client.calls) == (2 if phase == 'numeric' else 4)
    assert all('interpretation[' not in prompt for prompt, model, _ in client.calls if not issubclass(model, PaperDigest))


def test_optional_numeric_removal_cannot_accept_a_semantically_rejected_base():
    client = ScriptedClient(cards=False, responses=[PAYLOAD | {'interpretation': [dict(CARD, rationale='99999%')]}] * 2)
    original = client.pydantic_prompt

    def reject(**kwargs):
        result = original(**kwargs)
        if not issubclass(kwargs['pydantic_model'], PaperDigest):
            result['checks'][0] = dict(result['checks'][0], supported=False, evidence=[])
        return result

    client.pydantic_prompt = reject
    with pytest.raises(DigestSourceRejected):
        run_review(client)
    assert len(client.calls) == 4


@pytest.mark.parametrize('defect', ['missing_base', 'duplicate_base', 'invalid_optional_evidence'])
@pytest.mark.parametrize('recovery', ['repair', 'fallback', 'unavailable'])
def test_malformed_combined_checks_never_partially_accept_and_fallback_keeps_projection(defect, recovery):
    class Malformed(ScriptedClient):
        attempts = 0

        def pydantic_prompt(self, **kwargs):
            result = super().pydantic_prompt(**kwargs)
            if not issubclass(kwargs['pydantic_model'], PaperDigest):
                self.attempts += 1
                if recovery == 'unavailable' or self.attempts <= (1 if recovery == 'repair' else 2):
                    if defect == 'missing_base':
                        result['checks'].pop()
                    elif defect == 'duplicate_base':
                        result['checks'][1] = dict(result['checks'][0])
                    else:
                        result['checks'][0]['evidence'] = [999]
            return result

    mapper, reducer = Malformed(), ScriptedClient()
    if recovery == 'unavailable':
        reducer = Malformed()
        with pytest.raises(DigestVerifierUnavailable):
            run_review(reducer, mapper=mapper)
        assert len(reducer.calls) == 3
    else:
        result = run_review(reducer, mapper=mapper)
        assert result['verification_audit']['lineage'][0]['outcome'] == 'accepted'
        assert result['interpretation']['status'] == 'accepted'
        assert len(reducer.calls) == (1 if recovery == 'repair' else 2)
    assert mapper.attempts >= 2


@pytest.mark.parametrize('defect', ['missing_base', 'duplicate_base', 'invalid_optional_evidence', 'wrong_offset', 'wrong_rejection', 'no_checks'])
def test_invalid_typed_rejection_cannot_be_reused_or_trigger_generation_correction(defect):
    class BadOptional(ScriptedClient):
        def pydantic_prompt(self, *, prompt, **kwargs):
            result = super().pydantic_prompt(prompt=prompt, **kwargs)
            if 'interpretation[' in prompt:
                rows = result['checks']
                if defect == 'missing_base':
                    rows.pop()
                elif defect == 'duplicate_base':
                    rows[1] = dict(rows[0])
                elif defect == 'invalid_optional_evidence':
                    rows[0]['evidence'] = [999]
                elif defect == 'wrong_offset':
                    rows[0]['index'] = len(rows)
                elif defect == 'wrong_rejection':
                    rows[0]['supported'] = 'false'
                else:
                    result = {}
            return result

    client = BadOptional()
    result = run_review(client)
    assert result['interpretation']['status'] == 'unavailable'
    assert len(client.calls) == 4
    assert sum(issubclass(model, PaperDigest) for _, model, _ in client.calls) == 1


@pytest.mark.parametrize('card', [None, [CARD]])
def test_c_never_accepts_unknown_checks_as_a_valid_base_or_empty_card(card):
    client = ScriptedClient(cards=False, responses=[PAYLOAD | {'interpretation': card}])
    original = client.pydantic_prompt
    client.pydantic_prompt = lambda **kw: original(**kw) if issubclass(kw['pydantic_model'], PaperDigest) else {}
    with pytest.raises(DigestVerifierUnavailable):
        run_review(client)
    assert len(client.calls) == 1


def test_optional_numeric_rejection_cannot_pass_when_base_verifiers_are_unavailable():
    client = ScriptedClient(cards=False, responses=[PAYLOAD | {'interpretation': [dict(CARD, rationale='99999%')]}])
    original = client.pydantic_prompt
    client.pydantic_prompt = lambda **kw: original(**kw) if issubclass(kw['pydantic_model'], PaperDigest) else ''
    with pytest.raises(DigestVerifierUnavailable):
        run_review(client)
    assert len(client.calls) == 1


def test_off_does_not_pass_optional_keyword_to_the_existing_verifier(monkeypatch):
    from unittest.mock import Mock
    from zotero_summarizer.services.library import _digest_verification, quality_review
    from tests._interpretation_experiment import inputs

    spy = Mock(wraps=verify_digest)
    monkeypatch.setattr(_digest_verification, 'verify_digest', spy)
    _, config, _ = inputs()
    quality_review.assess_digest(title='Paper', full_text=TEXT, config=config, llm=ScriptedClient(cards=False))
    assert set(spy.call_args.kwargs) == {'research_goals'}
