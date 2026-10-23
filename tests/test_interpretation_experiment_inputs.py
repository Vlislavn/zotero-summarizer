import json
from copy import deepcopy

import pytest

from tests._interpretation_cohort import changed_recipe, cohort


def test_inspect_has_no_output_effect_then_freezes_approved_inputs_before_any_provider(tmp_path):
    from zotero_summarizer.services.faithbench._interpretation_inputs import inspect, prepare

    settings, path, recipe, config, profiles = cohort(tmp_path)
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    snapshot = inspect(settings, path)
    assert len(snapshot['items']) == 4 * len(snapshot['evaluation'])
    assert len(snapshot['evaluation']) == recipe.get('minimum_papers', 20)
    assert {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before
    root, frozen = prepare(settings, snapshot, config, profiles)
    assert root.parent == settings.faithbench_dir / 'interpretation'
    assert root.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in root.iterdir())
    assert frozen['physical_retries'] == 'not_observed'
    assert frozen['remote_weights_identity'] == 'not_established'
    assert frozen['runtime_observation'] == 'not_observed'
    assert json.loads((root / 'inputs.json').read_text()) == frozen
    with pytest.raises(FileExistsError):
        from zotero_summarizer.services.faithbench._interpretation_inputs import publish_json
        publish_json(root / 'inputs.json', frozen)


@pytest.mark.parametrize('mutation', [
    lambda r: r['stance'].pop(next(iter(r['stance']))),
    lambda r: r['groups']['TUNE0001'].update(family=r['groups']['P0000000']['family']),
    lambda r: r['groups']['P0000000'].update(version=''),
    lambda r: r['groups']['P0000000'].update(published_at='2024-01-01T00:00:00Z'),
    lambda r: r['groups']['P0000000'].update(published_at='2026-01-01T00:00:00'),
    lambda r: r['groups']['P0000000'].update(domain='agent') or
              [g.update(domain='agent') for g in r['groups'].values()],
    lambda r: r.update(minimum_papers=2),
    lambda r: r['model_profiles'].pop('qa'),
    lambda r: r.update(thresholds={'resource_ratio': 2.0}),
    lambda r: r.update(max_client_calls=True),
])
def test_incomplete_cells_lineage_time_domain_profile_and_weaker_gates_refuse(tmp_path, mutation):
    from zotero_summarizer.services.faithbench._interpretation_inputs import inspect

    settings, path, recipe, _, _ = cohort(tmp_path)
    changed_recipe(path, recipe, mutation)
    with pytest.raises(ValueError):
        inspect(settings, path)
    assert not (settings.faithbench_dir / 'interpretation').exists()


def test_changed_profiles_refuse_before_attempt_or_client_construction(tmp_path):
    from zotero_summarizer.services.faithbench._interpretation_inputs import inspect, prepare

    settings, path, _, config, profiles = cohort(tmp_path)
    snapshot = inspect(settings, path)
    changed = deepcopy(profiles)
    changed['qa'] = changed['qa'].model_copy(update={'model': 'wrong-model'})
    with pytest.raises(ValueError, match='profiles changed'):
        prepare(settings, snapshot, config, changed)
    assert not (settings.faithbench_dir / 'interpretation').exists()


def test_reference_changes_refuse_before_any_provider_or_publication(tmp_path):
    from zotero_summarizer.services.faithbench import _dataset
    from zotero_summarizer.services.faithbench._interpretation_inputs import inspect

    settings, path, recipe, _, _ = cohort(tmp_path)
    reference = _dataset.review_path(settings.faithbench_dir / recipe['benchmark'])
    reference.write_bytes(reference.read_bytes().replace(b'yes,', b'no,', 1))
    with pytest.raises(ValueError, match='reference CSV changed'):
        inspect(settings, path)
    assert not (settings.faithbench_dir / 'interpretation').exists()
