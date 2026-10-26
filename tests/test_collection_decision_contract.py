"""Original #42 machine intents at the current snapshot/native/artifact owners.

Synthetic controls are not human membership, model-quality or authenticated-lineage evidence.
"""
from copy import deepcopy
from html import unescape
import json
from pathlib import Path

import httpx
import pytest

from _collection_contract_fixtures import contract_bundle, declared_bundle, reseal_files
from test_collection_fixtures import bind, identity, response, save
from test_collection_native import native
from test_collection_replay import reseal
from tools.method_validation.collections import Config, Inputs, Result, Truth, admit, payload, project
from tools.method_validation.collections_report import paper_outcomes
from tools.method_validation.collections_run import prepare, reopen, run, schema_identity
from tools.method_validation.support import SupportArm, digest, encoded, sha


def test_multiple_memberships_exact_options_roundtrip_and_no_parent_inference(tmp_path, record_property):
    config, _, _, path = contract_bundle(tmp_path)
    cfg = Config.model_validate(config)
    owner, result = run(cfg, path.read_bytes())
    rows = [json.loads((owner / f'case-{i}.json').read_bytes()) for i in range(3)]
    assert [r['node_key'] for r in rows] == ['A', 'B', 'C']
    assert [r['label'] for r in rows] == ['does_not_belong', 'belongs', 'belongs']
    outcome = next(iter(paper_outcomes(rows, ('A', 'B', 'C'), {'A', 'B', 'C'}).values()))
    assert outcome['selected'] == ['B', 'C'] and not outcome['confirmed_no_match']
    prepared = prepare(cfg)
    from tools.method_validation import collections
    for row in rows:
        request = prepared[6][row['case_sha256']]
        assert tuple(json.loads(request)['questions']['decision']['criteria']) == collections.LABELS == (
            'belongs', 'does_not_belong', 'insufficient_evidence')
        assert row['request_sha256'] == sha(request)
        assert (owner / row['terminal']).read_bytes() == prepared[9][row['case_sha256'], 'recorded']
        assert Result.model_validate_json(encoded(row)) == Result.model_validate(row)
    assert json.loads((owner / 'manifest.json').read_bytes())['schema'] == schema_identity()
    assert reopen(owner) == result
    source = Path(collections.__file__).resolve()
    assert source.is_relative_to(Path(__file__).resolve().parents[1])
    record_property('loaded_collection_source', json.dumps({str(source): sha(source.read_bytes())}))


def test_explicit_eligibility_not_folder_name_inference_and_same_shortlist_across_arms(tmp_path):
    config, inputs, truth, path = contract_bundle(tmp_path)
    inputs['tree']['nodes'][1]['name'] = 'Inbox'
    inputs['tree'].update(selected_keys=['B'], selection_provenance='SYNTHETIC frozen non-gold selector')
    config['arms'].append({**config['arms'][0], 'arm_id': 'second'})
    inputs['recordings'] = [r for r in inputs['recordings'] if r['case_sha256'] in {
        digest([identity(p), 'B']) for p in inputs['papers']}]
    inputs['recordings'] += [{**r, 'arm_id': 'second'} for r in inputs['recordings']]
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    owner, result = run(cfg, path.read_bytes())
    rows = [json.loads(p.read_bytes()) for p in owner.glob('case-*.json')]
    assert {r['node_key'] for r in rows if r['capability'] == 'native_membership'} == {'B'}
    assert result['arms']['recorded'] == result['arms']['second']
    assert result['selected_question_keys'] == ['B'] and result['eligible_keys'] == ['A', 'B', 'C']
    assert result['arms']['recorded']['confirmed_native_no_match'] == 0
    assert reopen(owner) == result


@pytest.mark.parametrize('updates', [
    {'selected_keys': ['W']}, {'selected_keys': ['missing']}, {'selected_keys': ['B', 'B']},
    {'selected_keys': []}, {'selected_keys': ['B']}, {'selection_provenance': 'hidden selection'},
    {'eligible_keys': ['A']}, {'gold_collections': ['B']},
])
def test_invalid_eligibility_or_selection_refused(tmp_path, updates):
    config, inputs, truth, _ = contract_bundle(tmp_path)
    inputs['tree'].update(updates)
    with pytest.raises(ValueError):
        admit(Config.model_validate(config), Inputs.model_validate(inputs), Truth.model_validate(truth))
    assert not (tmp_path / 'data').exists()


@pytest.mark.parametrize('mutation', ['duplicate_key', 'unknown_parent', 'cycle', 'wrong_path', 'role'])
def test_hierarchy_is_complete_and_keys_not_names_own_identity(tmp_path, mutation):
    config, inputs, truth, _ = contract_bundle(tmp_path)
    nodes = inputs['tree']['nodes']
    if mutation == 'duplicate_key': nodes[1]['key'] = 'A'
    if mutation == 'unknown_parent': nodes[1]['parent'] = 'missing'
    if mutation == 'cycle': nodes[0]['parent'] = 'B'
    if mutation == 'wrong_path': nodes[1]['path'] = ['Project']
    if mutation == 'role': nodes[1]['role'] = 'guessed'
    with pytest.raises(ValueError):
        admit(Config.model_validate(config), Inputs.model_validate(inputs), Truth.model_validate(truth))


def test_purge_all_heldout_versions_and_mixed_derivations_without_mutating_inputs(tmp_path):
    config, inputs, truth, _ = declared_bundle(tmp_path)
    before = deepcopy((config, inputs, truth))
    prepared = prepare(Config.model_validate(save(tmp_path, config, inputs, truth)))
    assert (config, inputs, truth) == before
    audit = json.loads(prepared[-1]['lineage-audit.json'])
    assert audit['active_reference_ids'] == ['safe', 'dev-example']
    removed = {r['record']['entry_id']: r for r in audit['removed']}
    assert {'direct', 'prior-version', 'copy', 'mixed', 'transitive', 'heldout-example'} <= removed.keys()
    assert [(r['record']['channel'], r['record']['entry_id']) for r in audit['removed']
            if r['record']['channel'] is not None] == [
        ('baseline', 'direct'), ('baseline', 'prior-version'), ('baseline', 'copy'),
        ('baseline', 'mixed'), ('baseline', 'transitive'), ('example', 'heldout-example')]
    assert removed['mixed']['heldout_source_ids'] == removed['transitive']['heldout_source_ids'] == ['source:prior']
    assert removed['mixed']['record'] == next(e for e in inputs['lineage']['entries'] if e['entry_id'] == 'mixed')
    assert removed['source:prior']['record']['paper']['version_id'] == '0-v1'
    assert removed['source:copy-source']['record']['paper']['version_id'] == inputs['papers'][0]['version_id']
    assert removed['heldout-example']['record']['payload']['payload'] == 'REFERENCE_ONLY_SENTINEL'
    assert audit['qualification'] == 'declared_only_not_authenticated'


@pytest.mark.parametrize('mutation', ['unknown_source', 'no_source', 'blank_derivation', 'duplicate_entry'])
def test_incomplete_lineage_fails_not_silent_cull(tmp_path, mutation):
    config, inputs, truth, path = declared_bundle(tmp_path)
    entries = inputs['lineage']['entries']
    entry = next(e for e in entries if e['entry_id'] == 'safe')
    if mutation == 'unknown_source': entry['source_ids'] = ['missing']
    if mutation == 'no_source': entry['source_ids'] = []
    if mutation == 'blank_derivation': entry['derivation'] = ' '
    if mutation == 'duplicate_entry': entries.append(deepcopy(entry))
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    with pytest.raises(ValueError): run(cfg, path.read_bytes())
    assert not (tmp_path / 'data').exists()


@pytest.mark.parametrize('mutation', ['missing_identity', 'duplicate_key', 'family_leak',
                                      'late_development', 'version_collision', 'missing_time'])
def test_frozen_family_time_identity_is_required(tmp_path, mutation):
    config, inputs, truth, path = declared_bundle(tmp_path)
    entries = inputs['lineage']['entries']
    leaf = entries[0]
    if mutation == 'missing_identity': leaf['paper']['family'] = ''
    if mutation == 'duplicate_key': entries[1]['paper']['paper_id'] = leaf['paper']['paper_id']
    if mutation == 'family_leak': entries[3]['paper']['family'] = 'family-0'
    if mutation == 'late_development':
        entries[3]['paper']['observed_at'] = entries[3]['observed_at'] = truth['split_frozen_at']
    if mutation == 'version_collision': entries[1]['paper']['version_id'] = leaf['paper']['version_id']
    if mutation == 'missing_time': del leaf['paper']['observed_at']
    for entry in entries:
        if entry['paper'] is not None:
            entry['payload'] = deepcopy(entry['paper'])
            entry['payload_sha256'] = digest(entry['paper'])
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    with pytest.raises(ValueError): run(cfg, path.read_bytes())
    assert not (tmp_path / 'data').exists()


def test_no_fuzzy_identity_match_and_unknown_candidate_refused_before_call(tmp_path):
    config, inputs, truth, path = contract_bundle(tmp_path)
    inputs['recordings'][0]['case_sha256'] = 'f' * 64
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    with pytest.raises(ValueError): prepare(cfg)
    inputs['recordings'] = []
    truth['labels'][0]['identity'] = 'e' * 64
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    with pytest.raises(ValueError): run(cfg, path.read_bytes())
    assert not (tmp_path / 'data').exists()


@pytest.mark.parametrize('field', ['heldout_assignments', 'assignment_tags', 'gold_collections',
                                  'human_truth', 'private_notes', 'examples'])
def test_gold_firewall_rejects_extra_generator_fields_before_call(tmp_path, field):
    config, inputs, truth, path = contract_bundle(tmp_path)
    inputs['papers'][0][field] = 'GRADER_ONLY_SENTINEL'
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    with pytest.raises(ValueError): run(cfg, path.read_bytes())
    assert not (tmp_path / 'data').exists()


def test_generator_only_candidate_and_one_key_path_description_not_references_or_identities(tmp_path):
    config, inputs, truth, _ = declared_bundle(tmp_path)
    before = deepcopy((inputs, truth))
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    prepared = prepare(cfg)
    assert (inputs, truth) == before
    papers, nodes = prepared[2], prepared[4]
    _, _, _, paths, _ = admit(cfg, prepared[0], prepared[1])
    for node in ('A', 'B', 'C'):
        wire = json.loads(payload(next(iter(papers.values())), node, (nodes, paths, prepared[0].tree), cfg))
        state = wire['state']
        assert state.count('</untrusted_input>') == 1 and '&lt;/untrusted_input&gt;' in state
        value = json.loads(unescape(state.split('<untrusted_input>', 1)[1].split('</untrusted_input>')[0]))
        assert value == {'title': inputs['papers'][0]['title'], 'abstract': inputs['papers'][0]['abstract'],
            'collection_key': node, 'full_path': [{'key': k, 'name': nodes[k].name} for k in paths[node]],
            'description': nodes[node].description, 'examples': []}
        assert not {'paper_id', 'version_id', 'family', 'canonical_ids', 'lineage', 'memberships'} & value.keys()
        assert 'REFERENCE_ONLY_SENTINEL' not in state


def test_missing_input_and_workbudget_refuse_with_zero_calls(tmp_path):
    config, inputs, truth, _ = contract_bundle(tmp_path)
    inputs['papers'][0]['title'] = inputs['papers'][0]['abstract'] = None
    truth['labels'][0]['identity'] = identity(inputs['papers'][0])
    inputs['recordings'] = inputs['recordings'][3:]
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    prepared = prepare(cfg)
    assert len(prepared[6]) == 6 and list(prepared[7].values()).count('missing_input') == 4
    values = cfg.model_dump()
    for invalid in (True, -1, 1.5):
        with pytest.raises(ValueError): Config.model_validate({**values, 'max_calls': invalid})
    for limit in (1, 11):  # 3 papers * (3 questions + 1 baseline) = 12 outcomes.
        with pytest.raises(ValueError): prepare(Config.model_validate({**values, 'max_work_units': limit}))
    assert not (tmp_path / 'data').exists()


def test_oversize_input_not_clipped_and_zero_actions(tmp_path):
    config, inputs, truth, path = contract_bundle(tmp_path)
    config['max_prompt_chars'] = 1
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    before = deepcopy(inputs)
    owner, result = run(cfg, path.read_bytes())
    assert inputs == before and not list(owner.glob('terminal-*.bin'))
    assert result['arms']['recorded']['question_statuses'] == {'input_too_large': 9}
    assert result['arms']['recorded']['complete_papers'] == 0
    assert result['winner'] is None and result['action'] == 'retain_baseline'
    assert reopen(owner) == result


@pytest.mark.parametrize('updates', [
    {'choice': 'supports'}, {'choice': 'Belongs'}, {'probabilities': [.9, .1]},
    {'probabilities': [0.8, .1, .1, 0]}, {'probabilities': dict(belongs=True, does_not_belong=0, insufficient_evidence=0)},
    *[{'probabilities': dict(belongs=v, does_not_belong=.1, insufficient_evidence=.1)}
      for v in ('0.8', float('nan'), float('inf'), -.1, 1.1, .7)],
    {'rationale': 'invented'}, {'collection_key': 'unknown'},
])
def test_invalid_native_vector_or_domain_label_is_operational_not_no_match(tmp_path, updates):
    config, _, _, _ = contract_bundle(tmp_path)
    cfg = Config.model_validate(config)
    wire = json.loads(response())
    wire['answers']['decision'].update(updates)
    raw = json.dumps(wire).encode()  # Deliberately includes nonstandard NaN/Infinity terminals.
    assert project(raw, cfg.arms[0], cfg) == ('invalid_response', None)
    assert project(response(model=' '), cfg.arms[0], cfg) == ('invalid_response', None)


@pytest.mark.parametrize('field', ['model', 'choice', 'probabilities'])
def test_missing_native_fields_are_not_fabricated(tmp_path, field):
    config, _, _, _ = contract_bundle(tmp_path)
    cfg = Config.model_validate(config)
    wire = json.loads(response())
    del (wire if field == 'model' else wire['answers']['decision'])[field]
    assert project(encoded(wire), cfg.arms[0], cfg) == ('invalid_response', None)


def test_preserve_native_vector_and_selected_label_not_argmax_and_tolerance(tmp_path):
    config, _, _, _ = contract_bundle(tmp_path)
    cfg = Config.model_validate(config)
    raw = response('does_not_belong', probabilities=dict(belongs=.8, does_not_belong=.1,
                                                        insufficient_evidence=.10000001))
    status, readout = project(raw, cfg.arms[0], cfg)
    assert status == 'decision' and readout['label'] == 'does_not_belong'
    assert readout['probabilities'] == [.8, .1, .10000001]
    threshold = SupportArm.model_validate({**cfg.arms[0].model_dump(), 'abstention_threshold': .5})
    assert project(raw, threshold, cfg)[0] == 'threshold_abstention'
    strict = Config.model_validate({**cfg.model_dump(), 'probability_sum_tolerance': 0.0})
    assert project(raw, cfg.arms[0], strict) == ('invalid_response', None)


def test_no_match_insufficient_threshold_and_operational_errors_are_distinct(tmp_path):
    cfg, path = native(tmp_path)
    outcomes = [('no', response('does_not_belong'), 200, 'decision', 3),
                ('unknown', response('insufficient_evidence'), 200, 'decision', 0),
                ('mismatch', response(model='different'), 200, 'model_mismatch', 0),
                ('unavailable', response(), 503, 'unavailable', 0),
                ('timeout', None, None, 'timeout', 0)]
    for name, raw, code, status, confirmed in outcomes:
        local = Config.model_validate({**cfg.model_dump(), 'attempt': name})
        calls = []
        def server(request):
            calls.append(request.content)
            if code is None: raise httpx.ReadTimeout('PRIVATE_SENTINEL', request=request)
            return httpx.Response(code, content=raw)
        owner, result = run(local, path.read_bytes(), transport=httpx.MockTransport(server))
        assert len(calls) == 6 and result['arms']['recorded']['question_statuses'] == {status: 6}
        assert result['arms']['recorded']['confirmed_native_no_match'] == confirmed
        assert 'PRIVATE_SENTINEL' not in (owner / 'report.json').read_text()
        assert reopen(owner) == result


def test_adapter_fault_propagates_not_training_negative(tmp_path):
    cfg, path = native(tmp_path)
    calls = []
    def fault(request):
        calls.append(request)
        raise RuntimeError('programmer fault')
    with pytest.raises(RuntimeError): run(cfg, path.read_bytes(), transport=httpx.MockTransport(fault))
    assert len(calls) == 1
    owner = tmp_path / 'data/collection_comparison/fixture'
    assert (owner / 'FAILED').exists() and not (owner / 'ACK').exists()


@pytest.mark.parametrize('updates', [
    {'selected_keys': ['W']}, {'status': 'timeout'}, {'node_key': 'unknown'},
    {'request_sha256': 'f' * 64}, {'gold_memberships': []},
])
def test_result_reopen_cannot_turn_failures_or_unknown_keys_into_decisions(tmp_path, updates):
    config, _, _, path = contract_bundle(tmp_path)
    cfg = Config.model_validate(config)
    owner, _ = run(cfg, path.read_bytes())
    row = json.loads((owner / 'case-0.json').read_bytes())
    row.update(updates)
    (owner / 'case-0.json').write_bytes(encoded(row))
    reseal(owner, cfg)
    with pytest.raises(ValueError): reopen(owner)


@pytest.mark.parametrize('mutation', ['active_reference', 'removal_evidence', 'removed_record', 'dependent_use'])
def test_purge_reopen_cannot_restore_heldout_sources_or_forge_audit(tmp_path, mutation):
    config, inputs, truth, path = declared_bundle(tmp_path)
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    owner, _ = run(cfg, path.read_bytes())
    audit = json.loads((owner / 'lineage-audit.json').read_bytes())
    if mutation == 'active_reference': audit['active_reference_ids'].append('mixed')
    if mutation == 'removal_evidence': audit['removed'][0]['heldout_source_ids'] = []
    if mutation == 'removed_record': audit['removed'][0]['record']['derivation'] = 'changed'
    if mutation == 'dependent_use': audit['uses'][0]['entry_id'] = 'mixed'
    (owner / 'lineage-audit.json').write_bytes(encoded(audit))
    reseal_files(owner)
    with pytest.raises(ValueError): reopen(owner)


def test_case_admission_has_no_reference_or_gold_projection(tmp_path):
    config, inputs, truth, _ = contract_bundle(tmp_path)
    papers, gold, _, _, _ = admit(Config.model_validate(config), Inputs.model_validate(inputs), Truth.model_validate(truth))
    assert set(next(iter(papers.values())).model_dump()) == {
        'paper_id', 'version_id', 'family', 'canonical_ids', 'title', 'abstract', 'split', 'stratum', 'observed_at'}
    assert set(gold) == set(papers) and not {'lineage', 'gold', 'references'} & papers.keys()


@pytest.mark.parametrize('updates,expected', [
    pytest.param({'readout': None}, 'native readout/status mismatch', id='updates0'),
    pytest.param({'terminal': 'terminal-999.bin'}, 'terminal ownership/reference mismatch', id='updates1'),
    pytest.param({'node_key': 'unknown'}, 'outcome/admitted projection mismatch', id='updates2'),
    pytest.param({'status': 'timeout'}, 'native readout/status mismatch', id='updates3'),
    pytest.param({'request_sha256': '0' * 64}, 'outcome/admitted projection mismatch', id='updates4'),
    pytest.param({'label': 'insufficient_evidence'}, 'preserve independent native choice', id='updates5'),
])
def test_per_key_reopen_rejects_inconsistent_native_outcome(tmp_path, updates, expected):
    config, _, _, path = contract_bundle(tmp_path)
    cfg = Config.model_validate(config)
    owner, _ = run(cfg, path.read_bytes())
    row = json.loads((owner / 'case-0.json').read_bytes())
    row.update(updates)
    (owner / 'case-0.json').write_bytes(encoded(row))
    reseal_files(owner)
    with pytest.raises(ValueError) as error:
        reopen(owner)
    assert expected in str(error.value)


@pytest.mark.parametrize('reply', [None, {'readout': 'not a status/readout pair'}, ('decision',)])
def test_wrong_adapter_reply_shape_is_programmer_failure(tmp_path, monkeypatch, reply):
    import tools.method_validation.collections_run as runtime
    cfg, _ = native(tmp_path)
    retained = []
    monkeypatch.setattr(runtime, 'project', lambda *args: reply)
    with pytest.raises((TypeError, ValueError)):
        runtime.evaluate({}, cfg.arms[0], cfg, b'{}', retained.append, remaining=1.,
                         transport=httpx.MockTransport(lambda _: httpx.Response(200, content=response())))
    assert retained == [response()]


def test_success_path_performs_no_file_or_action_mutations(tmp_path, monkeypatch):
    config, inputs, truth, _ = declared_bundle(tmp_path)
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    original_open = Path.open
    def readonly_open(path, mode='r', *args, **kwargs):
        assert not set(mode) & set('wax+')
        return original_open(path, mode, *args, **kwargs)
    def forbidden_write(*args, **kwargs):
        pytest.fail('pure collection admission/projection must not write')
    monkeypatch.setattr(Path, 'open', readonly_open)
    monkeypatch.setattr(Path, 'write_text', forbidden_write)
    monkeypatch.setattr(Path, 'write_bytes', forbidden_write)
    prepared = prepare(cfg)
    for raw in prepared[9].values():
        assert project(raw, cfg.arms[0], cfg)[0] == 'decision'
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == before
    assert not (tmp_path / 'data').exists()
