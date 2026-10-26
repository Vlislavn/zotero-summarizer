"""Hand-computed denominator and paired-family uncertainty floors, not quality claims."""
import pytest

from tools.method_validation.support_report import class_metrics, paired, report, summarize
from test_support_fixtures import fixture, validated


def row(key, gold, prediction, vector, *, arm='native', family=None, status='decision'):
    return {'case_sha256': str(key), 'arm_id': arm, 'family': family or str(key),
            'gold': gold, 'status': status, 'support': prediction == 'supports' if status == 'decision' else None,
            'readout': {'label': prediction, 'probabilities': vector}, 'capability': 'native_three_way',
            'telemetry': {'elapsed_seconds': 2.0}, 'baseline_native': None, 'split': 'holdout'}


def test_hand_computed_three_way_and_binary_denominators():
    rows = [row(0, 'supports', 'supports', [0.8, 0.1, 0.1]),
            row(1, 'contradicts', 'supports', [0.6, 0.3, 0.1]),
            row(2, 'supports', 'contradicts', [0.2, 0.7, 0.1]),
            row(3, 'insufficient_evidence', 'insufficient_evidence', [0.1, 0.1, 0.8])]
    result = summarize(rows)
    assert result['labelled_decisions'] == 4 and result['coverage'] == 1
    assert result['false_confirmation'] == {'n': 1, 'denominator': 2, 'rate': 0.5}
    assert result['false_rejection'] == {'n': 1, 'denominator': 2, 'rate': 0.5}
    assert result['binary_error']['mean'] == result['binary_error']['median'] == 0.5
    assert result['three_way_brier']['mean'] == pytest.approx(0.53)
    assert result['three_way_brier']['median'] == pytest.approx(0.46)
    assert result['per_class']['supports']['f1'] == 0.5
    assert result['per_class']['contradicts']['f1'] == 0
    assert result['per_class']['insufficient_evidence']['f1'] == 1
    assert result['latency_seconds'] == {'n': 4, 'mean': 2.0, 'median': 2.0, 'p95': 2.0}
    rows.append(row(4, 'supports', 'supports', [0.8, 0.1, 0.1], status='threshold_abstention'))
    failure = row(5, 'supports', 'supports', [0.8, 0.1, 0.1], status='timeout')
    failure['readout'] = None
    rows.append(failure)
    result = summarize(rows)
    assert result['coverage'] == 4 / 6 and result['operational_error'] == 1 / 6
    assert result['labelled_decisions'] == 4 and result['binary_error']['mean'] == 0.5


def test_empty_missing_class_and_binary_capability_never_invent_three_way_metrics():
    empty = summarize([])
    assert empty['coverage'] is empty['false_confirmation']['rate'] is None
    assert empty['binary_error']['mean'] is None
    binary = row(1, 'supports', 'supports', [0.8, 0.1, 0.1])
    binary.update(capability='binary_support', readout=None,
                  baseline_native={'verdict': None, 'score': 0.75})
    result = summarize([binary])
    assert result['per_class'] is result['three_way_brier'] is result['calibration'] is None
    assert result['false_confirmation']['denominator'] == 0
    assert result['false_confirmation']['rate'] is None
    assert result['false_rejection']['rate'] == 0
    assert class_metrics([], [], ('supports',))['supports']['f1'] is None


def test_paired_binary_loss_family_bootstrap_has_hand_computed_constant_ci(tmp_path):
    cfg, _, _, _ = fixture(tmp_path)
    cfg = validated(cfg)
    rows = []
    for family in range(20):
        for pair in range(4):
            key = f'{family}-{pair}'
            rows.append(row(key, 'supports', 'contradicts', [0.1, 0.8, 0.1], family=str(family)))
            rows.append(row(key, 'supports', 'supports', [0.8, 0.1, 0.1], arm='verifier', family=str(family)))
    result = paired(rows, 'native', 'verifier', cfg)
    assert result['matched_pairs'] == 80 and result['independent_paper_families'] == 20
    assert result['paper_equal_binary_loss_difference'] == 1
    assert result['percentile_95_ci'] == [1, 1]
    assert result['matched_coverage'] == 1
    assert len(result['disagreements_for_human_review']) == 80
    assert paired(rows, 'native', 'verifier', cfg) == result
    assert paired(rows[:-2], 'native', 'verifier', cfg)['percentile_95_ci'] is None


def test_unequal_family_sizes_do_not_change_equal_family_estimand(tmp_path):
    cfg, _, _, _ = fixture(tmp_path)
    rows = []
    for pair in range(80):
        family = 'large' if pair < 79 else 'small'
        candidate = 'supports' if pair < 79 else 'contradicts'
        baseline = 'contradicts' if pair < 79 else 'supports'
        rows.append(row(pair, 'supports', candidate, [0.8, 0.1, 0.1], family=family))
        rows.append(row(pair, 'supports', baseline, [0.8, 0.1, 0.1], family=family, arm='verifier'))
    result = paired(rows, 'native', 'verifier', validated(cfg))
    assert result['paper_equal_binary_loss_difference'] == 0
    assert result['percentile_95_ci'] is None
    assert result['independent_paper_families'] == 2


def test_even_perfect_large_synthetic_cohort_cannot_become_resource_winner(tmp_path):
    cfg, bundle, _, _ = fixture(tmp_path)
    from tools.method_validation.support import FrozenSupportInput
    rows = []
    for key in range(80):
        for arm in ('native', 'verifier', 'minicheck'):
            rows.append(row(key, 'supports' if key % 2 else 'contradicts',
                            'supports' if key % 2 else 'contradicts', [0.8, 0.1, 0.1],
                            arm=arm, family=str(key % 20)))
    result = report(rows, validated(cfg), FrozenSupportInput(**bundle))
    assert result['winner'] is None and result['action'] == 'retain_baseline'
    assert result['gates']['native']['memory'] is result['gates']['native']['cost'] is None
