"""Independent review boundary probes; unchanged canonical inputs and derived rows."""
import pytest

from test_collection_boundaries import artifact
from test_collection_fixtures import bind, bundle, save
from test_collection_replay import reseal, start
from test_collection_reservations import development_reference
from tools.method_validation.collections import Config
from tools.method_validation.collections_run import prepare, reopen, run


def test_corpus_backed_baseline_without_reference_inventory_refuses_before_owner(tmp_path):
    config, inputs, truth, path = bundle(tmp_path)
    data = artifact(tmp_path, config, inputs)
    data['reference_papers'] = []
    config['baselines'][0]['artifact'] = bind(tmp_path, 'baseline.json', data)
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    with pytest.raises(ValueError, match='reference|lineage'):
        run(cfg, path.read_bytes())
    assert not (tmp_path / 'data').exists()


def test_safe_nonempty_baseline_reference_inventory_remains_admissible(tmp_path):
    config, inputs, truth, _ = bundle(tmp_path)
    data = artifact(tmp_path, config, inputs)
    reference = development_reference()
    reference['observed_at'] = '2026-07-15T00:00:00Z'
    data['reference_papers'] = [reference]
    config['baselines'][0]['artifact'] = bind(tmp_path, 'baseline.json', data)
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    assert prepare(cfg)[8]['similarity']


def test_no_corpus_with_empty_reference_inventory_remains_unavailable_and_replayable(tmp_path):
    config, inputs, truth, path = bundle(tmp_path)
    data = artifact(tmp_path, config, inputs, names=[], has_corpus=False)
    data['reference_papers'] = []
    config['baselines'][0]['artifact'] = bind(tmp_path, 'baseline.json', data)
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    owner, result = run(cfg, path.read_bytes())
    assert result['arms']['similarity']['question_statuses'] == {'unavailable': 3}
    assert reopen(owner) == result


@pytest.mark.parametrize('name', ['case-999.json', 'recording-999.bin', 'unrelated.txt'])
def test_manifest_resealing_cannot_admit_foreign_files_outside_frozen_plan(tmp_path, name):
    cfg, owner, _ = start(tmp_path)
    (owner / name).write_bytes(b'{}')
    reseal(owner, cfg)
    with pytest.raises(ValueError, match='inventory'):
        reopen(owner)


def test_exact_canonical_publication_set_reopens_without_original_snapshots(tmp_path):
    cfg, owner, result = start(tmp_path)
    cfg.inputs.path.unlink()
    cfg.gold.path.unlink()
    assert reopen(owner) == result
