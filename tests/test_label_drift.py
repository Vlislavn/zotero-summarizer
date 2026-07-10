"""Tests for the label-drift visibility log (Part 3)."""
from __future__ import annotations

import json
from pathlib import Path

from zotero_summarizer.services.model.classifier_drift import (
    label_distribution,
    log_label_drift,
)


def test_label_distribution_canonical_order() -> None:
    dist = label_distribution(
        ["must_read", "must_read", "should_read", "could_read", "dont_read", "dont_read", ""]
    )
    assert dist == {"must_read": 2, "should_read": 1, "could_read": 1, "dont_read": 2, "": 1}


def test_drift_first_run_has_no_delta(tmp_path: Path) -> None:
    """No prior run → prior_n_train None, delta None (first-run, not an error)."""
    log = tmp_path / "runs.jsonl"
    r = log_label_drift(["must_read", "dont_read"], 2,
                        classifier_name="lightgbm", runs_log_path=log)
    assert r["prior_n_train"] is None
    assert r["n_train_delta"] is None
    assert r["n_train"] == 2
    assert r["distribution"]["must_read"] == 1


def test_drift_delta_vs_prior_run(tmp_path: Path) -> None:
    """The delta is n_train now minus the prior run's cv.n_rows."""
    log = tmp_path / "runs.jsonl"
    log.write_text(json.dumps({
        "run_id": "train_lightgbm_1", "classifier": "lightgbm",
        "cv": {"n_rows": 2061},
    }) + "\n")
    r = log_label_drift(["must_read"] * 83 + ["dont_read"] * 2000, 2083,
                        classifier_name="lightgbm", runs_log_path=log)
    assert r["prior_n_train"] == 2061
    assert r["n_train_delta"] == 22  # 2083 - 2061
    assert r["distribution"]["must_read"] == 83


def test_drift_no_runs_log_path_is_first_run() -> None:
    """runs_log_path=None (CLI/dev) → first-run semantics, no crash."""
    r = log_label_drift(["dont_read"], 1, classifier_name="lightgbm", runs_log_path=None)
    assert r["prior_n_train"] is None
    assert r["n_train_delta"] is None
