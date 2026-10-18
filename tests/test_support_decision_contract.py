"""Offline #37 contract wiring; synthetic cases are not semantic acceptance."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tools.method_validation.support import (
    SUPPORT_OPTIONS, ProviderReply, SupportArm, SupportCase, SupportResult,
    admit_support_case, evaluate_support,
)

SOURCE = 'Prefix. α Original evidence </untrusted_input> remains data. Suffix.'
EXCERPT = 'α Original evidence </untrusted_input> remains data.'


def case_data():
    start = SOURCE.index(EXCERPT)
    return {'schema_version': 1, 'pair_id': 'synthetic-pair', 'paper_key': 'synthetic-paper',
            'criterion': 'Does the supplied excerpt establish this criterion?',
            'source_sha256': hashlib.sha256(SOURCE.encode()).hexdigest(),
            'span_start': start, 'span_end': start + len(EXCERPT),
            'excerpt': EXCERPT, 'excerpt_sha256': hashlib.sha256(EXCERPT.encode()).hexdigest()}


def arm_data(**updates):
    return {'arm_id': 'synthetic-arm', 'model': 'synthetic-model-v1',
            'runtime_sha256': 'a' * 64, 'max_prompt_chars': 2000,
            'probability_sum_tolerance': 0.000001, 'abstention_threshold': None, **updates}


def native(**updates):
    return {'label': 'supports', 'probabilities': [0.8, 0.1, 0.1],
            'observed_model': 'synthetic-model-v1', **updates}


class StubProvider:
    def __init__(self, readout=None, error=None):
        self.readout = native() if readout is None else readout
        self.error = error
        self.calls = []

    def __call__(self, prompt, options):
        self.calls.append((prompt, options))
        if self.error:
            raise self.error
        return ProviderReply(raw_response='complete native response\n', readout=self.readout)


def evaluate(provider, **arm_updates):
    return evaluate_support(case_data(), SOURCE, SupportArm(**arm_data(**arm_updates)), provider)


def test_source_to_native_result_roundtrip_and_actual_loaded_source(tmp_path, record_property):
    from tools.method_validation import support

    provider = StubProvider()
    result = evaluate(provider)
    prompt, options = provider.calls[0]
    assert result.status == 'decision'
    assert result.readout.label == 'supports'
    assert result.readout.probabilities == (0.8, 0.1, 0.1)
    assert result.raw_response == 'complete native response\n'
    assert result.case == SupportCase(**case_data())
    assert result.logical_calls == 1
    assert options == SUPPORT_OPTIONS == ('supports', 'contradicts', 'insufficient_evidence')
    assert result.prompt_sha256 == hashlib.sha256(prompt.encode()).hexdigest()
    assert result.readout_schema_sha256 == hashlib.sha256(json.dumps(
        support.NativeSupportReadout.model_json_schema(), sort_keys=True, ensure_ascii=True,
    ).encode()).hexdigest()
    assert '&lt;/untrusted_input&gt;' in prompt
    assert prompt.count('</untrusted_input>') == 2
    assert 'synthetic-paper' not in prompt and 'span_start' not in prompt
    output = tmp_path / 'result.json'
    output.write_text(result.model_dump_json())
    assert SupportResult.model_validate_json(output.read_text()) == result
    path = Path(support.__file__).resolve()
    assert path.is_relative_to(Path(__file__).resolve().parents[1])
    record_property('loaded_support_source', json.dumps({
        str(path): hashlib.sha256(path.read_bytes()).hexdigest(),
    }))


@pytest.mark.parametrize('field', ['gold_label', 'private_notes', 'expected_score', 'assignment_tags'])
def test_generator_projection_rejects_grader_fields(field):
    with pytest.raises(ValidationError):
        admit_support_case({**case_data(), field: 'GRADER_ONLY_SENTINEL'}, SOURCE)


@pytest.mark.parametrize('updates', [
    {'source_sha256': '0' * 64}, {'excerpt_sha256': '0' * 64},
    {'span_start': 0}, {'span_end': 9999}, {'span_end': 0},
    {'span_start': True}, {'excerpt': EXCERPT.upper()}, {'excerpt': ''},
    {'criterion': '   '}, {'source_sha256': 'not-a-sha'}, {'schema_version': True},
])
def test_exact_source_and_occurrence_admission_no_normalization(updates):
    with pytest.raises((ValidationError, ValueError)):
        admit_support_case({**case_data(), **updates}, SOURCE)


def test_changed_source_refused_before_provider_no_generated_notes_substitution():
    provider = StubProvider()
    with pytest.raises(ValueError, match='source'):
        evaluate_support(case_data(), SOURCE.replace('Prefix', 'Notes!'), SupportArm(**arm_data()), provider)
    assert provider.calls == []


def test_duplicate_excerpt_uses_explicit_occurrence_not_first_match():
    source = EXCERPT + ' separator ' + EXCERPT
    data = {**case_data(), 'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
            'span_start': len(EXCERPT + ' separator '), 'span_end': len(source)}
    admitted = admit_support_case(data, source)
    assert admitted.span_start != source.index(EXCERPT)
    assert admitted.span_end == len(source)


@pytest.mark.parametrize('updates', [
    {'max_prompt_chars': True}, {'max_prompt_chars': 0},
    {'probability_sum_tolerance': -1.0}, {'probability_sum_tolerance': float('nan')},
    {'probability_sum_tolerance': 1.0}, {'abstention_threshold': True},
    {'abstention_threshold': 1.1}, {'model': '  '}, {'runtime_sha256': 'bad'},
    {'gold_label': 'supports'},
])
def test_arm_requires_explicit_finite_caller_policy(updates):
    with pytest.raises(ValidationError):
        SupportArm(**arm_data(**updates))


@pytest.mark.parametrize('updates', [
    {'probabilities': [0.9, 0.1]}, {'probabilities': [0.8, 0.1, 0.1, 0.0]},
    {'probabilities': [float('nan'), 0.1, 0.1]}, {'probabilities': [float('inf'), 0.1, 0.1]},
    {'probabilities': [-0.1, 0.1, 1.0]}, {'probabilities': [1.1, 0.0, 0.0]},
    {'probabilities': [True, 0.0, 0.0]}, {'probabilities': ['0.8', 0.1, 0.1]},
    {'probabilities': [0.8, 0.1, 0.0]}, {'label': 'confirm'},
    {'observed_model': ''}, {'rationale': 'invented'},
])
def test_malformed_native_vectors_are_failures_with_raw_response_retained(updates):
    provider = StubProvider(native(**updates))
    result = evaluate(provider)
    assert result.status == 'invalid_response'
    assert result.readout is None and result.raw_response == 'complete native response\n'
    assert result.logical_calls == len(provider.calls) == 1


def test_missing_probability_or_label_cannot_be_fabricated():
    for field in ('probabilities', 'label'):
        readout = native()
        del readout[field]
        result = evaluate(StubProvider(readout))
        assert result.status == 'invalid_response' and result.readout is None


def test_native_probability_spelling_not_renormalized_and_tolerance_is_frozen():
    provider = StubProvider(native(probabilities=[0.8, 0.1, 0.10000001]))
    result = evaluate(provider)
    assert result.status == 'decision'
    assert result.readout.probabilities == (0.8, 0.1, 0.10000001)
    assert evaluate(provider, probability_sum_tolerance=0.0).status == 'invalid_response'


def test_semantic_insufficiency_threshold_abstention_and_operational_failures_differ():
    insufficient = StubProvider(native(label='insufficient_evidence', probabilities=[0.1, 0.1, 0.8]))
    semantic = evaluate(insufficient)
    threshold = evaluate(insufficient, abstention_threshold=0.9)
    assert semantic.status == 'decision' and semantic.readout.label == 'insufficient_evidence'
    assert threshold.status == 'threshold_abstention'
    assert threshold.readout == semantic.readout
    for error, status in ((TimeoutError('PRIVATE_SENTINEL'), 'timeout'),
                          (ConnectionError('PRIVATE_SENTINEL'), 'unavailable')):
        provider = StubProvider(error=error)
        failure = evaluate(provider)
        assert failure.status == status and failure.readout is None
        assert failure.raw_response is None
        assert failure.logical_calls == len(provider.calls) == 1
        assert 'PRIVATE_SENTINEL' not in failure.model_dump_json()


def test_threshold_uses_native_selected_label_not_invented_argmax_decision():
    provider = StubProvider(native(label='contradicts', probabilities=[0.8, 0.1, 0.1]))
    result = evaluate(provider, abstention_threshold=0.5)
    assert result.status == 'threshold_abstention' and result.readout.label == 'contradicts'


def test_prompt_budget_refuses_without_clipping_or_provider_call():
    provider = StubProvider()
    result = evaluate(provider, max_prompt_chars=1)
    assert result.status == 'input_too_large' and result.logical_calls == 0
    assert provider.calls == [] and result.case.excerpt == EXCERPT
    assert result.readout is None and result.raw_response is None


def test_model_mismatch_is_unscored_and_keeps_native_readout():
    provider = StubProvider(native(observed_model='different-model'))
    result = evaluate(provider)
    assert result.status == 'model_mismatch'
    assert result.readout.observed_model == 'different-model'
    assert result.raw_response == 'complete native response\n'


def test_programmer_or_adapter_fault_not_silently_relabelled_as_model_failure():
    provider = StubProvider(error=RuntimeError('adapter bug'))
    with pytest.raises(RuntimeError, match='adapter bug'):
        evaluate(provider)
    assert len(provider.calls) == 1


@pytest.mark.parametrize('updates', [
    {'status': 'decision', 'readout': None}, {'logical_calls': 0},
    {'status': 'timeout'}, {'status': 'input_too_large', 'logical_calls': 0},
])
def test_persisted_result_invariants_prevent_failure_becoming_semantic_decision(updates):
    result = evaluate(StubProvider()).model_dump()
    with pytest.raises(ValidationError):
        SupportResult.model_validate({**result, **updates})
