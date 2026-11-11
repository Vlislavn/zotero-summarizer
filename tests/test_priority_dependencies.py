"""Current-parent safety boundaries exercised through the priority entrypoints."""
from pathlib import Path

import httpx
import pytest

from tools.method_validation import priority_run
from tools.method_validation.decision_adapter import NativeDecisionAdapter
from tools.method_validation.priority import Config, Inputs, Truth, identity, payload, project
from tools.method_validation.support import encoded, loads
from test_priority_boundaries import wire
from test_priority_fixtures import fixture


@pytest.mark.parametrize('entry', ['adapter', 'evaluate'])
@pytest.mark.parametrize('field,value', [('public_input_consent', False), ('external_guard', None)])
def test_public_priority_native_entry_requires_authorization(tmp_path, entry, field, value):
    cfg, _, _ = fixture(tmp_path, native=True)
    cfg[field] = value
    config = Config.model_validate(cfg)
    inputs = Inputs.model_validate(loads(Path(cfg['inputs']['path']).read_bytes()))
    paper = inputs.papers[0]
    arm = config.arms[0]
    request = payload(paper, config)
    calls, retained = [], []
    def handler(message):
        calls.append(message)
        return httpx.Response(200, content=encoded(wire()))
    with pytest.raises(ValueError, match='consent|guard'):
        if entry == 'adapter':
            NativeDecisionAdapter(arm, config, transport=httpx.MockTransport(handler), projector=project)(
                request, retained.append)
        else:
            row = priority_run.base_row(identity(paper), paper, arm, {})
            priority_run.evaluate(row, arm, config, request, retained.append,
                                  transport=httpx.MockTransport(handler))
    assert calls == retained == []


def test_priority_snapshot_reader_honors_remaining_aggregate_budget(tmp_path, monkeypatch):
    cfg, _, _ = fixture(tmp_path)
    refs = [cfg['inputs'], cfg['gold']]
    bundle = loads(Path(cfg['inputs']['path']).read_bytes())
    refs.extend(row['terminal'] for row in bundle['recordings'])
    sizes = [Path(ref['path']).stat().st_size for ref in refs]
    cfg['max_snapshot_bytes'] = max(sizes)
    assert sum(sizes) > cfg['max_snapshot_bytes']
    observed = []
    original = priority_run.read_bound
    def read(ref, cap):
        observed.append(cap)
        return original(ref, cap)
    monkeypatch.setattr(priority_run, 'read_bound', read)
    with pytest.raises(ValueError, match='byte|budget'):
        priority_run.run(Config.model_validate(cfg), encoded(cfg))
    assert observed[0] <= cfg['max_snapshot_bytes']
    assert not (tmp_path / 'selected').exists()


@pytest.mark.parametrize('human_count', [0, 60, 100])
def test_priority_paired_inference_uses_only_provenanced_gold(tmp_path, human_count):
    cfg, _, _ = fixture(tmp_path)
    config = Config.model_validate(cfg)
    papers, labels = [], []
    for index in range(100):
        paper = {'paper_id': str(index), 'family': str(index // 4), 'canonical_ids': [],
                 'title': f'Public dependency control {index}', 'abstract': 'Synthetic abstract.',
                 'split': 'holdout', 'stratum': 'shown', 'observed_at': '2026-02-01T00:00:00Z'}
        papers.append(paper)
    inputs = Inputs.model_validate({'papers': papers})
    for index, paper in enumerate(inputs.papers):
        labels.append({'identity': identity(paper), 'label': 'must_read',
                       'human_provenance': 'Synthetic declared reviewer' if index < human_count else None})
    truth = Truth.model_validate({'labels': labels, 'synthetic': False,
        'split_frozen_at': '2026-01-01T00:00:00Z', 'holdout_opened_at': '2026-03-01T00:00:00Z'})
    gold = {label.identity: label for label in truth.labels}
    rows = []
    for paper in inputs.papers:
        key = identity(paper)
        candidate = priority_run.base_row(key, paper, config.arms[0], gold)
        native = wire('must_read')
        status, readout = project(encoded(native), config.arms[0], config)
        candidate.update(status=status, label=readout['label'], readout=readout)
        reference = priority_run.base_row(key, paper, config.baselines[0], gold)
        priority_run.baseline_result(reference, config.baselines[0], {
            'case_sha256': key, 'label': 'must_read', 'observed_model': config.baselines[0].model, 'score': 0.9})
        rows.extend((candidate, reference))
    report = priority_run.report(rows, config, inputs, truth)
    paired = report['paired']['candidate']
    assert paired['matched_cases'] == human_count
    assert paired['coverage'] == human_count / 100
    assert (paired['percentile_95_ci'] is not None) == (human_count == 100)
    assert report['winner'] is None


def test_priority_blocked_recording_never_retains_or_scores_bytes(tmp_path):
    cfg, _, _ = fixture(tmp_path)
    cfg['arms'][0]['kind'] = 'blocked'
    config = Config.model_validate(cfg)
    inputs = Inputs.model_validate(loads(Path(cfg['inputs']['path']).read_bytes()))
    paper, arm = inputs.papers[0], config.arms[0]
    retained = []
    row = priority_run.base_row(identity(paper), paper, arm, {})
    priority_run.evaluate(row, arm, config, payload(paper, config), retained.append,
                          recording=encoded(wire()))
    assert row['status'] == 'blocked' and row['label'] is None and retained == []
