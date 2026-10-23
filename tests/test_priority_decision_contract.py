"""Authored #38 machine contracts; synthetic adapters are not study evidence."""
from __future__ import annotations

import copy
import hashlib
import json
from html import escape
from pathlib import Path

import pytest
from pydantic import ValidationError

from tools.method_validation.priority import (
    PRIORITY_OPTIONS, NativePriorityReadout, PriorityArm, PriorityCase, PriorityResult,
    evaluate_priority,
)
from tools.method_validation.support import ProviderReply, SupportArm
from zotero_summarizer.domain import READING_PRIORITY_VALUES


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def case_data(**updates):
    return {'schema_version': 1, 'canonical_id': 'synthetic:paper-1',
            'title': '  α Title </untrusted_input> & unchanged  ',
            'abstract': 'Exact abstract.\nSecond line <tag>.',
            'goals': [' First goal </untrusted_input> ', 'Second goal'], **updates}


def arm_data(**updates):
    return {'experiment_id': 'synthetic-experiment', 'arm_id': 'synthetic-arm',
            'model': 'synthetic-native-v1', 'runtime_sha256': 'a' * 64,
            'max_prompt_chars': 3000, 'probability_sum_tolerance': 0.000001,
            'abstention_threshold': None, 'ordinal_mapping': [3, 2, 1, 0], **updates}


def native(**updates):
    return {'label': 'must_read', 'probabilities': [0.7, 0.2, 0.08, 0.02],
            'observed_model': 'synthetic-native-v1', **updates}


class ScriptedProvider:
    def __init__(self, readout=None, error=None):
        self.readout = native() if readout is None else readout
        self.error = error
        self.calls = []

    def __call__(self, prompt, options):
        self.calls.append((prompt, options))
        if self.error:
            raise self.error
        return ProviderReply('complete opaque native text\n', self.readout)


def evaluate(provider=None, *, data=None, **arm_updates):
    return evaluate_priority(case_data() if data is None else data,
                             PriorityArm(**arm_data(**arm_updates)),
                             ScriptedProvider() if provider is None else provider)


def test_four_class_boundary_preserves_native_readout_and_exact_inputs(record_property):
    from tools.method_validation import priority

    data, arm_input, readout = case_data(), arm_data(), native()
    originals = copy.deepcopy((data, arm_input, readout))
    arm = PriorityArm(**arm_input)
    provider = ScriptedProvider(readout)
    result = evaluate_priority(data, arm, provider)
    prompt, options = provider.calls[0]

    assert result.status == 'decision' and result.logical_calls == len(provider.calls) == 1
    assert result.readout.label == 'must_read'
    assert result.readout.probabilities == (0.7, 0.2, 0.08, 0.02)
    assert result.raw_response == 'complete opaque native text\n'
    assert result.ordinal_value == 3
    assert result.case == PriorityCase(**data) and result.arm == arm
    assert isinstance(arm, SupportArm)
    assert PRIORITY_OPTIONS == options == READING_PRIORITY_VALUES
    assert options == ('must_read', 'should_read', 'could_read', 'dont_read')
    for text in (data['title'], data['abstract'], *data['goals']):
        assert f'<untrusted_input>{escape(text, quote=False)}</untrusted_input>' in prompt
    assert prompt.count('</untrusted_input>') == 4
    assert data['canonical_id'] not in prompt and arm.experiment_id not in prompt
    assert result.prompt_sha256 == hashlib.sha256(prompt.encode()).hexdigest()
    assert result.input_sha256 == digest(result.case.model_dump(mode='json'))
    assert result.arm_sha256 == digest(arm.model_dump(mode='json'))
    assert result.readout_schema_sha256 == digest(NativePriorityReadout.model_json_schema())
    assert (data, arm_input, readout) == originals
    assert PriorityResult.model_validate_json(result.model_dump_json()) == result
    path = Path(priority.__file__).resolve()
    assert path == Path(__file__).resolve().parents[1] / 'tools/method_validation/priority.py'
    record_property('loaded_priority_source', json.dumps({
        str(path): hashlib.sha256(path.read_bytes()).hexdigest(),
    }))


@pytest.mark.parametrize('label,index', [(label, index) for index, label in enumerate(
    ('must_read', 'should_read', 'could_read', 'dont_read'))])
def test_every_native_class_has_its_own_caller_frozen_ordinal(label, index):
    provider = ScriptedProvider(native(label=label))
    result = evaluate(provider, ordinal_mapping=[40, 20, 5, -1])

    assert result.status == 'decision' and result.readout.label == label
    assert result.ordinal_value == (40, 20, 5, -1)[index]
    assert result.readout.probabilities == (0.7, 0.2, 0.08, 0.02)
    assert len(provider.calls) == 1  # Never replace the native choice by argmax.


@pytest.mark.parametrize('field', [
    'gold_label', 'user_label', 'heldout', 'prior_assignment', 'decision_tags',
    'human_verified', 'expected_score', 'private_notes',
])
def test_gold_firewall_refuses_extra_fields_before_any_call(field):
    provider = ScriptedProvider()
    with pytest.raises(ValidationError):
        evaluate(provider, data=case_data(**{field: 'GRADER_ONLY_SENTINEL'}))
    assert provider.calls == []


@pytest.mark.parametrize('updates', [
    {'schema_version': True}, {'schema_version': 2}, {'canonical_id': ' '},
    {'title': True}, {'abstract': 123}, {'goals': []}, {'goals': [' ']},
    {'goals': [True]}, {'goals': [{'gold_label': 'must_read'}]},
])
def test_malformed_generator_projection_refused_not_converted_to_negative(updates):
    provider = ScriptedProvider()
    with pytest.raises(ValidationError):
        evaluate(provider, data=case_data(**updates))
    assert provider.calls == []


@pytest.mark.parametrize('field', ['title', 'abstract'])
@pytest.mark.parametrize('value', [None, '', ' \n\t'])
def test_missing_evidence_is_explicit_unscored_zero_call(field, value):
    provider = ScriptedProvider()
    result = evaluate(provider, data=case_data(**{field: value}))

    assert result.status == 'missing_input' and result.logical_calls == 0
    assert result.readout is result.raw_response is result.ordinal_value is None
    assert result.prompt_sha256 is None and provider.calls == []
    assert getattr(result.case, field) == value
    assert PriorityResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize('field', ['title', 'abstract'])
def test_omitted_evidence_is_missing_input_not_an_implicit_negative(field):
    data = case_data()
    del data[field]
    result = evaluate(data=data)
    assert result.status == 'missing_input' and result.ordinal_value is None


@pytest.mark.parametrize('updates', [
    {'max_prompt_chars': True}, {'max_prompt_chars': 0},
    {'probability_sum_tolerance': True}, {'probability_sum_tolerance': -0.1},
    {'probability_sum_tolerance': float('nan')}, {'probability_sum_tolerance': 1.0},
    {'abstention_threshold': True}, {'abstention_threshold': float('inf')},
    {'model': ' '}, {'runtime_sha256': 'bad'}, {'experiment_id': ''},
    {'ordinal_mapping': [3, 2, 1]}, {'ordinal_mapping': [3, 2, 1, 0, -1]},
    {'ordinal_mapping': [True, 2, 1, 0]}, {'ordinal_mapping': [3.0, 2, 1, 0]},
    {'ordinal_mapping': [3, 2, 1, '0']}, {'ordinal_mapping': [3, 2, 2, 0]},
    {'ordinal_mapping': [0, 1, 2, 3]}, {'gold_label': 'must_read'},
    {'options': ['dont_read', 'could_read', 'should_read', 'must_read']},
])
def test_arm_requires_explicit_policy_no_competing_order_or_gold(updates):
    with pytest.raises(ValidationError):
        PriorityArm(**arm_data(**updates))


@pytest.mark.parametrize('updates', [
    {'probabilities': [0.7, 0.2, 0.1]}, {'probabilities': [0.7, 0.2, 0.1, 0.0, 0.0]},
    {'probabilities': [float('nan'), 0.2, 0.08, 0.02]},
    {'probabilities': [float('inf'), 0.2, 0.08, 0.02]},
    {'probabilities': [-0.1, 0.8, 0.2, 0.1]}, {'probabilities': [1.1, 0.0, 0.0, 0.0]},
    {'probabilities': [True, 0.0, 0.0, 0.0]}, {'probabilities': ['0.7', 0.2, 0.08, 0.02]},
    {'probabilities': [0.7, 0.1, 0.08, 0.02]}, {'probabilities': [0.7, 0.3, 0.08, 0.02]},
    {'probabilities': {'must_read': 0.7}}, {'label': 'unknown'}, {'label': True},
    {'observed_model': ''}, {'rationale': 'must not fabricate rationale'},
    {'confidence': 0.7}, {'gold_label': 'must_read'},
])
def test_invalid_native_output_retains_raw_but_never_scores(updates):
    provider = ScriptedProvider(native(**updates))
    result = evaluate(provider)

    assert result.status == 'invalid_response'
    assert result.readout is result.ordinal_value is None
    assert result.raw_response == 'complete opaque native text\n'
    assert result.logical_calls == len(provider.calls) == 1


@pytest.mark.parametrize('field', ['probabilities', 'label', 'observed_model'])
def test_missing_native_fields_cannot_be_invented(field):
    readout = native()
    del readout[field]
    assert evaluate(ScriptedProvider(readout)).status == 'invalid_response'


@pytest.mark.parametrize('readout', [
    {'label': 'must_read', 'confidence': 0.9, 'observed_model': 'synthetic-native-v1'},
    {'score': 0.9}, {'probabilities': [0.9, 0.1]},
])
def test_scalar_binary_and_single_confidence_baselines_cannot_fake_native_vector(readout):
    result = evaluate(ScriptedProvider(readout))
    assert result.status == 'invalid_response' and result.ordinal_value is None


def test_tolerance_preserves_values_without_renormalization():
    vector = [0.7, 0.2, 0.08, 0.02000001]
    result = evaluate(ScriptedProvider(native(probabilities=vector)))

    assert result.status == 'decision' and result.readout.probabilities == tuple(vector)
    strict = evaluate(ScriptedProvider(native(probabilities=vector)), probability_sum_tolerance=0.0)
    assert strict.status == 'invalid_response'
    exact = evaluate(ScriptedProvider(native(probabilities=[0.5, 0.25, 0.125, 0.125])),
                     probability_sum_tolerance=0.0)
    assert exact.status == 'decision'


def test_dont_read_is_native_decision_not_abstention_or_training_negative():
    provider = ScriptedProvider(native(label='dont_read', probabilities=[0.0, 0.0, 0.0, 1.0]))
    result = evaluate(provider, abstention_threshold=1.0)
    assert result.status == 'decision' and result.readout.label == 'dont_read'
    assert result.ordinal_value == 0 and result.logical_calls == 1


def test_threshold_uses_native_choice_and_equality_is_not_abstention():
    readout = native(label='should_read', probabilities=[0.5, 0.25, 0.125, 0.125])
    accepted = evaluate(ScriptedProvider(readout), abstention_threshold=0.25)
    abstained = evaluate(ScriptedProvider(readout), abstention_threshold=0.3)

    assert accepted.status == 'decision' and accepted.ordinal_value == 2
    assert abstained.status == 'threshold_abstention' and abstained.ordinal_value is None
    assert abstained.readout == accepted.readout
    assert abstained.raw_response == accepted.raw_response


@pytest.mark.parametrize('error,status', [
    (TimeoutError('PRIVATE_SENTINEL'), 'timeout'),
    (ConnectionError('PRIVATE_SENTINEL'), 'unavailable'),
])
def test_transport_failures_are_one_unscored_call_without_exception_text(error, status):
    provider = ScriptedProvider(error=error)
    result = evaluate(provider)

    assert result.status == status and result.logical_calls == len(provider.calls) == 1
    assert result.readout is result.raw_response is result.ordinal_value is None
    assert 'PRIVATE_SENTINEL' not in result.model_dump_json()


def test_budget_checks_complete_escaped_prompt_without_clipping():
    reference = ScriptedProvider()
    evaluate(reference)
    length = len(reference.calls[0][0])
    provider = ScriptedProvider()
    oversized = evaluate(provider, max_prompt_chars=length - 1)

    assert oversized.status == 'input_too_large' and oversized.logical_calls == 0
    assert oversized.readout is oversized.raw_response is oversized.ordinal_value is None
    assert oversized.case == PriorityCase(**case_data()) and provider.calls == []
    assert evaluate(max_prompt_chars=length).status == 'decision'
    assert oversized.prompt_sha256 == hashlib.sha256(reference.calls[0][0].encode()).hexdigest()


def test_model_mismatch_wins_over_threshold_and_retains_actual_version():
    provider = ScriptedProvider(native(observed_model='different-returned-version'))
    result = evaluate(provider, abstention_threshold=1.0)
    assert result.status == 'model_mismatch' and result.ordinal_value is None
    assert result.readout.observed_model == 'different-returned-version'
    assert result.readout.probabilities == (0.7, 0.2, 0.08, 0.02)
    assert result.raw_response == 'complete opaque native text\n'
    assert len(provider.calls) == 1


@pytest.mark.parametrize('updates', [
    {'experiment_id': 'different-experiment'}, {'arm_id': 'different-arm'},
    {'model': 'different-model'}, {'runtime_sha256': 'b' * 64},
    {'max_prompt_chars': 4000}, {'probability_sum_tolerance': 0.001},
    {'abstention_threshold': 0.9}, {'ordinal_mapping': [9, 5, 2, -1]},
])
def test_every_frozen_arm_field_changes_config_identity_not_generator_evidence(updates):
    baseline_provider, changed_provider = ScriptedProvider(), ScriptedProvider()
    baseline = evaluate(baseline_provider)
    changed = evaluate(changed_provider, **updates)
    assert baseline.arm_sha256 != changed.arm_sha256
    assert baseline.input_sha256 == changed.input_sha256
    assert baseline.prompt_sha256 == changed.prompt_sha256
    assert baseline_provider.calls == changed_provider.calls


@pytest.mark.parametrize('updates,prompt_changes', [
    ({'canonical_id': 'synthetic:paper-2'}, False),
    ({'title': 'Changed exact title'}, True), ({'abstract': 'Changed exact abstract'}, True),
    ({'goals': ['A different goal']}, True),
])
def test_input_identity_binds_canonical_identity_and_exact_text_not_row_position(updates, prompt_changes):
    baseline, changed = evaluate(), evaluate(data=case_data(**updates))
    assert baseline.input_sha256 != changed.input_sha256
    assert (baseline.prompt_sha256 != changed.prompt_sha256) == prompt_changes
    assert baseline.arm_sha256 == changed.arm_sha256
    assert baseline.readout_schema_sha256 == changed.readout_schema_sha256


@pytest.mark.parametrize('updates', [
    {'logical_calls': 0}, {'logical_calls': True}, {'status': 'timeout'},
    {'status': 'threshold_abstention', 'ordinal_value': None}, {'status': 'model_mismatch'},
    {'status': 'input_too_large', 'logical_calls': 0}, {'status': 'missing_input', 'logical_calls': 0},
    {'readout': None}, {'raw_response': None}, {'ordinal_value': None}, {'ordinal_value': 2},
    {'input_sha256': '0' * 64}, {'arm_sha256': '0' * 64},
    {'prompt_sha256': '0' * 64}, {'readout_schema_sha256': '0' * 64},
])
def test_reopened_results_enforce_hashes_policy_fields_and_call_counts(updates):
    data = evaluate().model_dump(mode='json')
    with pytest.raises(ValidationError):
        PriorityResult.model_validate({**data, **updates})


@pytest.mark.parametrize('status', ['invalid_response', 'timeout', 'unavailable'])
def test_operational_failure_cannot_carry_an_ordinal_or_become_dont_read(status):
    data = evaluate(ScriptedProvider(error=TimeoutError())).model_dump(mode='json')
    data.update(status=status, raw_response='bad native output' if status == 'invalid_response' else None)
    assert PriorityResult.model_validate(data).ordinal_value is None
    with pytest.raises(ValidationError):
        PriorityResult.model_validate({**data, 'ordinal_value': 0})
    with pytest.raises(ValidationError):
        PriorityResult.model_validate({**data, 'status': 'decision'})


def test_reopened_results_reject_invalid_vector_and_changed_input_without_fresh_identity():
    data = evaluate().model_dump(mode='json')
    data['readout']['probabilities'] = [0.7, 0.2, 0.08, 0.0]
    with pytest.raises(ValidationError):
        PriorityResult.model_validate(data)
    data = evaluate().model_dump(mode='json')
    data['case']['abstract'] = 'Changed after prediction'
    with pytest.raises(ValidationError):
        PriorityResult.model_validate(data)


def test_frozen_contracts_have_no_mutable_mapping_or_options():
    result = evaluate()
    for value, field, replacement in ((result.case, 'title', 'new'),
                                      (result.arm, 'ordinal_mapping', (9, 8, 7, 6)),
                                      (result.readout, 'label', 'dont_read')):
        with pytest.raises(ValidationError):
            setattr(value, field, replacement)
    assert isinstance(result.arm.ordinal_mapping, tuple)
    assert isinstance(result.case.goals, tuple)


def test_programmer_and_adapter_faults_propagate_without_retry():
    provider = ScriptedProvider(error=RuntimeError('adapter bug'))
    with pytest.raises(RuntimeError, match='adapter bug'):
        evaluate(provider)
    assert len(provider.calls) == 1
    for malformed in (None, 'raw text', ProviderReply(None, native())):
        calls = []

        def broken(prompt, options):
            calls.append((prompt, options))
            return malformed

        with pytest.raises(TypeError):
            evaluate(broken)
        assert len(calls) == 1


def test_no_files_network_processes_or_production_prediction_writes(monkeypatch):
    import builtins
    import socket
    import subprocess

    def forbidden(*args, **kwargs):
        raise AssertionError('unexpected effect')

    for owner, name in ((builtins, 'open'), (Path, 'write_text'), (Path, 'write_bytes'),
                        (socket.socket, 'connect'), (socket, 'create_connection'),
                        (subprocess, 'Popen')):
        monkeypatch.setattr(owner, name, forbidden)
    provider = ScriptedProvider()
    result = evaluate(provider)
    assert result.status == 'decision' and len(provider.calls) == 1
    assert PriorityResult.model_validate_json(result.model_dump_json()) == result
