"""Declared ancestry is consistency evidence, not authenticated scientific purge proof."""
from copy import deepcopy
import json

import pytest

from _collection_contract_fixtures import declared_bundle, derived, reseal_files, source_entry
from test_collection_boundaries import artifact
from test_collection_fixtures import bind, bundle, identity, save
from test_collection_native import native
from tools.method_validation.collections import Config, Inputs
from tools.method_validation.collections_report import paper_outcomes
from tools.method_validation.collections_run import prepare, reopen, run
from tools.method_validation.support import digest, encoded


def add_use(inputs, channel, owner, value, sources):
    entry = derived(channel + ':' + owner, sources, value)
    inputs['lineage']['entries'].append(entry)
    use = {'channel': channel, 'owner': owner, 'entry_id': entry['entry_id'],
           'binding_sha256': entry['payload_sha256']}
    inputs['lineage']['uses'].append(use)
    return use, entry


def all_channels(root):
    config, inputs, truth, path = declared_bundle(root)
    dev = deepcopy(inputs['tree']['description_sources'][0])
    inputs['papers'].append(dev)
    example = {'paper': dev, 'node_key': 'B', 'belongs': True}
    inputs['tree']['examples'] = [example]
    add_use(inputs, 'example', '0', example, ['source:dev'])
    inputs['tree'].update(selected_keys=['B'], selection_provenance='SYNTHETIC predeclared selector')
    inputs['recordings'] = [r for r in inputs['recordings'] if r['case_sha256'] in {
        digest([identity(p), 'B']) for p in inputs['papers']}]
    selection = {k: inputs['tree'][k] for k in ('selected_keys', 'selection_provenance')}
    add_use(inputs, 'shortlist', 'tree', selection, ['source:dev'])
    config['calibration'] = {'split_sha256': digest([(p['paper_id'], p['family'], p['split']) for p in inputs['papers']]),
        'development_families': ['development'], 'method': 'SYNTHETIC declared development calibration',
        'frozen_at': '2026-09-02T00:00:00Z'}
    add_use(inputs, 'calibration', 'calibration', config['calibration'], ['source:dev'])
    baseline = artifact(root, config, inputs)
    reference = baseline['reference_papers'][0]
    reference['version_id'] = 'reference-v1'
    inputs['lineage']['entries'].append(source_entry(reference))
    config['baselines'][0]['artifact'] = bind(root, 'baseline.json', baseline)
    add_use(inputs, 'baseline', 'similarity', baseline, ['source:reference'])
    return config, inputs, truth, path


def test_all_reference_channels_share_exact_version_inventory_and_verified_offline_replay(tmp_path):
    config, inputs, truth, path = all_channels(tmp_path)
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    owner, result = run(cfg, path.read_bytes())
    audit = json.loads((owner / 'lineage-audit.json').read_bytes())
    assert {u['channel'] for u in audit['uses']} == {'description', 'example', 'shortlist', 'calibration', 'baseline'}
    assert result['lineage_qualification'] == 'declared_only_not_authenticated'
    assert audit['active_reference_ids'] == ['safe', 'dev-example']
    before = {p.name: p.read_bytes() for p in owner.iterdir()}
    for path in tmp_path.glob('*.json'): path.unlink()
    for path in tmp_path.glob('*.bin'): path.unlink()
    assert reopen(owner) == result
    assert {p.name: p.read_bytes() for p in owner.iterdir()} == before


@pytest.mark.parametrize('channel', ['description', 'example', 'shortlist', 'calibration', 'baseline'])
def test_removed_ancestor_never_licenses_dependent_text_or_artifact(tmp_path, channel):
    config, inputs, truth, path = all_channels(tmp_path)
    use = next(u for u in inputs['lineage']['uses'] if u['channel'] == channel)
    entry = next(e for e in inputs['lineage']['entries'] if e['entry_id'] == use['entry_id'])
    entry['source_ids'].append('transitive')
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    with pytest.raises(ValueError): run(cfg, path.read_bytes())
    assert not (tmp_path / 'data').exists()


@pytest.mark.parametrize('channel', ['description', 'example', 'shortlist', 'calibration', 'baseline'])
def test_every_active_reference_use_binds_actual_bytes_not_a_declared_safe_label(tmp_path, channel):
    config, inputs, truth, path = all_channels(tmp_path)
    if channel == 'description': inputs['tree']['nodes'][0]['description'] += ' changed'
    if channel == 'example': inputs['tree']['examples'][0]['belongs'] = False
    if channel == 'shortlist': inputs['tree']['selection_provenance'] += ' changed'
    if channel == 'calibration': config['calibration']['method'] += ' changed'
    if channel == 'baseline':
        baseline = json.loads((tmp_path / 'baseline.json').read_bytes())
        baseline['model_artifact_sha256'] = 'f' * 64
        config['baselines'][0]['artifact'] = bind(tmp_path, 'baseline.json', baseline)
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    with pytest.raises(ValueError): run(cfg, path.read_bytes())
    assert not (tmp_path / 'data').exists()


@pytest.mark.parametrize('mutation', ['cycle', 'orphan', 'missing_version', 'foreign_use', 'duplicate_use',
                                    'missing_use', 'unknown_inventory', 'duplicate_inventory', 'version_text_drift',
                                    'reference_version_drift', 'derived_before_source', 'missing_channel',
                                    'original_payload_drift'])
def test_closed_lineage_refuses_unknown_incomplete_or_inconsistent_declarations(tmp_path, mutation):
    config, inputs, truth, path = declared_bundle(tmp_path)
    graph = inputs['lineage']
    if mutation == 'cycle':
        for e in graph['entries']:
            if e['entry_id'] == 'mixed': e['source_ids'] = ['transitive']
    if mutation == 'orphan': graph['entries'].append(derived('orphan', ['source:dev']))
    if mutation == 'missing_version': del inputs['papers'][0]['version_id']
    if mutation == 'foreign_use': graph['uses'][0]['owner'] = 'missing'
    if mutation == 'duplicate_use': graph['uses'].append(deepcopy(graph['uses'][0]))
    if mutation == 'missing_use': graph['uses'].pop()
    if mutation == 'unknown_inventory': graph['inventory_ids'].append('missing')
    if mutation == 'duplicate_inventory': graph['inventory_ids'].append('safe')
    if mutation == 'version_text_drift':
        graph['entries'][0]['paper']['title'] = 'Different version bytes'
        graph['entries'][0]['payload'] = deepcopy(graph['entries'][0]['paper'])
        graph['entries'][0]['payload_sha256'] = digest(graph['entries'][0]['paper'])
    if mutation == 'reference_version_drift': inputs['tree']['description_sources'][0]['version_id'] = 'new-version'
    if mutation == 'derived_before_source': graph['entries'][-1]['observed_at'] = '2026-01-01T00:00:00Z'
    if mutation == 'missing_channel':
        del next(e for e in graph['entries'] if e['entry_id'] == 'safe')['channel']
    if mutation == 'original_payload_drift': graph['entries'][0]['payload']['title'] = 'Forged retained source'
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    with pytest.raises(ValueError): run(cfg, path.read_bytes())
    assert not (tmp_path / 'data').exists()


def test_lineage_closure_and_audit_reserve_existing_work_and_snapshot_budgets(tmp_path):
    config, inputs, truth, _ = declared_bundle(tmp_path)
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    prepared = prepare(cfg)
    required = sum(len(r) for r in prepared[-1].values()) + sum(len(r) for r in prepared[6].values())
    with pytest.raises(ValueError): prepare(Config.model_validate({**cfg.model_dump(), 'max_snapshot_bytes': required - 1}))
    with pytest.raises(ValueError): prepare(Config.model_validate({**cfg.model_dump(), 'max_work_units': 20}))
    assert not (tmp_path / 'data').exists()


def test_original_input_hash_binding_prevents_resealed_lineage_and_audit_replacement(tmp_path):
    config, inputs, truth, path = declared_bundle(tmp_path)
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    owner, _ = run(cfg, path.read_bytes())
    changed = json.loads((owner / 'inputs.json').read_bytes())
    changed['lineage']['inventory_ids'] = ['safe']
    (owner / 'inputs.json').write_bytes(encoded(changed))
    reseal_files(owner)
    with pytest.raises(ValueError): reopen(owner)


def test_legacy_family_time_inputs_keep_lineage_unknown_not_exhaustively_purged(tmp_path):
    config, _, _, path = bundle(tmp_path)
    owner, result = run(Config.model_validate(config), path.read_bytes())
    assert result['lineage_qualification'] == 'unknown'
    assert not (owner / 'lineage-audit.json').exists()
    assert reopen(owner) == result


@pytest.mark.parametrize('missing', ['title', 'abstract'])
def test_nullable_paper_text_is_required_not_defaulted(tmp_path, missing):
    _, inputs, _, _ = bundle(tmp_path)
    inputs['papers'][0][missing] = None
    assert getattr(Inputs.model_validate(inputs).papers[0], missing) is None
    del inputs['papers'][0][missing]
    with pytest.raises(ValueError): Inputs.model_validate(inputs)


def test_complete_native_no_match_requires_each_distinct_eligible_question(tmp_path):
    config, _, _, path = bundle(tmp_path)
    owner, _ = run(Config.model_validate(config), path.read_bytes())
    rows = [json.loads((owner / f'case-{i}.json').read_bytes()) for i in (3, 4)]
    key = rows[0]['paper_sha256']
    assert paper_outcomes(rows, ('A', 'B'), {'A', 'B'})[key]['confirmed_no_match']
    assert not paper_outcomes(rows[:1], ('A', 'B'), {'A', 'B'})[key]['confirmed_no_match']
    assert not paper_outcomes([rows[0], rows[0]], ('A', 'B'), {'A', 'B'})[key]['confirmed_no_match']
    assert not paper_outcomes(rows[:1], ('A',), {'A', 'B'})[key]['confirmed_no_match']


def test_native_call_budget_counts_every_paper_question_and_arm_and_baseline_outcome(tmp_path):
    cfg, _ = native(tmp_path)
    values = cfg.model_dump()
    values['arms'] = [*values['arms'], {**values['arms'][0], 'arm_id': 'second'}]
    for budget, bound in [('max_calls', 11), ('max_work_units', 26)]:
        with pytest.raises(ValueError): prepare(Config.model_validate({**values, budget: bound}))
    assert not (tmp_path / 'data').exists()
