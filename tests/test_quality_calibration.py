"""Calibration scaffold: fleet-proposal vs confirmed-label agreement + Cohen's kappa."""
from __future__ import annotations

import json

import pytest

from zotero_summarizer.models.triage import ProposedVerdict
from zotero_summarizer.services.library import quality_calibration as qc
from zotero_summarizer.services.library.review_fleet import verdict_store


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
    assert out["cohen_kappa"] == 0.636
    assert out["insufficient"] is True  # < 20 pairs
    assert "self-consistency" in out["note"]


@pytest.mark.parametrize(
    "malformed",
    [None, True, 7, [], "not-a-proposal", {"proposed": "skip"}],
    ids=["none", "bool", "int", "list", "string", "invalid-enum"],
)
def test_calibration_ignores_malformed_injected_proposals_and_unknown_labels(malformed):
    proposals = {
        "BAD": malformed,
        "GOOD": ProposedVerdict(proposed="must_read").model_dump(),
        "UNKNOWN_LABEL": ProposedVerdict(proposed="must_read").model_dump(),
    }
    labels = {"BAD": "must_read", "GOOD": "must_read", "UNKNOWN_LABEL": "future_label"}

    out = qc.compute_proposal_calibration(proposals=proposals, labels=labels)

    assert out["n_pairs"] == 1
    assert out["agreement"] == 1.0
    assert out["cohen_kappa"] is None


@pytest.mark.parametrize(
    "malformed",
    [None, True, 7, [], "not-a-proposal", {"proposed": "skip"}],
    ids=["none", "bool", "int", "list", "string", "invalid-enum"],
)
def test_calibration_reads_real_store_without_losing_valid_neighbor(
    tmp_path, monkeypatch, malformed
):
    path = tmp_path / "proposed_verdicts.json"
    monkeypatch.setattr(verdict_store, "_cache_path", lambda: path)
    valid = ProposedVerdict(proposed="must_read").model_dump()
    original_bytes = json.dumps(
        {"updated_at": "2026-10-08T00:00:00Z", "proposals": {"BAD": malformed, "GOOD": valid}}
    ).encode("utf-8")
    path.write_bytes(original_bytes)

    out = qc.compute_proposal_calibration(labels={"BAD": "must_read", "GOOD": "must_read"})

    assert out["n_pairs"] == 1
    assert out["agreement"] == 1.0
    assert out["cohen_kappa"] is None
    assert path.read_bytes() == original_bytes
    assert not path.with_name(path.name + ".corrupt").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("confidence", True),
        ("proposal_version", True),
        ("confidence", "0.7"),
        ("proposal_version", "1"),
    ],
    ids=["boolean-confidence", "boolean-version", "string-confidence", "string-version"],
)
def test_calibration_rejects_coercible_numeric_metadata_and_keeps_valid_neighbor(field, value):
    malformed = {"proposed": "must_read", field: value}
    valid = ProposedVerdict(
        proposed="should_read", confidence=0.7, proposal_version=1,
    ).model_dump()

    out = qc.compute_proposal_calibration(
        proposals={"BAD": malformed, "GOOD": valid},
        labels={"BAD": "must_read", "GOOD": "should_read"},
    )

    assert out["n_pairs"] == 1
    assert out["agreement"] == 1.0
    assert out["cohen_kappa"] is None


def test_calibration_empty_is_honest_zero():
    out = qc.compute_proposal_calibration(proposals={}, labels={})
    assert out["n_pairs"] == 0 and out["agreement"] == 0.0 and out["cohen_kappa"] is None
    assert out["insufficient"] is True
