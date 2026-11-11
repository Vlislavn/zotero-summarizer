"""Conflicting ordinal evidence cannot select a binary substitute in mixed cohorts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from test_feedback_analysis_history import bundle, event, log, project
from zotero_summarizer.services.golden.feedback_analysis import analysis, analyze_inputs, history, metrics


@pytest.fixture(autouse=True)
def loaded_sources(tmp_path):
    root = Path(__file__).resolve().parents[1]
    receipt = {}
    for module in (analysis, history, metrics):
        path = Path(module.__file__).resolve()
        assert path.is_relative_to(root)
        receipt[module.__name__] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (tmp_path / "loaded-source.json").write_text(json.dumps(receipt, indent=2) + "\n")


@pytest.mark.parametrize("labels", [("must_read", "dont_read"), ("dont_read", "must_read")])
@pytest.mark.parametrize("binary_time", ["2026-10-03T00:00:00Z", "2026-10-04T00:00:00Z",
                                         "2026-10-05T00:00:00Z"], ids=["earlier", "same", "later"])
def test_conflicting_ordinal_does_not_promote_binary_into_combined_support(tmp_path, labels, binary_time):
    settings = project(tmp_path)
    log(settings, [event("A", "2026-10-04T00:00:00Z", new_user_priority=value) for value in labels] + [
        event("A", binary_time, kind="human_feedback"),
        event("B", new_user_priority="should_read", model_priority="must_read"),
        event("B", kind="human_feedback", human={"kind": "feedback", "value": "reject"}),
    ])
    target = bundle(settings)
    retained_before = {p: p.read_bytes() for p in target.rglob("*") if p.is_file()}

    result = analyze_inputs(target)

    (tmp_path / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    for window in ("last_7d", "last_30d", "all_time"):
        channel = result["periods"][window]["interaction_log"]
        assert channel["raw_observations"] == 5
        assert channel["unique_papers"] == 2
        assert channel["latest_conflicting_papers"] == ["A"]
        assert channel["class_support"]["should_read"]["unique_papers"] == 1
        assert channel["class_support"]["approve"]["unique_papers"] == 0
        assert channel["class_support"]["reject"]["unique_papers"] == 0
        assert channel["agreement"]["with_prediction_count"] == 1
        assert channel["agreement_ci"]["n_unique_papers"] == 1
        assert channel["binary_feedback_class_support"]["approve"]["unique_papers"] == 1
        assert channel["binary_feedback_class_support"]["reject"]["unique_papers"] == 1
        assert channel["category_changes_lower_bound"] is None
    settings.triage_db_path.unlink()
    settings.interaction_log_path.unlink()
    assert analyze_inputs(target) == result
    assert retained_before == {p: p.read_bytes() for p in target.rglob("*") if p.is_file()}


def test_later_unambiguous_ordinal_resolves_conflict_and_keeps_precedence(tmp_path):
    settings = project(tmp_path)
    log(settings, [event("A", "2026-10-04T00:00:00Z", new_user_priority=value)
                   for value in ("must_read", "dont_read")] + [
        event("A", "2026-10-05T00:00:00Z", new_user_priority="should_read"),
        event("A", "2026-10-06T00:00:00Z", kind="human_feedback"),
    ])

    result = analyze_inputs(bundle(settings))

    channel = result["periods"]["all_time"]["interaction_log"]
    assert channel["latest_conflicting_papers"] == []
    assert channel["class_support"]["should_read"]["unique_papers"] == 1
    assert channel["class_support"]["approve"]["unique_papers"] == 0
    assert channel["binary_feedback_class_support"]["approve"]["unique_papers"] == 1
    assert channel["agreement"]["with_prediction_count"] == 1
