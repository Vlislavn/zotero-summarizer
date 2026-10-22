import hashlib
import json
from copy import deepcopy

import pytest

from tests._interpretation_cohort import cohort
from tests.test_interpretation_experiment_run import ObservedSyntheticProvider


@pytest.fixture(scope='module')
def frozen_run(tmp_path_factory):
    from zotero_summarizer.services.faithbench._interpretation import execute, read_run
    from zotero_summarizer.services.faithbench._interpretation_inputs import inspect

    settings, path, _, config, profiles = cohort(tmp_path_factory.mktemp('interpretation-report'))
    root = execute(settings, inspect(settings, path), config, profiles, lambda role, profile: ObservedSyntheticProvider())
    return settings, root, read_run(root)


def test_blind_export_is_not_a_study_decision_and_omits_condition_model_fields(frozen_run):
    from zotero_summarizer.services.faithbench._interpretation_report import report

    settings, root, (inputs, records) = frozen_run
    result = report(settings, root)
    assert result['status'] == 'NEEDS_INDEPENDENT_HUMAN_LABELS'
    assert 'decision' not in result
    from pathlib import Path
    directory = Path(result['directory'])
    reviews_and_questions = json.loads((directory / 'blinded.json').read_text())
    cards = json.loads((directory / 'cards.json').read_text())
    assert not set(reviews_and_questions) & set(cards)
    assert all('card_claims' not in packet for packet in reviews_and_questions.values())
    packets = reviews_and_questions | cards
    template = json.loads((directory / 'labels-template.json').read_text())
    assert len(packets) == sum(1 + len(record['questions']) for record in records) + sum(
        bool(record['review']['interpretation'].get('claims')) for record in records if record['review'])
    assert set(template['judgments']) == set(packets)
    assert all(not ({'condition', 'model', 'provider', 'card_visibility'} & set(packet)) for packet in packets.values())
    assert all(judgment['correct'] is None and judgment['overclaim'] is None for judgment in template['judgments'].values())
    assert template['reviewer'] == ''


def test_missing_human_and_operational_observations_remain_inconclusive(frozen_run, tmp_path):
    from zotero_summarizer.services.faithbench._interpretation_report import report

    settings, root, (inputs, records) = frozen_run
    path = tmp_path / 'unsubmitted.json'
    path.write_text(json.dumps({'reviewer': 'synthetic fixture, not a human study',
                                'manifest_sha256': hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest(),
                                'judgments': {}}))
    result = report(settings, root, path)
    assert result['decision'] == 'INCONCLUSIVE'
    assert all(value is None for value in result['operational_judgments'].values())
    assert all(row['assigned'] == len(inputs['evaluation']) * 4 for row in result['metrics'].values())
    assert result['metrics']['A']['supported_accuracy'] == 0
    assert result['metrics']['A']['supported_accuracy_median'] == 0
    assert result['metrics']['A']['accuracy_mean'] == 0.5
    assert len(result['metrics']['A']['capabilities']) == 8
    assert result['metrics']['A']['latency_median'] >= 0
    assert result['metrics']['C']['human_complete'] is False


def test_wrong_response_labels_and_unreferenced_operational_success_refuse(frozen_run, tmp_path):
    from zotero_summarizer.services.faithbench._interpretation_report import blind_packets, report

    settings, root, (_, records) = frozen_run
    packets = blind_packets(records)
    key = records[0]['assignment']['question_blind_ids'][records[0]['questions'][0]['item_id']]
    label = {'reviewer': 'synthetic fixture', 'manifest_sha256': hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest(),
             'judgments': {key: {'question': packets[key]['question'], 'response_sha256': '0' * 64,
                                'correct': True, 'overclaim': False}}}
    path = tmp_path / 'labels.json'
    path.write_text(json.dumps(label))
    with pytest.raises(ValueError, match='response/question identity'):
        report(settings, root, path)
    label.update(judgments={}, controlled_runtime=True)
    path.write_text(json.dumps(label))
    with pytest.raises(ValueError, match='independent referenced evidence'):
        report(settings, root, path)
    label['controlled_runtime'] = 'true'
    path.write_text(json.dumps(label))
    with pytest.raises(ValueError):
        report(settings, root, path)


@pytest.mark.parametrize('mutation', ['card', 'claimed_invocation', 'content', 'null_status'])
def test_resealed_record_cannot_detach_from_retained_verifier_or_original(frozen_run, mutation):
    from zotero_summarizer.services.faithbench._interpretation import read_run
    from zotero_summarizer.services.faithbench._corpus import chunk_text
    from zotero_summarizer.services.library._digest_audit import canonical_hash
    from zotero_summarizer.services.library.interpretation import qualify, scientific_claims, selected_original

    _, root, (_, records) = frozen_run
    original = next(record for record in records if record['review']['interpretation']['status'] == 'accepted')
    path = root / original['assignment']['directory'] / 'record.json'
    manifest_path = root / 'manifest.json'
    before, manifest_before = path.read_bytes(), manifest_path.read_bytes()
    published = path.parent / 'paper_presentation.html'
    published_before = published.read_bytes()
    changed = json.loads(before)
    result = changed['review']
    if mutation == 'null_status':
        result['interpretation'] = {'status': None}
    elif mutation == 'content':
        result['content']['full_text'] = 'A different extraction than the retained original'
    else:
        card = result['interpretation']
        card['items'][0]['studied_context'] = 'Hypothetical gamma delta epsilon setting'
        card['claims'] = scientific_claims(card['items'], result['digest'])
        if mutation == 'claimed_invocation':
            # Deliberately reseal the copied receipt, not the independent retained call tape.
            result['scientific_call']['scientific_binding'] = canonical_hash(
                [card['claims'], card['source'], chunk_text(selected_original(result)), card['checks']])
            qualify(result)
    try:
        path.write_text(json.dumps(changed))
        manifest = json.loads(manifest_before)
        manifest['files'][str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        with pytest.raises(ValueError):
            read_run(root)
        assert published.read_bytes() == published_before
    finally:
        path.write_bytes(before)
        manifest_path.write_bytes(manifest_before)
    assert len(read_run(root)[1]) == len(records)


def decision_fixture():
    from zotero_summarizer.services.faithbench._interpretation_report import Labels
    metrics = {arm: {'human_complete': True, 'card_exposed_questions': 80 if arm == 'C' else 0,
                      'card_failures': 0, 'add_regressions': [False],
                      'supported_accuracy': 0.9, 'supported_counts': [36, 40], 'review_overclaims': [False], 'failures': 0, 'review_failures': 0,
                      'assigned': 80, 'overclaim_counts': [32, 32] if arm != 'C' else [8, 8],
                      'observed_tokens_mean': 100, 'latency_p95': 1.0, 'tokens_by_paper': {'paper': 100},
                      'overclaim_lower': 0.4 if arm != 'C' else 0.1,
                      'overclaim_upper': 0.4 if arm != 'C' else 0.1} for arm in ('A', 'B', 'C')}
    comparisons = {arm: {'paper_paired_95_interval': [0.1, 0.4], 'relative_95_interval': [0.4, 0.9]} for arm in ('A', 'B')}
    recipe = {'thresholds': {'relative_reduction': 0.3, 'absolute_reduction': 0.05,
                             'accuracy_loss': 0.05, 'resource_ratio': 1.25}}
    labels = Labels.model_construct(controlled_runtime=True, complete_token_accounting=True, model_identity_verified=True)
    return metrics, comparisons, recipe, labels


def test_decision_math_is_a_by_construction_wiring_floor_not_scientific_evidence():
    from zotero_summarizer.services.faithbench._interpretation_report import _decision

    metrics, comparisons, recipe, labels = decision_fixture()
    assert _decision(metrics, comparisons, recipe, labels) == 'GO_TO_LARGER_PILOT'
    labels.controlled_runtime = None
    assert _decision(metrics, comparisons, recipe, labels) == 'INCONCLUSIVE'
    metrics['C']['card_failures'] = 1
    assert _decision(metrics, comparisons, recipe, labels) == 'NO-GO'


@pytest.mark.parametrize('mutate', [
    lambda m: m['C'].update(supported_accuracy=0.8, supported_counts=[32, 40]),
    lambda m: m['C'].update(failures=1),
    lambda m: m['C'].update(review_failures=1),
    lambda m: m['C'].update(review_overclaims=[True]),
    lambda m: m['C'].update(add_regressions=[True]),
    lambda m: m['C'].update(observed_tokens_mean=126),
    lambda m: m['C'].update(latency_p95=1.26),
    lambda m: m['C'].update(tokens_by_paper={'paper': 126}),
])
def test_every_independent_correctness_or_resource_gate_can_refuse(mutate):
    from zotero_summarizer.services.faithbench._interpretation_report import _decision

    metrics, comparisons, recipe, labels = decision_fixture()
    mutate(metrics)
    assert _decision(metrics, comparisons, recipe, labels) == 'NO-GO'


def test_zero_reference_cost_and_no_baseline_errors_cannot_manufacture_go():
    from zotero_summarizer.services.faithbench._interpretation_report import _decision

    metrics, comparisons, recipe, labels = decision_fixture()
    metrics['A']['observed_tokens_mean'] = 0
    metrics['A']['tokens_by_paper'] = {'paper': 0}
    assert _decision(metrics, comparisons, recipe, labels) == 'INCONCLUSIVE'
    metrics, comparisons, recipe, labels = decision_fixture()
    for row in metrics.values():
        row.update(overclaim_lower=0, overclaim_upper=0, overclaim_counts=[0, 0])
    for row in comparisons.values():
        row.update(paper_paired_95_interval=[0, 0], relative_95_interval=None)
    assert _decision(metrics, comparisons, recipe, labels) == 'INCONCLUSIVE'


@pytest.mark.parametrize('field', ['observed_tokens_mean', 'tokens_by_paper', 'latency_p95'])
@pytest.mark.parametrize('ratio', [1.0, 1.14, 1.15, 1.2, 1.25])
@pytest.mark.parametrize('offset', [-1, 0, 1])
def test_admitted_resource_caps_are_inclusive_without_relaxing_a_breach(field, ratio, offset):
    from decimal import Decimal
    from statistics import mean
    from zotero_summarizer.services.faithbench._interpretation_inputs import Thresholds
    from zotero_summarizer.services.faithbench._interpretation_report import _decision

    metrics, comparisons, recipe, labels = decision_fixture()
    recipe['thresholds'] = Thresholds(resource_ratio=ratio).model_dump()
    reference, observed = 100, int(Decimal(str(ratio)) * 100) + offset
    if field == 'latency_p95':
        metrics['A'][field], metrics['C'][field] = reference, observed
    else:
        for arm, count in [('A', reference), ('C', observed)]:
            counts = {'paper': count, 'control': reference} if field == 'tokens_by_paper' else {'paper': count}
            metrics[arm].update(tokens_by_paper=counts, observed_tokens_mean=mean(counts.values()))
    expected = 'GO_TO_LARGER_PILOT' if offset <= 0 else 'NO-GO'
    assert _decision(metrics, comparisons, recipe, labels) == expected


@pytest.mark.parametrize('observed,expected', [
    (0.113, 'GO_TO_LARGER_PILOT'), (0.114, 'GO_TO_LARGER_PILOT'),
    (0.115, 'NO-GO'), (0.11400000000000002, 'NO-GO'),
])
def test_fractional_latency_boundary_does_not_borrow_a_tolerance(observed, expected):
    from zotero_summarizer.services.faithbench._interpretation_report import _decision

    metrics, comparisons, recipe, labels = decision_fixture()
    recipe['thresholds']['resource_ratio'] = 1.14
    metrics['A']['latency_p95'], metrics['C']['latency_p95'] = 0.1, observed
    assert _decision(metrics, comparisons, recipe, labels) == expected


def test_token_mean_gate_uses_exact_paired_counts_not_rounded_display_means():
    from statistics import mean
    from zotero_summarizer.services.faithbench._interpretation_report import _decision

    metrics, comparisons, recipe, labels = decision_fixture()
    recipe['thresholds']['resource_ratio'] = 1.14
    reference, observed = [50, 50, 50, 50, 50, 50, 1200], [57, 57, 57, 57, 57, 57, 1368]
    for arm, counts in [('A', reference), ('C', observed)]:
        metrics[arm].update(tokens_by_paper=dict(enumerate(counts)), observed_tokens_mean=mean(counts))
    assert _decision(metrics, comparisons, recipe, labels) == 'GO_TO_LARGER_PILOT'
    metrics['C']['tokens_by_paper'][6] += 1
    metrics['C']['observed_tokens_mean'] = mean(metrics['C']['tokens_by_paper'].values())
    assert _decision(metrics, comparisons, recipe, labels) == 'NO-GO'


@pytest.mark.parametrize('absolute,relative,expected', [
    (0.05, 0.30, 'INCONCLUSIVE'), (0.049, 0.30, 'NO-GO'), (0.05, 0.299, 'NO-GO'),
])
def test_shortfall_boundaries_keep_the_same_inclusive_decimal_convention(absolute, relative, expected):
    from zotero_summarizer.services.faithbench._interpretation_report import _decision

    metrics, comparisons, recipe, labels = decision_fixture()
    for row in comparisons.values():
        row.update(paper_paired_95_interval=[0, absolute], relative_95_interval=[0, relative])
    assert _decision(metrics, comparisons, recipe, labels) == expected


def test_paper_paired_bootstrap_retains_clusters_and_seed_reproducibility():
    from zotero_summarizer.services.faithbench._interpretation_report import _paired

    losses = {arm: {str(index): value for index, value in enumerate(values)} for arm, values in
              [('A', [0, 1, 0, 1]), ('B', [0, 1, 1, 1]), ('C', [0, 0, 0, 0])]}
    recipe = {'seed': 49, 'bootstrap_samples': 200}
    result = _paired(losses, recipe)
    assert result == _paired(deepcopy(losses), recipe)
    assert result['A']['improvement_mean'] == result['A']['improvement_median'] == 0.5
    assert result['B']['improvement_mean'] == 0.75
    assert result['A']['paper_paired_95_interval'][0] >= 0
