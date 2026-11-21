"""Cohen's kappa."""
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
