"""Offline #37 protocol wiring; synthetic cases are not semantic acceptance."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from tools.method_validation import support, support_run
from tools.method_validation.decision_adapter import evaluate_support, payload
from tools.method_validation.support import (
    Config, SUPPORT_OPTIONS, SupportArm, SupportResult, admit_support_case, digest,
    encoded, loads, sha,
)
from test_support_fixtures import native_env, native_wire, rows_at
from test_support_replay_states import rewrite

EXCERPT = 'α original </untrusted_input> evidence.'
SOURCE = EXCERPT + '\nRepeated occurrence: ' + EXCERPT


def case_data():
    start = SOURCE.rindex(EXCERPT)
    return {'schema_version': 1, 'pair_id': 'public-pair', 'paper_key': 'public-paper',
            'criterion': 'Does this excerpt establish the criterion?',
            'source_sha256': sha(SOURCE.encode()), 'span_start': start,
            'span_end': start + len(EXCERPT), 'excerpt': EXCERPT,
            'excerpt_sha256': sha(EXCERPT.encode())}


def configure(env, *, arm=None, **policy):
    data = env.cfg.model_dump()
    data.update(policy)
    if arm is not None:
        data['arms'][0].update(arm)
    env.cfg = Config.model_validate(data)


def evaluate(env):
    return evaluate_support(env.case, env.cfg.arms[0], env.cfg, env.retained.append,
                            transport=env.transport)


def published(env):
    owner, report = support_run.run(env.cfg, encoded(env.raw_config), transport=env.transport)
    row = next(row for row in rows_at(owner) if row['arm_id'] == env.cfg.arms[0].arm_id)
    return owner, row, report


def test_source_to_native_result_roundtrip_and_actual_loaded_source(native_env, record_property):
    owner, row, report = published(native_env)
    result = SupportResult.model_validate(row)
    request = native_env.requests[0]
    wire = loads(request.content)
    state = wire['state']
    assert result.status == 'decision' and result.support is True
    assert result.readout.label == 'supports'
    assert result.readout.probabilities == (0.8, 0.1, 0.1)
    assert result.readout.usage.input_tokens == 123
    assert (owner / result.terminal).read_bytes() == native_env.raw
    assert native_env.original == SOURCE and native_env.case.model_dump() == case_data()
    assert result.case_sha256 == digest(case_data())
    assert result.telemetry['attempt'] == len(native_env.requests) == 1
    assert result.telemetry['dispatched'] is True
    assert tuple(wire['questions']['decision']['criteria']) == native_env.cfg.option_order
    assert SUPPORT_OPTIONS == ('supports', 'contradicts', 'insufficient_evidence')
    assert result.request_sha256 == sha(request.content) == sha(payload(native_env.case, native_env.cfg))
    manifest = loads((owner / 'manifest.json').read_bytes())
    assert manifest['schema'] == support_run.schema_identity()
    assert manifest['code'] == support_run.code_identity()
    assert '&lt;/untrusted_input&gt;' in state and state.count('</untrusted_input>') == 2
    assert native_env.case.paper_key not in state and 'span_start' not in state and SOURCE not in state
    assert SupportResult.model_validate_json(result.model_dump_json()) == result
    assert support_run.reopen(owner) == report and len(native_env.requests) == 1
    path = Path(support.__file__).resolve()
    assert path.is_relative_to(Path(__file__).resolve().parents[1])
    record_property('loaded_support_source', json.dumps({str(path): hashlib.sha256(path.read_bytes()).hexdigest()}))


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


def test_changed_source_refused_before_provider_no_generated_notes_substitution(native_env):
    with pytest.raises(ValueError, match='source'):
        admitted = admit_support_case(case_data(), SOURCE.replace('Repeated', 'Changed!'))
        evaluate_support(admitted, native_env.cfg.arms[0], native_env.cfg,
                         native_env.retained.append, transport=native_env.transport)
    assert native_env.requests == native_env.retained == []


def test_duplicate_excerpt_uses_explicit_occurrence_not_first_match():
    source = EXCERPT + ' separator ' + EXCERPT
    data = {**case_data(), 'source_sha256': sha(source.encode()),
            'span_start': len(EXCERPT + ' separator '), 'span_end': len(source)}
    admitted = admit_support_case(data, source)
    assert admitted.span_start != source.index(EXCERPT)
    assert admitted.span_end == len(source)


@pytest.mark.parametrize('owner,updates', [
    ('config', {'max_prompt_chars': True}), ('config', {'max_prompt_chars': 0}),
    ('config', {'probability_sum_tolerance': -1.0}),
    ('config', {'probability_sum_tolerance': float('nan')}),
    ('config', {'probability_sum_tolerance': 1.0}),
    ('arm', {'abstention_threshold': True}), ('arm', {'abstention_threshold': 1.1}),
    ('arm', {'model': '  '}), ('arm', {'runtime_sha256': 'bad'}),
    ('arm', {'gold_label': 'supports'}),
])
def test_arm_requires_explicit_finite_caller_policy(native_env, owner, updates):
    model = Config if owner == 'config' else SupportArm
    current = native_env.cfg if owner == 'config' else native_env.cfg.arms[0]
    with pytest.raises(ValidationError):
        model.model_validate({**current.model_dump(), **updates})
    assert native_env.requests == []


@pytest.mark.parametrize('scope,updates', [
    ('decision', {'probabilities': {'supports': 0.9, 'contradicts': 0.1}}),
    ('decision', {'probabilities': {'supports': 0.8, 'contradicts': 0.1, 'insufficient_evidence': 0.1, 'foreign': 0.0}}),
    ('decision', {'probabilities': [0.8, 0.1, 0.1]}),
    ('decision', {'probabilities': {'supports': float('nan'), 'contradicts': 0.1, 'insufficient_evidence': 0.1}}),
    ('decision', {'probabilities': {'supports': float('inf'), 'contradicts': 0.1, 'insufficient_evidence': 0.1}}),
    ('decision', {'probabilities': {'supports': -0.1, 'contradicts': 0.1, 'insufficient_evidence': 1.0}}),
    ('decision', {'probabilities': {'supports': 1.1, 'contradicts': 0.0, 'insufficient_evidence': 0.0}}),
    ('decision', {'probabilities': {'supports': True, 'contradicts': 0.0, 'insufficient_evidence': 0.0}}),
    ('decision', {'probabilities': {'supports': '0.8', 'contradicts': 0.1, 'insufficient_evidence': 0.1}}),
    ('decision', {'probabilities': {'supports': 0.8, 'contradicts': 0.1, 'insufficient_evidence': 0.0}}),
    ('decision', {'choice': 'confirm'}), ('wire', {'model': ''}),
    ('decision', {'rationale': 'invented'}),
])
def test_malformed_native_vectors_are_failures_with_raw_response_retained(native_env, scope, updates):
    wire = native_wire()
    (wire if scope == 'wire' else wire['answers']['decision']).update(updates)
    native_env.raw = json.dumps(wire, ensure_ascii=False, allow_nan=True).encode()
    result = evaluate(native_env)
    assert result.status == 'invalid_response'
    assert result.readout is None and result.raw_response == native_env.raw
    assert native_env.retained == [native_env.raw]
    assert result.telemetry['attempt'] == len(native_env.requests) == 1


@pytest.mark.parametrize('field', ['probabilities', 'choice'])
def test_missing_probability_or_label_cannot_be_fabricated(native_env, field):
    wire = native_wire()
    del wire['answers']['decision'][field]
    native_env.raw = encoded(wire)
    result = evaluate(native_env)
    assert result.status == 'invalid_response' and result.readout is None
    assert native_env.retained == [native_env.raw] and len(native_env.requests) == 1


def test_native_probability_spelling_not_renormalized_and_tolerance_is_frozen(native_env):
    wire = native_wire()
    wire['answers']['decision']['probabilities']['insufficient_evidence'] = 0.10000001
    native_env.raw = encoded(wire)
    result = evaluate(native_env)
    assert result.status == 'decision'
    assert result.readout['probabilities'] == [0.8, 0.1, 0.10000001]
    configure(native_env, probability_sum_tolerance=0.0)
    assert evaluate(native_env).status == 'invalid_response'
    assert native_env.retained == [native_env.raw, native_env.raw]


def test_semantic_insufficiency_threshold_abstention_and_operational_failures_differ(native_env):
    wire = native_wire()
    wire['answers']['decision'].update(choice='insufficient_evidence', probabilities={
        'supports': 0.1, 'contradicts': 0.1, 'insufficient_evidence': 0.8})
    native_env.raw = encoded(wire)
    semantic = evaluate(native_env)
    configure(native_env, arm={'abstention_threshold': 0.9})
    threshold = evaluate(native_env)
    assert semantic.status == 'decision' and semantic.readout['label'] == 'insufficient_evidence'
    assert threshold.status == 'threshold_abstention' and threshold.readout == semantic.readout
    for error, status in ((httpx.ReadTimeout('PRIVATE_SENTINEL'), 'timeout'),
                          (httpx.ConnectError('PRIVATE_SENTINEL'), 'unavailable')):
        native_env.error = error
        before = len(native_env.requests)
        failure = evaluate(native_env)
        assert failure.status == status and failure.readout is None
        assert failure.raw_response == b'' and native_env.retained[-1] == b''
        assert len(native_env.requests) == before + 1 and failure.telemetry['attempt'] == 1
        assert 'PRIVATE_SENTINEL' not in json.dumps(failure.telemetry)


def test_threshold_uses_native_selected_label_not_invented_argmax_decision(native_env):
    wire = native_wire()
    wire['answers']['decision']['choice'] = 'contradicts'
    native_env.raw = encoded(wire)
    configure(native_env, arm={'abstention_threshold': 0.5})
    result = evaluate(native_env)
    assert result.status == 'threshold_abstention' and result.readout['label'] == 'contradicts'
    assert result.readout['probabilities'] == [0.8, 0.1, 0.1]


def test_prompt_budget_refuses_without_clipping_or_provider_call(native_env):
    configure(native_env, max_prompt_chars=1)
    before = native_env.case.model_dump()
    with pytest.raises(ValueError, match='cap'):
        evaluate(native_env)
    assert native_env.requests == native_env.retained == []
    assert native_env.case.model_dump() == before and native_env.case.excerpt == EXCERPT
    with pytest.raises(ValueError, match='cap'):
        support_run.run(native_env.cfg, encoded(native_env.raw_config), transport=native_env.transport)
    assert not native_env.cfg.project_root.exists()


def test_model_mismatch_is_unscored_and_keeps_native_readout(native_env):
    native_env.raw = encoded(native_wire(model='different-model'))
    result = evaluate(native_env)
    assert result.status == 'model_mismatch'
    assert result.readout['observed_model'] == 'different-model'
    assert result.raw_response == native_env.raw and native_env.retained == [native_env.raw]


def test_programmer_or_adapter_fault_not_silently_relabelled_as_model_failure(native_env):
    native_env.error = RuntimeError('adapter bug')
    with pytest.raises(RuntimeError, match='adapter bug'):
        evaluate(native_env)
    assert len(native_env.requests) == 1 and native_env.retained == []


@pytest.mark.parametrize('updates', [
    {'status': 'decision', 'readout': None}, {'support': False},
    {'status': 'timeout'}, {'status': 'wall_budget', 'support': None},
])
def test_persisted_result_invariants_prevent_failure_becoming_semantic_decision(native_env, updates):
    _, row, _ = published(native_env)
    with pytest.raises(ValidationError):
        SupportResult.model_validate({**row, **updates})


@pytest.mark.parametrize('mutation', ['attempt', 'attempt_bool', 'reservation', 'reservation_bool',
    'foreign', 'dispatched', 'retained', 'request', 'http', 'complete'])
def test_persisted_native_accounting_and_inventory_match_admitted_work(native_env, mutation):
    owner, row, report = published(native_env)
    assert support_run.reopen(owner) == report
    terminal_before = (owner / row['terminal']).read_bytes()
    manifest = loads((owner / 'manifest.json').read_bytes())
    if mutation in ('attempt', 'attempt_bool'):
        row['telemetry']['attempt'] = True if mutation == 'attempt_bool' else 0
    elif mutation in ('reservation', 'reservation_bool'):
        manifest['reserved_calls'] = True if mutation == 'reservation_bool' else 0
    elif mutation == 'foreign':
        foreign = owner / 'foreign.bin'
        foreign.write_bytes(b'unadmitted private fixture file')
        manifest['files'][foreign.name] = sha(foreign.read_bytes())
    else:
        field, value = {'dispatched': ('dispatched', False),
            'retained': ('retained_bytes', len(terminal_before) + 1),
            'request': ('request_bytes', row['telemetry']['request_bytes'] + 1),
            'http': ('http_status', 503), 'complete': ('raw_complete', False)}[mutation]
        row['telemetry'][field] = value
    (owner / 'manifest.json').write_bytes(encoded(manifest))
    rewrite(owner, native_env.cfg, support_run.prepare(native_env.cfg)[0], row)
    assert (owner / row['terminal']).read_bytes() == terminal_before
    with pytest.raises(ValueError):
        support_run.reopen(owner)
