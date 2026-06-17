"""Calibration scaffold: fleet-proposal vs confirmed-label agreement + Cohen's kappa."""
from __future__ import annotations

from zotero_summarizer.services.library import quality_calibration as qc


def test_cohen_kappa_perfect_and_degenerate():
    assert qc.cohen_kappa(["a", "b", "a"], ["a", "b", "a"]) == 1.0
    # all one category → chance agreement 1 → kappa undefined (None), not a fake 1.0
    assert qc.cohen_kappa(["a", "a"], ["a", "a"]) is None
    assert qc.cohen_kappa([], []) is None
    assert qc.cohen_kappa(["a"], ["a", "b"]) is None  # mismatched lengths


def test_cohen_kappa_partial_agreement_is_chance_corrected():
    a = ["must_read", "should_read", "could_read", "dont_read"]
    b = ["must_read", "should_read", "dont_read", "could_read"]  # 2/4 agree
    k = qc.cohen_kappa(a, b)
    assert k is not None and 0.0 < k < 1.0


def test_calibration_matches_only_items_with_both_and_flags_insufficient():
    proposals = {
        "A": {"proposed": "must_read"}, "B": {"proposed": "should_read"},
        "C": {"proposed": "could_read"}, "D": {"proposed": "must_read"},
        "E": {"proposed": "should_read"},  # no label → excluded
    }
    labels = {"A": "must_read", "B": "should_read", "C": "dont_read", "D": "must_read", "Z": "could_read"}
    out = qc.compute_proposal_calibration(proposals=proposals, labels=labels)
    assert out["n_pairs"] == 4          # A,B,C,D (E has no label, Z has no proposal)
    assert out["agreement"] == 0.75     # 3/4 match (C differs)
    assert out["insufficient"] is True  # < 20 pairs
    assert "self-consistency" in out["note"]


def test_calibration_empty_is_honest_zero():
    out = qc.compute_proposal_calibration(proposals={}, labels={})
    assert out["n_pairs"] == 0 and out["agreement"] == 0.0 and out["cohen_kappa"] is None
    assert out["insufficient"] is True
