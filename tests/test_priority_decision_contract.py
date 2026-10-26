"""Current #38 machine contracts; scripted native transport is not study evidence."""
from __future__ import annotations

import copy
from html import unescape
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from tools.method_validation import priority, priority_run
from tools.method_validation.priority import Config, LABELS, ORDINAL, Paper, Result, identity, payload, project
from tools.method_validation.support import SupportArm, digest, encoded, loads, sha
from zotero_summarizer.domain import READING_PRIORITY_VALUES
from test_priority_fixtures import priority_env, write_ref
from test_priority_replay_states import rewrite


def configure(env, *, arm=None, **policy):
    values = env.cfg.model_dump()
    values.update(policy)
    if arm is not None:
        values['arms'][0].update(arm)
    env.cfg = Config.model_validate(values)


def evaluate(env):
    row = priority_run.base_row(identity(env.paper), env.paper, env.cfg.arms[0], env.gold)
    priority_run.evaluate(row, env.cfg.arms[0], env.cfg, payload(env.paper, env.cfg),
                          env.retained.append, transport=env.transport)
    return Result.model_validate(row)


def published(env):
    owner, report = priority_run.run(env.cfg, encoded(env.cfg.model_dump(mode='json')), transport=env.transport)
    return owner, loads((owner / 'case-0.json').read_bytes()), report


def write_paper(env, updates, *, omit=()):
    old = identity(env.paper)
    data = {**env.paper.model_dump(mode='json'), **updates}
    for field in omit:
        del data[field]
    expected = Paper.model_validate({**data, **dict.fromkeys(omit)})
    key = identity(expected)
    env.bundle['papers'][0] = data
    for label in env.truth['labels']:
        if label['identity'] == old:
            label['identity'] = key
    configure(env, inputs=write_ref(env.path.parent / 'inputs.json', env.bundle),
              gold=write_ref(env.path.parent / 'gold.json', env.truth))
    return key


def test_four_class_boundary_preserves_native_readout_and_exact_inputs(priority_env, record_property):
    env = priority_env
    originals = copy.deepcopy((env.paper.model_dump(), env.cfg.model_dump(), env.raw))
    owner, row, report = published(env)
    result = Result.model_validate(row)
    request = env.requests[0].content
    wire = loads(request)
    state = wire['state']
    data = json.loads(unescape(state.split('<untrusted_input>', 1)[1].split('</untrusted_input>', 1)[0]))
    assert data == {'goals': env.cfg.goals, 'title': env.paper.title, 'abstract': env.paper.abstract}
    assert state.count('</untrusted_input>') == 1 and '&lt;/untrusted_input&gt;' in state
    assert result.status == 'decision' and result.label == result.readout.label == 'must_read'
    assert result.readout.probabilities == (0.7, 0.2, 0.08, 0.02) and ORDINAL[result.label] == 3
    assert result.readout.usage.input_tokens == 40
    assert len(env.requests) == len(env.bundle['papers']) and result.telemetry['attempt'] == 1
    assert result.telemetry['dispatched'] is True and isinstance(env.cfg.arms[0], SupportArm)
    assert LABELS == READING_PRIORITY_VALUES == ('must_read', 'should_read', 'could_read', 'dont_read')
    assert tuple(wire['questions']['decision']['criteria']) == env.cfg.option_order == tuple(sorted(LABELS))
    assert result.case_sha256 == identity(env.paper) and result.request_sha256 == sha(request) == sha(env.request)
    assert (owner / result.terminal).read_bytes() == env.raw
    assert env.paper.paper_id not in state and env.cfg.attempt not in state and 'GRADER_ONLY_SENTINEL' not in state
    manifest = loads((owner / 'manifest.json').read_bytes())
    assert manifest['schema'] == priority_run.schema_identity() and manifest['code'] == priority_run.code_identity()
    assert (env.paper.model_dump(), env.cfg.model_dump(), env.raw) == originals
    assert Result.model_validate_json(result.model_dump_json()) == result
    assert priority_run.reopen(owner) == report and len(env.requests) == len(env.bundle['papers'])
    path = Path(priority.__file__).resolve()
    assert path == Path(__file__).resolve().parents[1] / 'tools/method_validation/priority.py'
    record_property('loaded_priority_source', json.dumps({str(path): sha(path.read_bytes())}))


@pytest.mark.parametrize('index,label', list(enumerate(LABELS)))
def test_every_native_class_has_its_own_caller_frozen_ordinal(priority_env, index, label):
    wire = loads(priority_env.raw)
    wire['answers']['decision']['choice'] = label
    priority_env.raw = encoded(wire)
    result = evaluate(priority_env)
    assert result.status == 'decision' and result.label == label and ORDINAL[result.label] == 3 - index
    assert result.readout.probabilities == (0.7, 0.2, 0.08, 0.02) and len(priority_env.requests) == 1
    with pytest.raises(ValidationError):
        Config.model_validate({**priority_env.cfg.model_dump(), 'ordinal_mapping': [40, 20, 5, -1]})


@pytest.mark.parametrize('field', ['gold_label', 'user_label', 'heldout', 'prior_assignment',
    'decision_tags', 'human_verified', 'expected_score', 'private_notes'])
def test_gold_firewall_refuses_extra_fields_before_any_call(priority_env, field):
    with pytest.raises(ValidationError):
        Paper.model_validate({**priority_env.paper.model_dump(), field: 'GRADER_ONLY_SENTINEL'})
    assert priority_env.requests == []


@pytest.mark.parametrize('updates', [{'schema_version': True}, {'schema_version': 2},
    {'canonical_ids': [' ']}, {'title': True}, {'abstract': 123}, {'paper_id': ' '},
    {'split': 'unknown'}, {'family': True}, {'private_notes': {'gold_label': 'must_read'}}])
def test_malformed_generator_projection_refused_not_converted_to_negative(priority_env, updates):
    with pytest.raises(ValidationError):
        Paper.model_validate({**priority_env.paper.model_dump(), **updates})
    assert priority_env.requests == []


@pytest.mark.parametrize('field', ['title', 'abstract'])
@pytest.mark.parametrize('value', [None, '', ' \n\t'])
def test_missing_evidence_is_explicit_unscored_zero_call(priority_env, field, value):
    key = write_paper(priority_env, {field: value})
    owner, row, report = published(priority_env)
    assert row['case_sha256'] == key and row['status'] == 'missing_input'
    result = Result.model_validate(row)
    assert result.label is result.readout is result.terminal is result.request_sha256 is None
    assert row['telemetry']['dispatched'] is False and len(priority_env.requests) == 11
    assert priority_run.reopen(owner) == report and len(priority_env.requests) == 11
    assert loads((owner / 'inputs.json').read_bytes())['papers'][0][field] == value


@pytest.mark.parametrize('field', ['title', 'abstract'])
def test_omitted_evidence_is_missing_input_not_an_implicit_negative(priority_env, field):
    key = write_paper(priority_env, {}, omit=(field,))
    assert field not in loads(priority_env.cfg.inputs.path.read_bytes())['papers'][0]
    owner, row, report = published(priority_env)
    assert row['case_sha256'] == key and row['status'] == 'missing_input' and row['label'] is None
    assert row['telemetry']['dispatched'] is False and len(priority_env.requests) == 11
    assert field not in loads((owner / 'inputs.json').read_bytes())['papers'][0]
    assert priority_run.reopen(owner) == report and len(priority_env.requests) == 11


@pytest.mark.parametrize('scope,updates', [
    ('config', {'max_prompt_chars': True}), ('config', {'max_prompt_chars': 0}),
    ('config', {'probability_sum_tolerance': True}), ('config', {'probability_sum_tolerance': -0.1}),
    ('config', {'probability_sum_tolerance': float('nan')}), ('config', {'probability_sum_tolerance': 1.0}),
    ('arm', {'abstention_threshold': True}), ('arm', {'abstention_threshold': float('inf')}),
    ('arm', {'model': ' '}), ('arm', {'runtime_sha256': 'bad'}), ('config', {'attempt': ''}),
    *[('config', {'ordinal_mapping': value}) for value in ([3, 2, 1], [3, 2, 1, 0, -1],
        [True, 2, 1, 0], [3.0, 2, 1, 0], [3, 2, 1, '0'], [3, 2, 2, 0], [0, 1, 2, 3])],
    ('arm', {'gold_label': 'must_read'}), ('config', {'option_order': list(reversed(LABELS))})])
def test_arm_requires_explicit_policy_no_competing_order_or_gold(priority_env, scope, updates):
    model = Config if scope == 'config' else SupportArm
    current = priority_env.cfg if scope == 'config' else priority_env.cfg.arms[0]
    with pytest.raises(ValidationError):
        model.model_validate({**current.model_dump(), **updates})
    assert priority_env.requests == []


@pytest.mark.parametrize('scope,updates', [
    *[('decision', {'probabilities': dict(zip(LABELS, v))}) for v in ([0.7, 0.2, 0.1],
        [float('nan'), 0.2, 0.08, 0.02], [float('inf'), 0.2, 0.08, 0.02], [-0.1, 0.8, 0.2, 0.1],
        [1.1, 0.0, 0.0, 0.0], [True, 0.0, 0.0, 0.0], ['0.7', 0.2, 0.08, 0.02],
        [0.7, 0.1, 0.08, 0.02], [0.7, 0.3, 0.08, 0.02])],
    ('decision', {'probabilities': dict.fromkeys((*LABELS, 'foreign'), 0.2)}),
    ('decision', {'probabilities': {'must_read': 0.7}}), ('decision', {'choice': 'unknown'}),
    ('decision', {'choice': True}), ('wire', {'model': ''}), ('decision', {'rationale': 'invented'}),
    ('wire', {'gold_label': 'must_read'}), ('decision', {'confidence': True})])
def test_invalid_native_output_retains_raw_but_never_scores(priority_env, scope, updates):
    wire = loads(priority_env.raw)
    (wire if scope == 'wire' else wire['answers']['decision']).update(updates)
    priority_env.raw = json.dumps(wire, ensure_ascii=False, allow_nan=True).encode()
    result = evaluate(priority_env)
    assert result.status == 'invalid_response' and result.readout is result.label is None
    assert priority_env.retained == [priority_env.raw] and len(priority_env.requests) == 1


@pytest.mark.parametrize('scope,field', [('decision', 'probabilities'), ('decision', 'choice'), ('wire', 'model')])
def test_missing_native_fields_cannot_be_invented(priority_env, scope, field):
    wire = loads(priority_env.raw)
    del (wire if scope == 'wire' else wire['answers']['decision'])[field]
    priority_env.raw = encoded(wire)
    assert evaluate(priority_env).status == 'invalid_response' and priority_env.retained == [priority_env.raw]


@pytest.mark.parametrize('answer', [{'choice': 'must_read', 'confidence': 0.9},
    {'score': 0.9}, {'probabilities': [0.9, 0.1]}])
def test_scalar_binary_and_single_confidence_baselines_cannot_fake_native_vector(priority_env, answer):
    wire = loads(priority_env.raw)
    wire['answers']['decision'] = answer
    priority_env.raw = encoded(wire)
    result = evaluate(priority_env)
    assert result.status == 'invalid_response' and result.label is None
    assert priority_env.retained == [priority_env.raw]


def test_tolerance_preserves_values_without_renormalization(priority_env):
    wire = loads(priority_env.raw)
    wire['answers']['decision']['probabilities']['dont_read'] = 0.02000001
    priority_env.raw = encoded(wire)
    result = evaluate(priority_env)
    assert result.status == 'decision' and result.readout.probabilities == (0.7, 0.2, 0.08, 0.02000001)
    configure(priority_env, probability_sum_tolerance=0.0)
    assert evaluate(priority_env).status == 'invalid_response'
    wire['answers']['decision']['probabilities'] = dict(zip(LABELS, (0.5, 0.25, 0.125, 0.125)))
    priority_env.raw = encoded(wire)
    assert evaluate(priority_env).status == 'decision'


def test_dont_read_is_native_decision_not_abstention_or_training_negative(priority_env):
    wire = loads(priority_env.raw)
    wire['answers']['decision'].update(choice='dont_read', probabilities=dict(zip(LABELS, (0.0, 0.0, 0.0, 1.0))))
    priority_env.raw = encoded(wire)
    configure(priority_env, arm={'abstention_threshold': 1.0})
    result = evaluate(priority_env)
    assert result.status == 'decision' and result.label == 'dont_read' and ORDINAL[result.label] == 0
    assert len(priority_env.requests) == 1


def test_threshold_uses_native_choice_and_equality_is_not_abstention(priority_env):
    wire = loads(priority_env.raw)
    wire['answers']['decision'].update(choice='should_read', probabilities=dict(zip(LABELS, (0.5, 0.25, 0.125, 0.125))))
    priority_env.raw = encoded(wire)
    configure(priority_env, arm={'abstention_threshold': 0.25})
    accepted = evaluate(priority_env)
    configure(priority_env, arm={'abstention_threshold': 0.3})
    abstained = evaluate(priority_env)
    assert accepted.status == 'decision' and ORDINAL[accepted.label] == 2
    assert abstained.status == 'threshold_abstention' and abstained.label is None
    assert abstained.readout == accepted.readout and priority_env.retained == [priority_env.raw] * 2


@pytest.mark.parametrize('error,status', [(httpx.ReadTimeout('PRIVATE_SENTINEL'), 'timeout'),
    (httpx.ConnectError('PRIVATE_SENTINEL'), 'unavailable')])
def test_transport_failures_are_one_unscored_call_without_exception_text(priority_env, error, status):
    priority_env.error = error
    result = evaluate(priority_env)
    assert result.status == status and result.label is result.readout is None
    assert len(priority_env.requests) == result.telemetry['attempt'] == 1 and priority_env.retained == [b'']
    assert 'PRIVATE_SENTINEL' not in result.model_dump_json()


def test_budget_checks_complete_escaped_prompt_without_clipping(priority_env):
    env = priority_env
    request = payload(env.paper, env.cfg)
    wire = loads(request)
    question = wire['questions']['decision']
    chars = len(wire['state']) + len(question['instructions']) + sum(map(len, question['criteria']))
    original = env.paper.model_dump()
    configure(env, max_prompt_chars=chars - 1)
    with pytest.raises(ValueError, match='input_too_large'):
        evaluate(env)
    assert env.requests == env.retained == [] and env.paper.model_dump() == original
    prepared = priority_run.prepare(env.cfg)
    assert prepared[5][identity(env.paper)] == 'input_too_large' and identity(env.paper) not in prepared[4]
    configure(env, max_prompt_chars=chars, max_request_bytes=len(request))
    assert payload(env.paper, env.cfg) == request and evaluate(env).status == 'decision'
    configure(env, max_request_bytes=len(request) - 1)
    with pytest.raises(ValueError, match='input_too_large'):
        payload(env.paper, env.cfg)
    assert len(env.requests) == 1


def test_model_mismatch_wins_over_threshold_and_retains_actual_version(priority_env):
    wire = loads(priority_env.raw)
    wire['model'] = 'different-returned-version'
    priority_env.raw = encoded(wire)
    configure(priority_env, arm={'abstention_threshold': 1.0})
    result = evaluate(priority_env)
    assert result.status == 'model_mismatch' and result.label is None
    assert result.readout.observed_model == 'different-returned-version'
    assert result.readout.probabilities == (0.7, 0.2, 0.08, 0.02)
    assert priority_env.retained == [priority_env.raw] and len(priority_env.requests) == 1


@pytest.mark.parametrize('scope,updates', [('config', {'attempt': 'different-attempt'}),
    ('arm', {'arm_id': 'different-arm'}), ('arm', {'model': 'different-model'}),
    ('arm', {'runtime_sha256': 'b' * 64}), ('config', {'max_prompt_chars': 4000}),
    ('config', {'probability_sum_tolerance': 0.001}), ('arm', {'abstention_threshold': 0.9})])
def test_every_frozen_arm_field_changes_config_identity_not_generator_evidence(priority_env, scope, updates):
    cfg, paper, request = priority_env.cfg, priority_env.paper, priority_env.request
    configure(priority_env, **({'arm': updates} if scope == 'arm' else updates))
    assert digest(cfg.model_dump(mode='json')) != digest(priority_env.cfg.model_dump(mode='json'))
    assert paper == priority_env.paper and identity(paper) == identity(priority_env.paper)
    assert payload(paper, cfg) == payload(paper, priority_env.cfg) == request


@pytest.mark.parametrize('updates,prompt_changes', [({'canonical_ids': ['synthetic:paper-2']}, False),
    ({'title': 'Changed exact title'}, True), ({'abstract': 'Changed exact abstract'}, True),
    ({'goals': 'A different goal'}, True)])
def test_input_identity_binds_canonical_identity_and_exact_text_not_row_position(priority_env, updates, prompt_changes):
    cfg, paper, request = priority_env.cfg, priority_env.paper, priority_env.request
    if 'goals' in updates:
        configure(priority_env, **updates)
        changed = paper
    else:
        changed = Paper.model_validate({**paper.model_dump(), **updates})
        assert digest(paper.model_dump(mode='json')) != digest(changed.model_dump(mode='json'))
    assert (payload(changed, priority_env.cfg) != request) is prompt_changes
    assert (identity(changed) != identity(paper)) is bool('title' in updates or 'abstract' in updates)
    if 'goals' not in updates:
        assert cfg == priority_env.cfg


@pytest.mark.parametrize('mutation', ['attempt', 'attempt_bool', 'dispatched', 'request', 'case', 'arm',
    'status', 'threshold', 'model', 'schema', 'input'])
def test_reopened_results_enforce_hashes_policy_fields_and_call_counts(priority_env, mutation):
    env = priority_env
    owner, row, report = published(env)
    assert priority_run.reopen(owner) == report
    before = (owner / row['terminal']).read_bytes()
    if mutation in ('attempt', 'attempt_bool', 'dispatched'):
        field, value = ('attempt', True if mutation == 'attempt_bool' else 0) if mutation != 'dispatched' else ('dispatched', False)
        row['telemetry'][field] = value
    elif mutation in ('request', 'case', 'arm'):
        field, value = {'request': ('request_sha256', '0' * 64), 'case': ('case_sha256', '0' * 64),
                        'arm': ('arm_id', 'unadmitted-arm')}[mutation]
        row[field] = value
    elif mutation in ('status', 'threshold'):
        row.update(status='timeout' if mutation == 'status' else 'threshold_abstention', label=None)
        if mutation == 'status':
            row['readout'] = None
    elif mutation == 'model':
        row['readout']['observed_model'] = 'not-the-terminal-model'
    elif mutation == 'schema':
        manifest = loads((owner / 'manifest.json').read_bytes())
        manifest['schema'] = '0' * 64
        (owner / 'manifest.json').write_bytes(encoded(manifest))
    else:
        bundle = loads((owner / 'inputs.json').read_bytes())
        bundle['papers'][0]['title'] += ' changed after publication'
        (owner / 'inputs.json').write_bytes(encoded(bundle))
    rewrite(owner, env.cfg, row)
    assert (owner / row['terminal']).read_bytes() == before
    with pytest.raises(ValueError):
        priority_run.reopen(owner)
    assert len(env.requests) == len(env.bundle['papers'])


@pytest.mark.parametrize('status', ['invalid_response', 'timeout', 'unavailable', 'blocked',
    'wall_budget', 'missing_input', 'input_too_large', 'response_too_large', 'encoded_response'])
def test_operational_failure_cannot_carry_an_ordinal_or_become_dont_read(priority_env, status):
    decision = evaluate(priority_env).model_dump(mode='json')
    failure = {**decision, 'status': status, 'label': None, 'readout': None}
    assert Result.model_validate(failure).label is None
    with pytest.raises(ValidationError):
        Result.model_validate({**failure, 'label': 'dont_read'})
    with pytest.raises(ValidationError):
        Result.model_validate({**failure, 'status': 'decision'})
    with pytest.raises(ValidationError):
        Result.model_validate({**failure, 'readout': decision['readout']})


def test_reopened_results_reject_invalid_vector_and_changed_input_without_fresh_identity(priority_env):
    owner, row, report = published(priority_env)
    original = copy.deepcopy(row)
    row['readout']['probabilities'] = [0.7, 0.2, 0.08, 0.0]
    rewrite(owner, priority_env.cfg, row)
    with pytest.raises(ValueError):
        priority_run.reopen(owner)
    rewrite(owner, priority_env.cfg, original)
    assert priority_run.reopen(owner) == report
    bundle = loads((owner / 'inputs.json').read_bytes())
    bundle['papers'][0]['abstract'] = 'Changed after prediction'
    (owner / 'inputs.json').write_bytes(encoded(bundle))
    rewrite(owner, priority_env.cfg, original)
    with pytest.raises(ValueError):
        priority_run.reopen(owner)


def test_frozen_contracts_have_no_mutable_mapping_or_options(priority_env):
    result = evaluate(priority_env)
    for value, field, replacement in ((priority_env.paper, 'title', 'new'),
        (priority_env.cfg, 'option_order', tuple(reversed(LABELS))), (result.readout, 'label', 'dont_read')):
        with pytest.raises(ValidationError):
            setattr(value, field, replacement)
    assert isinstance(priority_env.cfg.option_order, tuple) and isinstance(result.readout.probabilities, tuple)


def test_programmer_and_adapter_faults_propagate_without_retry(priority_env, monkeypatch):
    priority_env.error = RuntimeError('adapter bug')
    with pytest.raises(RuntimeError, match='adapter bug'):
        evaluate(priority_env)
    assert len(priority_env.requests) == 1 and priority_env.retained == []
    priority_env.error = None
    for malformed in (None, 'raw text', ('decision',)):
        before = len(priority_env.requests)
        monkeypatch.setattr(priority_run, 'project', lambda *args, **kwargs: malformed)
        with pytest.raises((TypeError, ValueError)):
            evaluate(priority_env)
        assert len(priority_env.requests) == before + 1


@pytest.mark.parametrize('fields', [('title',), ('abstract',), ('title', 'abstract')])
@pytest.mark.parametrize('omitted', [False, True])
def test_nullable_evidence_preserves_received_key_presence(priority_env, fields, omitted):
    raw = priority_env.paper.model_dump(mode='json')
    for field in fields:
        if omitted:
            del raw[field]
        else:
            raw[field] = None
    original = copy.deepcopy(raw)
    paper = Paper.model_validate(raw)
    assert raw == original and all(getattr(paper, field) is None for field in fields)
    assert all((field in paper.model_fields_set) is (not omitted) for field in fields)
    with pytest.raises(ValueError, match='missing_input'):
        payload(paper, priority_env.cfg)
    assert priority_env.requests == priority_env.retained == []


@pytest.mark.parametrize('status', ['threshold_abstention', 'model_mismatch'])
def test_nonoperational_native_readouts_remain_available(priority_env, status):
    decision = evaluate(priority_env).model_dump(mode='json')
    unscored = Result.model_validate({**decision, 'status': status, 'label': None})
    assert unscored.label is None and unscored.readout.model_dump(mode='json') == decision['readout']


def test_no_files_network_processes_or_production_prediction_writes(priority_env, monkeypatch):
    import builtins
    import socket
    import subprocess

    def forbidden(*args, **kwargs):
        raise AssertionError('unexpected effect')

    for owner, name in ((builtins, 'open'), (Path, 'write_text'), (Path, 'write_bytes'),
        (socket.socket, 'connect'), (socket, 'create_connection'), (subprocess, 'Popen')):
        monkeypatch.setattr(owner, name, forbidden)
    request = payload(priority_env.paper, priority_env.cfg)
    status, readout = project(priority_env.raw, priority_env.cfg.arms[0], priority_env.cfg)
    assert request == priority_env.request and status == 'decision' and readout['label'] == 'must_read'
    assert priority_env.requests == priority_env.retained == []
