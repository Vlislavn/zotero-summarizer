"""Phase 1.9: classification metrics for golden-set evaluation."""

from __future__ import annotations

import pytest

from zotero_summarizer.services.model.golden_metrics import (
    CLASSES,
    accuracy,
    compute_binary,
    compute_confusion,
    compute_per_class,
)


def test_perfect_predictions_yield_one_for_all_classes():
    gold = ["must_read", "should_read", "could_read", "dont_read"]
    pred = list(gold)
    metrics = compute_per_class(gold, pred)
    for cls, m in metrics.items():
        assert m.precision == 1.0
        assert m.recall == 1.0
        assert m.f1 == 1.0
        assert m.support == 1


def test_known_counts_for_one_class():
    # gold:   must_read x3, should_read x2
    # pred:   must_read x2 correct + 1 misclassified as should_read
    #         should_read x2 correct
    gold = ["must_read", "must_read", "must_read", "should_read", "should_read"]
    pred = ["must_read", "must_read", "should_read", "should_read", "should_read"]
    metrics = compute_per_class(gold, pred)
    must = metrics["must_read"]
    assert must.true_positive == 2
    assert must.false_positive == 0  # nothing else was predicted as must_read
    assert must.false_negative == 1  # the 3rd must_read got predicted as should_read
    assert must.support == 3
    assert must.precision == pytest.approx(1.0)
    assert must.recall == pytest.approx(2 / 3)


def test_binary_aggregates_must_and_should_as_positive():
    gold = ["must_read", "should_read", "could_read", "dont_read"]
    pred = ["should_read", "must_read", "must_read", "could_read"]
    # gold positive = {must, should} = 2
    # pred positive = 3 (positions 0, 1, 2)
    # TP = 2 (positions 0, 1 are both gold-positive AND pred-positive)
    # FP = 1 (position 2: gold=could_read, pred=must_read)
    # FN = 0
    binary = compute_binary(gold, pred)
    assert binary.true_positive == 2
    assert binary.false_positive == 1
    assert binary.false_negative == 0
    assert binary.precision == pytest.approx(2 / 3)
    assert binary.recall == 1.0


def test_confusion_matrix_diagonal_for_perfect_predictions():
    gold = list(CLASSES) * 2  # 8 items, 2 per class
    pred = list(gold)
    matrix = compute_confusion(gold, pred)
    for i in range(len(CLASSES)):
        for j in range(len(CLASSES)):
            assert matrix[i][j] == (2 if i == j else 0)


def test_confusion_matrix_off_diagonal():
    gold = ["must_read", "must_read", "dont_read"]
    pred = ["should_read", "must_read", "dont_read"]
    matrix = compute_confusion(gold, pred)
    # idx 0=must_read, 1=should_read, 2=could_read, 3=dont_read
    assert matrix[0][0] == 1   # 1 must_read predicted as must_read
    assert matrix[0][1] == 1   # 1 must_read mistaken for should_read
    assert matrix[3][3] == 1   # 1 dont_read predicted correctly


def test_empty_lists_return_zero_metrics_not_division_error():
    metrics = compute_per_class([], [])
    for m in metrics.values():
        assert m.precision == 0.0
        assert m.recall == 0.0
        assert m.f1 == 0.0
        assert m.support == 0
    assert accuracy([], []) == 0.0


def test_unknown_labels_dropped_from_confusion_matrix():
    """Empty `our_priority` (uncached row) should not crash compute_confusion."""
    gold = ["must_read", "must_read"]
    pred = ["must_read", ""]  # second row never scored
    matrix = compute_confusion(gold, pred)
    assert matrix[0][0] == 1  # only the first row counted


def test_length_mismatch_raises():
    with pytest.raises(ValueError, match="length mismatch"):
        compute_per_class(["must_read"], ["must_read", "should_read"])
    with pytest.raises(ValueError, match="length mismatch"):
        compute_binary(["must_read"], ["must_read", "should_read"])
    with pytest.raises(ValueError, match="length mismatch"):
        compute_confusion(["must_read"], ["must_read", "should_read"])


def test_accuracy_basic():
    assert accuracy(["a", "b", "c"], ["a", "b", "c"]) == 1.0
    assert accuracy(["a", "b", "c"], ["a", "x", "c"]) == pytest.approx(2 / 3)
