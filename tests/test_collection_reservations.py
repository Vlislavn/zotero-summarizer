"""Total request-memory admission and temporal provenance joins."""
import pytest

from test_collection_boundaries import artifact
from test_collection_fixtures import bind, bundle, save
from tools.method_validation.collections import Config
from tools.method_validation.collections_run import prepare, run


def development_reference():
    return {'paper_id': 'dev', 'family': 'dev-family', 'canonical_ids': ['doi:dev'],
            'title': 'Development source', 'abstract': 'Original development evidence',
            'split': 'development', 'stratum': 'development', 'observed_at': '2026-08-15T00:00:00Z'}


def test_complete_requests_share_cumulative_snapshot_admission_budget(tmp_path):
    config, inputs, truth, path = bundle(tmp_path)
    prepared = prepare(Config.model_validate(config))
    required = sum(len(raw) for raw in prepared[-1].values()) + sum(len(raw) for raw in prepared[6].values())
    config['max_snapshot_bytes'] = required - 1
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    with pytest.raises(ValueError, match='byte|budget'):
        run(cfg, path.read_bytes())
    assert not (tmp_path / 'data').exists()


@pytest.mark.parametrize('owner', ['taxonomy', 'baseline'])
def test_reference_cannot_postdate_its_own_frozen_owner(tmp_path, owner):
    config, inputs, truth, path = bundle(tmp_path)
    reference = development_reference()
    if owner == 'taxonomy':
        inputs['tree']['description_sources'] = [reference]
    else:
        data = artifact(tmp_path, config, inputs)
        data['reference_papers'] = [reference]
        config['baselines'][0]['artifact'] = bind(tmp_path, 'baseline.json', data)
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    with pytest.raises(ValueError, match='reference|leakage'):
        run(cfg, path.read_bytes())
    assert not (tmp_path / 'data').exists()


@pytest.mark.parametrize('owner', ['taxonomy', 'baseline'])
def test_real_earlier_reference_is_admitted(tmp_path, owner):
    config, inputs, truth, path = bundle(tmp_path)
    reference = development_reference()
    reference['observed_at'] = '2026-07-15T00:00:00Z'
    if owner == 'taxonomy':
        inputs['tree']['description_sources'] = [reference]
    else:
        data = artifact(tmp_path, config, inputs)
        data['reference_papers'] = [reference]
        config['baselines'][0]['artifact'] = bind(tmp_path, 'baseline.json', data)
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    assert prepare(cfg)[2]


def test_reviewed_empty_unknown_and_unreviewed_gold_are_not_interchangeable(tmp_path):
    config, inputs, truth, path = bundle(tmp_path)
    truth['labels'][0]['memberships'] = None
    truth['labels'][2]['human_provenance'] = None
    truth['labels'].reverse()
    cfg = Config.model_validate(save(tmp_path, config, inputs, truth))
    _, report = run(cfg, path.read_bytes())
    assert report['holdout_labelled_papers'] == 1
    assert report['arms']['recorded']['labelled_complete_papers'] == 1
    assert report['arms']['recorded']['exact_set_accuracy'] == 1
    assert report['arms']['recorded']['false_assignment_on_no_match'] == {'n': 0, 'denominator': 1, 'rate': 0}
