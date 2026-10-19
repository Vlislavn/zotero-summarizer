"""Hand-computed numeric identities; by-construction floors, not model evidence."""
from types import SimpleNamespace

import pytest

from tools.method_validation.priority_report import paired, summary


def row(key, gold, label, *, arm='candidate', family=None):
    return {'case_sha256': key, 'gold': gold, 'label': label, 'status': 'decision',
            'capability': 'llm_ordinal', 'family': family or key, 'arm_id': arm,
            'baseline_native': {'score': 0.99}, 'readout': None, 'telemetry': {}}


def test_classification_and_ranking_denominators_are_independent():
    values = summary([row('a', 'must_read', 'should_read'), row('b', 'dont_read', 'must_read')], 1)
    assert values['coverage'] == 1
    assert values['ordinal_error'] == {'n': 2, 'mean': 2, 'median': 2.0, 'p95': 3}
    assert values['must_read_retention'] == 1
    assert values['false_strong'] == 0.5
    assert values['ranking']['precision'] == values['ranking']['ndcg'] == 0
    assert values['per_class']['must_read']['precision'] == 0
    assert values['brier'] is values['ece'] is None
    empty = summary([], 10)
    assert empty['coverage'] is empty['ordinal_error']['mean'] is empty['ranking']['ndcg'] is None


def test_brier_uses_full_vector_and_calibration_preserved_choice_not_vendor_confidence():
    item = row('a', 'must_read', 'should_read')
    item.update(capability='native_four_way', baseline_native=None,
                readout={'probabilities': [0.6, 0.2, 0.1, 0.1], 'confidence': 0.99})
    values = summary([item], 1)
    assert values['brier']['mean'] == pytest.approx(0.22)
    assert values['brier']['median'] == pytest.approx(0.22)
    assert values['ece'] == pytest.approx(0.2)
    assert values['calibration'] == [{'bin': 2, 'n': 1, 'probability': 0.2, 'accuracy': 0}]


def test_family_paired_ci_is_reproducible_and_names_its_actual_estimand():
    rows = [r for i in range(100) for r in
            (row(str(i), 'must_read', 'should_read'), row(str(i), 'must_read', 'must_read', arm='reference'))]
    cfg = SimpleNamespace(min_cases=100, min_families=20, seed=38, bootstrap_samples=200, ranking_depth=10)
    result = paired(rows, 'candidate', 'reference', cfg)
    assert result == paired(rows, 'candidate', 'reference', cfg)
    assert result['matched_cases'] == result['families'] == 100
    assert result['equal_family_ordinal_loss_difference'] == 1
    assert result['percentile_95_ci'] == [1, 1]
