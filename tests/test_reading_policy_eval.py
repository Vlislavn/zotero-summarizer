"""Frozen independent-oracle evaluation for reading recommendations."""
from __future__ import annotations

import json
import hashlib

from tools.eval_reading_policy import DEFAULT_FIXTURE, evaluate


def test_frozen_capture_missing_digest_basis_cannot_certify_read_precision():
    rows = json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))["papers"]
    report = evaluate(rows)["metrics"]

    assert 12 <= report["papers"] <= 20
    assert all("basis" not in row["signals"]["digest"] for row in rows)
    assert report["passes"] is False
    assert report["read_precision"] is None
    assert report["policy_read_rate"] == 0
    assert report["idea_rescue_recall"] >= 0.9
    assert report["policy_read_rate"] < report["baseline_read_rate"]
    assert report["high_friction_full_reads"] == 0
    assert report["weak_evidence_full_reads"] == 0


def test_explicit_full_text_synthetic_control_retains_defined_read_precision():
    signals = {"digest": {"read_decision": "read", "basis": "full_text", "novelty": 5,
                          "significance": 4, "writing_friction": "low"},
               "quality": {"quality_band": "highlight"},
               "goal_summaries": [{"retrieval_state": "hit", "relevant": True, "abstained": False}]}
    read = {"idea_worth_preserving": True, "writing_friction": "low", "scientific_concern": "no",
            "expected_read_decision": "read", "signals": signals}
    skim = {**read, "writing_friction": "high", "expected_read_decision": "skim",
            "signals": {**signals, "digest": {**signals["digest"], "writing_friction": "high"}}}

    report = evaluate([read, skim])["metrics"]

    assert report["read_precision"] == 1.0
    assert report["policy_read_rate"] == 0.5
    assert report["idea_rescue_recall"] == 1.0
    assert report["passes"] is True  # machine control only, not a labelled-paper study


def test_captured_fixture_preserves_all_original_human_annotations():
    captured = json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))
    original_bytes = DEFAULT_FIXTURE.with_name("reading_policy_fixture.json").read_bytes()
    original = json.loads(original_bytes)

    assert hashlib.sha256(original_bytes).hexdigest() == captured["source"]["annotations_sha256"]
    assert [{key: value for key, value in row.items() if key not in {"signals", "source_reviewed_at"}}
            for row in captured["papers"]] == [
        {key: value for key, value in row.items() if key != "signals"} for row in original["papers"]
    ]


def test_incomplete_legacy_assessments_cannot_pass_the_reading_gate():
    original = json.loads(DEFAULT_FIXTURE.with_name("reading_policy_fixture.json").read_text(encoding="utf-8"))

    report = evaluate(original["papers"])["metrics"]

    assert report["read_precision"] is None
    assert report["passes"] is False
