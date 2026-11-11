"""Outside-in collection comparison, artifact ownership and no-bootstrap boundaries."""
import json

from test_collection_fixtures import bundle, cli


def test_module_admission_is_effect_free(tmp_path):
    _, _, _, path = bundle(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    result = cli(path)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output == {'mode': 'inspect-inputs', 'papers': 3, 'questions': 6,
                      'synthetic': True, 'model_calls': 0, 'writes': 0}
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_recorded_run_has_a_complete_replayable_paper_outcome(tmp_path):
    from tools.method_validation.collections import Config
    from tools.method_validation.collections_run import run, reopen
    config, _, _, path = bundle(tmp_path)
    owner, report = run(Config.model_validate(config), path.read_bytes())
    assert report['winner'] is None and report['action'] == 'retain_baseline'
    assert report['arms']['recorded']['complete_papers'] == 3
    assert report['arms']['recorded']['exact_set_accuracy'] == 1
    assert report['arms']['recorded']['question_coverage'] == 1
    assert report['arms']['similarity']['complete_papers'] == 0
    assert report['arms']['similarity']['exact_set_accuracy'] is None
    assert reopen(owner) == report
    assert (owner / 'ACK').is_file()
    assert len(list(owner.glob('terminal-*.bin'))) == 6


def test_real_module_recorded_run_inspection_and_owner_reuse(tmp_path):
    from pathlib import Path
    _, _, _, path = bundle(tmp_path)
    result = cli(path, '--run')
    assert result.returncode == 0, result.stderr
    owner = Path(result.stdout.splitlines()[0].removeprefix('Private artifacts: '))
    before = {p.name: p.read_bytes() for p in owner.iterdir()}
    inspected = cli(None, '--inspect', str(owner))
    assert inspected.returncode == 0, inspected.stderr
    assert json.loads(inspected.stdout) == json.loads((owner / 'report.json').read_bytes())
    assert {p.name: p.read_bytes() for p in owner.iterdir()} == before
    repeated = cli(path, '--run')
    assert repeated.returncode != 0
    assert {p.name: p.read_bytes() for p in owner.iterdir()} == before


def test_set_and_ranking_metrics_are_literal_wiring_floors(tmp_path):
    import pytest
    from test_collection_boundaries import artifact
    from test_collection_fixtures import save
    from tools.method_validation.collections import Config
    from tools.method_validation.collections_run import run
    config, inputs, truth, path = bundle(tmp_path)
    artifact(tmp_path, config, inputs)
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    _, report = run(cfg, path.read_bytes())
    native, baseline = report['arms']['recorded'], report['arms']['similarity']
    assert native['micro_f1'] == native['macro_f1'] == native['exact_set_accuracy'] == 1
    assert baseline['micro_f1'] == pytest.approx(2/3)
    assert baseline['exact_set_accuracy'] == pytest.approx(1/3)
    assert baseline['set_hamming_loss']['mean'] == pytest.approx(1/3)
    assert baseline['set_hamming_loss']['median'] == .5
    assert native['precision_at3']['mean'] == .5
    assert baseline['recall_at3']['mean'] == .75
    assert report['paired']['recorded']['equal_family_hamming_loss_difference'] == pytest.approx(-1/3)
    assert report['paired']['recorded']['percentile_95_ci'] is None
    assert report['winner'] is None
