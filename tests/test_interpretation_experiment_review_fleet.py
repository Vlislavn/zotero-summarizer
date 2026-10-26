"""Protocol counterexamples; synthetic labels are not human scientific evidence."""
from copy import deepcopy
import hashlib
import json

import pytest

from tests.test_interpretation_experiment_report import decision_fixture, frozen_run


def test_blind_packet_order_does_not_retain_assignment_or_question_groups(frozen_run):
    from zotero_summarizer.services.faithbench._interpretation_report import blind_packets

    _, _, (_, records) = frozen_run
    packets = blind_packets(records)
    reverse = blind_packets(list(reversed(records)))
    assert list(packets) == sorted(packets)
    assert list(packets) == list(reverse)
    assert packets == reverse


@pytest.mark.parametrize('missing_arm', ['A', 'C'])
@pytest.mark.parametrize('candidate_correct,expected', [(0, 'NO-GO'), (39, 'INCONCLUSIVE')])
def test_supported_accuracy_bounds_keep_known_failure_with_missing_labels(missing_arm, candidate_correct, expected):
    from zotero_summarizer.services.faithbench._interpretation_report import Labels, _decision, _metrics

    inputs = {'items': [], 'recipe': {'labels': dict.fromkeys('ABC'), 'groups': {}, 'stance': {}}}
    records, judgments = [], {}
    for arm in 'ABC':
        paper = 'paper'
        inputs['recipe']['groups'][paper] = {'domain': 'clinical'}
        assignment = {'paper': paper, 'condition': arm, 'review_blind_id': 'review-' + arm,
                      'card_blind_id': 'card-' + arm, 'question_blind_ids': {}}
        judgments['review-' + arm] = {'response_sha256': '0' * 64, 'overclaim': False, 'add_regression': False}
        questions = []
        for index in range(40):
            item_id = str(index)
            if arm == 'A':
                inputs['items'].append({'item_id': item_id, 'kind': 'qa'})
                inputs['recipe']['stance'][item_id] = 'neutral'
            key = arm + item_id
            assignment['question_blind_ids'][item_id] = key
            correct = index < (candidate_correct if arm == 'C' else 40)
            judgments[key] = {'response_sha256': '0' * 64, 'correct': correct, 'overclaim': arm != 'C'}
            if (arm == missing_arm or missing_arm == 'A' and arm == 'B') and index == 39:
                judgments[key]['correct'] = None
            questions.append({'item_id': item_id, 'status': 'returned', 'output': {'response': {'answer': 'synthetic'}}})
        records.append({'assignment': assignment, 'review_status': 'returned', 'questions': questions,
                        'review': {'interpretation': {}}, 'calls': [], 'seconds': 1})
    labels = Labels.model_validate({'reviewer': 'synthetic protocol fixture', 'manifest_sha256': '0' * 64,
                                   'judgments': judgments})
    metrics, _ = _metrics(inputs, records, labels)
    _, comparisons, recipe, _ = decision_fixture()
    assert _decision(metrics, comparisons, recipe, labels) == expected


def test_inspection_requires_actual_approval_not_only_a_matching_csv_hash(tmp_path):
    from tests._interpretation_cohort import cohort
    from zotero_summarizer.services.faithbench._interpretation_inputs import inspect
    from zotero_summarizer.services.faithbench._dataset import review_path

    settings, path, recipe, _, _ = cohort(tmp_path)
    csv = review_path(path.parent / recipe['benchmark'])
    csv.write_bytes(csv.read_bytes().replace(b'yes,', b'no,', 1))
    recipe['review_sha256'] = hashlib.sha256(csv.read_bytes()).hexdigest()
    path.write_text(json.dumps(recipe))
    with pytest.raises(ValueError, match='not human-approved'):
        inspect(settings, path)
    assert not (settings.faithbench_dir / 'interpretation').exists()


def test_conclusive_benefit_shortfall_does_not_require_unrelated_resource_or_review_labels():
    from zotero_summarizer.services.faithbench._interpretation_report import _decision

    metrics, comparisons, recipe, labels = decision_fixture()
    metrics['B']['human_complete'] = False
    metrics['B']['review_overclaims'] = [None]
    labels.controlled_runtime = None
    comparisons['A'].update(paper_paired_95_interval=[0, 0.01], relative_95_interval=[0, 0.1])
    assert _decision(metrics, comparisons, recipe, labels) == 'NO-GO'


@pytest.mark.parametrize('visibility,expected', [('whole_card', 80), ('whole_card_over_budget', 0), ('unavailable', 0)])
def test_exposure_counts_actual_qa_visibility_not_only_retained_cards(frozen_run, visibility, expected):
    from zotero_summarizer.services.faithbench._interpretation_report import Labels, _metrics

    _, _, (inputs, records) = frozen_run
    changed = deepcopy(records)
    for record in changed:
        if record['assignment']['condition'] == 'C':
            for row in record['questions']:
                row['output']['card_visibility'] = visibility
    labels = Labels.model_validate({'reviewer': 'synthetic empty judgments', 'manifest_sha256': '0' * 64, 'judgments': {}})
    metrics, _ = _metrics(inputs, changed, labels)
    assert metrics['C']['card_exposed_questions'] == expected


def test_no_card_exposure_cannot_establish_a_card_pilot_recommendation():
    from zotero_summarizer.services.faithbench._interpretation_report import _decision

    metrics, comparisons, recipe, labels = decision_fixture()
    metrics['C']['card_exposed_questions'] = 0
    assert _decision(metrics, comparisons, recipe, labels) == 'INCONCLUSIVE'


@pytest.mark.parametrize('directory', ['interpretation', 'interpretation-reports'])
def test_symlinked_output_parent_refuses_before_publication(tmp_path, frozen_run, directory):
    from tests._interpretation_cohort import cohort
    from zotero_summarizer.services.faithbench._interpretation_inputs import inspect, prepare
    from zotero_summarizer.services.faithbench._interpretation_report import report

    settings, path, _, config, profiles = cohort(tmp_path / 'selected')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (settings.faithbench_dir / directory).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='[Ss]ymlink|selected project'):
        if directory == 'interpretation':
            prepare(settings, inspect(settings, path), config, profiles)
        else:
            _, root, _ = frozen_run
            # A real selected-project run is required before testing its export parent.
            import shutil
            local = settings.faithbench_dir / 'interpretation' / root.name
            shutil.copytree(root, local)
            report(settings, local)
    assert list(outside.iterdir()) == []


def test_existing_run_symlink_cannot_export_another_projects_sources(tmp_path, frozen_run):
    from types import SimpleNamespace
    from zotero_summarizer.services.faithbench._interpretation_report import report

    _, root, _ = frozen_run
    selected = tmp_path / 'faithbench'
    (selected / 'interpretation').mkdir(parents=True)
    link = selected / 'interpretation' / root.name
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match='[Ss]ymlink|selected project'):
        report(SimpleNamespace(faithbench_dir=selected), link)
    assert not (selected / 'interpretation-reports').exists()


def test_reference_csv_symlink_cannot_read_another_project(tmp_path):
    from tests._interpretation_cohort import cohort
    from zotero_summarizer.services.faithbench._interpretation_inputs import inspect
    from zotero_summarizer.services.faithbench._dataset import review_path

    settings, path, recipe, _, _ = cohort(tmp_path / 'selected')
    csv = review_path(path.parent / recipe['benchmark'])
    outside = tmp_path / 'other-project.csv'
    outside.write_bytes(csv.read_bytes())
    csv.unlink()
    csv.symlink_to(outside)
    with pytest.raises(ValueError, match='[Ss]ymlink|selected project'):
        inspect(settings, path)


def test_reopening_refuses_symlinked_manifest_before_read(tmp_path, frozen_run):
    import shutil
    from zotero_summarizer.services.faithbench._interpretation import read_run

    _, root, _ = frozen_run
    local = tmp_path / 'local-run'
    shutil.copytree(root, local)
    manifest = local / 'manifest.json'
    outside = tmp_path / 'foreign-manifest.json'
    outside.write_bytes(manifest.read_bytes())
    manifest.unlink()
    manifest.symlink_to(outside)
    with pytest.raises(ValueError, match='[Ss]ymlink'):
        read_run(local)


def test_missing_primary_labels_cannot_turn_pessimistic_shortfall_into_no_go():
    from zotero_summarizer.services.faithbench._interpretation_report import _decision

    metrics, comparisons, recipe, labels = decision_fixture()
    metrics['C'].update(human_complete=False, overclaim_counts=[8, 32])
    for comparison in comparisons.values():
        comparison.update(paper_paired_95_interval=[-0.1, 0.01], relative_95_interval=[-0.2, 0.1])
    assert _decision(metrics, comparisons, recipe, labels) == 'INCONCLUSIVE'
