"""Reachable parent admission/identity limits and collection provenance countercontrols."""
import httpx
import pytest

from test_collection_boundaries import artifact
from test_collection_fixtures import bundle, encoded, response, save
from test_collection_native import native
from tools.method_validation import collections_run as runtime
from tools.method_validation.collections import Config, Inputs, Truth, admit, identity, payload
from tools.method_validation.support import digest, loads


@pytest.mark.parametrize('entry', ['adapter', 'evaluate'])
@pytest.mark.parametrize('field,value', [('public_input_consent', False), ('external_guard', None)])
def test_collection_public_native_entry_requires_authorization(tmp_path, entry, field, value):
    cfg, _ = native(tmp_path)
    cfg = Config.model_validate({**cfg.model_dump(), field: value})
    inputs = Inputs.model_validate(loads(cfg.inputs.path.read_bytes()))
    truth = Truth.model_validate(loads(cfg.gold.path.read_bytes()))
    papers, gold, nodes, paths, questions = admit(cfg, inputs, truth)
    key, paper = next(iter(papers.items()))
    request = payload(paper, questions[0], (nodes, paths, inputs.tree), cfg)
    calls, retained = [], []
    def handler(message):
        calls.append(message)
        return httpx.Response(200, content=response())
    with pytest.raises(ValueError, match='consent|guard'):
        if entry == 'adapter':
            runtime.NativeDecisionAdapter(cfg.arms[0], cfg, transport=httpx.MockTransport(handler))(
                request, retained.append)
        else:
            row = runtime.base_row(digest([key, questions[0]]), paper, questions[0], cfg.arms[0], gold)
            runtime.evaluate(row, cfg.arms[0], cfg, request, retained.append,
                             transport=httpx.MockTransport(handler), remaining=1.0)
    assert calls == retained == []


def test_collection_canonical_duplicate_within_same_family_refuses_before_owner(tmp_path):
    config, inputs, truth, path = bundle(tmp_path)
    inputs['papers'][1]['family'] = inputs['papers'][0]['family']
    inputs['papers'][1]['canonical_ids'] = inputs['papers'][0]['canonical_ids']
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    with pytest.raises(ValueError, match='canonical'):
        runtime.run(cfg, path.read_bytes())
    assert not (tmp_path / 'data').exists()


def test_collection_snapshot_reader_uses_remaining_cumulative_budget(tmp_path, monkeypatch):
    config, inputs, truth, _ = bundle(tmp_path)
    config['max_snapshot_bytes'] = len(encoded(inputs)) - 1
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    observed, original = [], runtime.read_bound
    def read(ref, cap):
        observed.append(cap)
        return original(ref, cap)
    monkeypatch.setattr(runtime, 'read_bound', read)
    with pytest.raises(ValueError, match='byte|budget'):
        runtime.prepare(cfg)
    assert observed[0] == cfg.max_snapshot_bytes
    assert not (tmp_path / 'data').exists()


@pytest.mark.parametrize('human_count', [0, 60, 100])
def test_collection_paired_inference_already_filters_unprovenanced_gold(tmp_path, human_count):
    config, raw_inputs, raw_truth, _ = bundle(tmp_path)
    baseline = artifact(tmp_path, config, raw_inputs)['rows'][0]
    cfg = Config.model_validate(config)
    raw_inputs.update(papers=[{**raw_inputs['papers'][0], 'paper_id': str(i), 'family': str(i // 4),
        'canonical_ids': [], 'title': f'Public provenance control {i}'} for i in range(100)], recordings=[])
    inputs = Inputs.model_validate(raw_inputs)
    raw_truth['labels'] = [{'identity': identity(p), 'memberships': ['B'],
        'human_provenance': 'Synthetic declared reviewer' if i < human_count else None}
        for i, p in enumerate(inputs.papers)]
    truth = Truth.model_validate(raw_truth)
    papers, gold, nodes, _, questions = admit(cfg, inputs, truth)
    rows = []
    for unit, key, node, arm in runtime.units(papers, questions, cfg):
        row = runtime.base_row(unit, papers[key], node, arm, gold)
        if node is None:
            runtime.baseline_result(row, arm, {**baseline, 'case_sha256': key}, nodes)
        else:
            runtime.evaluate(row, arm, cfg, b'{}', lambda _: None,
                recording=response('belongs' if node == 'B' else 'does_not_belong'), remaining=1.0)
        rows.append(row)
    result = runtime.report(rows, cfg, inputs, truth)
    paired = result['paired']['recorded']
    assert paired['matched_papers'] == human_count
    assert paired['coverage'] == human_count / 100
    assert (paired['percentile_95_ci'] is not None) == (human_count == 100)
    assert result['holdout_labelled_papers'] == human_count
    assert result['winner'] is None
