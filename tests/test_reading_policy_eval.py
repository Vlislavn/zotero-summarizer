"""Frozen independent-oracle evaluation for reading recommendations."""
from __future__ import annotations

import json

from tools.eval_reading_policy import DEFAULT_FIXTURE, evaluate


def test_reading_policy_meets_frozen_fixture_gates():
    rows = json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))["papers"]
    report = evaluate(rows)["metrics"]

    assert 12 <= report["papers"] <= 20
    assert report["passes"] is True
    assert report["read_precision"] >= 0.8
    assert report["idea_rescue_recall"] >= 0.9
    assert report["policy_read_rate"] < report["baseline_read_rate"]
    assert report["high_friction_full_reads"] == 0
    assert report["weak_evidence_full_reads"] == 0
