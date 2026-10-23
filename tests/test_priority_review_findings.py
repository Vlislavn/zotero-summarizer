"""Actual source-review counterexamples; synthetic artifacts, no provider or model."""
from pathlib import Path

import pytest

from tools.method_validation import priority_run
from tools.method_validation.priority import Config, Paper, identity, project
from tools.method_validation.support import encoded, loads, sha
from test_priority_fixtures import LABELS, fixture, write_ref


def test_report_names_declared_and_missing_provenance_without_claiming_verification(tmp_path):
    cfg, _, _ = fixture(tmp_path)
    truth = loads(Path(cfg['gold']['path']).read_bytes())
    truth['labels'][0]['human_provenance'] = None
    cfg['gold'] = write_ref(tmp_path / 'gold.json', truth)
    owner, result = priority_run.run(Config.model_validate(cfg), encoded(cfg))
    assert result['synthetic_labels'] is True
    assert result['input_provenance_class_counts'] == {
        'declared': {'must_read': 2, 'should_read': 3, 'could_read': 3, 'dont_read': 3},
        'missing': {'must_read': 1}}
    assert result['input_gold_class_counts']['must_read'] == 3
    assert result['holdout_gold_class_counts']['must_read'] == 2
    assert loads((owner / 'case-0.json').read_bytes())['gold'] is None
    assert priority_run.reopen(owner) == result


def baseline_fixture(tmp_path, kind, *, missing=False):
    cfg, papers, _ = fixture(tmp_path)
    baseline = next(b for b in cfg['baselines'] if b['kind'] == kind)
    rows = [{'case_sha256': identity(Paper.model_validate(papers[i])), 'label': LABELS[i],
             'observed_model': baseline['model'], 'score': score}
            for i, score in [(0, None if missing else 0.2), (3, 0.9)]]
    artifact = {'kind': kind, 'model': baseline['model'],
                'owner_sha256': priority_run.baseline_owners()[kind], 'model_artifact_sha256': 'c' * 64,
                'trained_at': '2025-01-01T00:00:00Z', 'training_families': ['older-training'], 'rows': rows}
    baseline['artifact'] = write_ref(tmp_path / 'baseline.json', artifact)
    cfg['ranking_depth'] = 1
    return cfg


@pytest.mark.parametrize('families', [None, []])
def test_ml_requires_nonempty_training_family_provenance_before_effects(tmp_path, families):
    cfg = baseline_fixture(tmp_path, 'ml')
    artifact = loads(Path(cfg['baselines'][0]['artifact']['path']).read_bytes())
    artifact['training_families'] = families
    cfg['baselines'][0]['artifact'] = write_ref(tmp_path / 'baseline.json', artifact)
    with pytest.raises(ValueError, match='training provenance'):
        priority_run.run(Config.model_validate(cfg), encoded(cfg))
    assert not (tmp_path / 'selected').exists()


def test_llm_confidence_is_not_priority_relevance(tmp_path):
    cfg = baseline_fixture(tmp_path, 'llm')
    _, report = priority_run.run(Config.model_validate(cfg), encoded(cfg))
    assert report['arms']['llm']['ordinal_error']['mean'] == 0
    assert report['arms']['llm']['ranking']['precision'] == 1
    assert report['arms']['llm']['ranking']['ndcg'] == 1


def test_ml_regression_score_is_retained_as_a_distinct_capability(tmp_path):
    cfg = baseline_fixture(tmp_path, 'ml')
    _, report = priority_run.run(Config.model_validate(cfg), encoded(cfg))
    assert report['arms']['ml']['ordinal_error']['mean'] == 0
    assert report['arms']['ml']['ranking']['precision'] == 0


def test_missing_ml_score_does_not_manufacture_an_ordinal_regression_rank(tmp_path):
    cfg = baseline_fixture(tmp_path, 'ml', missing=True)
    _, report = priority_run.run(Config.model_validate(cfg), encoded(cfg))
    assert report['arms']['ml']['ordinal_error']['n'] == 2
    assert report['arms']['ml']['ranking']['ndcg'] is None


@pytest.mark.parametrize('mutation', ['terminal', 'transport_status'])
def test_recorded_terminal_must_equal_its_unchanged_admitted_input(tmp_path, mutation):
    cfg, _, _ = fixture(tmp_path)
    config = Config.model_validate(cfg)
    owner, _ = priority_run.run(config, encoded(cfg))
    prepared = priority_run.prepare(config)
    inputs, truth = prepared[:2]
    row = loads((owner / 'case-0.json').read_bytes())
    swapped = (owner / 'terminal-3.bin').read_bytes()
    assert swapped != (owner / 'recording-0.bin').read_bytes()
    status, readout = project(swapped, config.arms[0], config)
    if mutation == 'terminal':
        row.update(status=status, label=readout['label'], readout=readout)
        (owner / 'terminal-0.bin').write_bytes(swapped)
    else:
        row.update(status='unavailable', label=None, readout=None)
        row['telemetry']['terminal_status'] = 'unavailable'
    (owner / 'case-0.json').write_bytes(encoded(row))
    rows = [loads((owner / f'case-{i}.json').read_bytes()) for i in range(36)]
    report = priority_run.report(rows, config, inputs, truth)
    (owner / 'report.json').write_bytes(encoded(report))
    (owner / 'report.md').write_text(priority_run.readable(report))
    manifest = loads((owner / 'manifest.json').read_bytes())
    for name in ('terminal-0.bin', 'case-0.json', 'report.json', 'report.md'):
        manifest['files'][name] = sha((owner / name).read_bytes())
    raw = encoded(manifest)
    (owner / 'manifest.json').write_bytes(raw)
    (owner / 'ACK').write_bytes(sha(raw).encode())
    with pytest.raises(ValueError, match='recording'):
        priority_run.reopen(owner)
