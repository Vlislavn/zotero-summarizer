"""Frozen independent-oracle evaluation for reading recommendations."""
from __future__ import annotations

import json
import hashlib

from tools.eval_reading_policy import DEFAULT_FIXTURE, evaluate


def test_legacy_fixture_cannot_certify_full_read_without_recorded_source_basis():
    rows = json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))["papers"]
    report = evaluate(rows)["metrics"]

    assert 12 <= report["papers"] <= 20
    assert report["passes"] is False
    assert report["read_precision"] is None
    assert report["policy_read_rate"] == 0
    assert all("full_text_not_confirmed" in row["flags"] for row in evaluate(rows)["results"]
               if row["raw"] == "read")
    assert report["high_friction_full_reads"] == 0
    assert report["weak_evidence_full_reads"] == 0


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
