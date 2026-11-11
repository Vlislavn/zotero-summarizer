"""Transition-only producer ABI compatibility through retained SQLite/JSONL."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from test_feedback_analysis_history import (
    OTHER, STABLE, bundle, event, feed, log, project, revision, sql,
)
from test_feedback_analysis_inputs import CUTOFF, PROVENANCE
from zotero_summarizer.api.routes.sync import _PushRequest
from zotero_summarizer.runtime import AppContext, set_context
from zotero_summarizer.services import interaction_log, run_log
from zotero_summarizer.services.golden import label_verdicts
from zotero_summarizer.services.golden.feedback_analysis import (
    analysis, analyze_inputs, freeze_inputs, history, metrics,
)
from zotero_summarizer.services.sync import service
from zotero_summarizer.storage import repositories

COMPATIBILITY_SOURCES = ("offline_sync", "sync_conflict_resolution")
SOURCES = (*COMPATIBILITY_SOURCES, "user", "zotero", "machine_add", "unknown",
           "prefix_offline_sync", "offline_sync_suffix", "prefix_sync_conflict_resolution",
           "sync_conflict_resolution_suffix", "Offline_sync", " offline_sync", None)


@pytest.fixture(autouse=True)
def loaded_sources(tmp_path):
    root = Path(__file__).resolve().parents[1]
    receipt = {}
    for module in (analysis, history, metrics, service, label_verdicts,
                   interaction_log, run_log, repositories):
        path = Path(module.__file__).resolve()
        assert path.is_relative_to(root)
        receipt[module.__name__] = {
            "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    (tmp_path / "loaded-source.json").write_text(json.dumps(receipt, indent=2) + "\n")


@pytest.fixture
def producer(tmp_path, monkeypatch):
    settings = project(tmp_path)
    set_context(AppContext(settings=settings))  # No app startup/config/model construction.
    monkeypatch.setattr(service.verdict_effects, "apply_verdict_effects", lambda *_a, **_k: {})
    monkeypatch.setattr(service.verdict_effects, "mirror_current_verdict", lambda *_a, **_k: {})
    monkeypatch.setattr(run_log, "short_git_commit", lambda: "synthetic")
    return settings


def mutation(value, base, **fields):
    payload = {
        "mutation_id": str(uuid4()), "device_id": "owned-device", "item_key": "P1",
        "field": "verdict", "operation": "set", "value": value, "comment": "owned transition",
        "model_priority": "must_read", "base_revision": base,
        "created_at": datetime.now(timezone.utc).isoformat(), **fields,
    }
    return _PushRequest(protocol=1, mutations=[payload]).mutations[0].model_dump(mode="json")


def push(settings, payload):
    return service.push(settings.triage_db_path, [payload])["results"][0]


def retained_result(settings, target=None):
    result = analyze_inputs(target or bundle(settings))
    (settings.data_dir / "analysis-result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def log_events(result):
    return [row for row in result["events"] if row["channel"] == "interaction_log"]


@pytest.mark.parametrize("conflict_resolution", [False, True], ids=["ordinary", "real-conflict"])
def test_actual_typed_sync_sqlite_append_retained_analysis(producer, conflict_resolution):
    settings = producer
    db = settings.triage_db_path
    first = mutation("could_read", 0)
    assigned = push(settings, first)
    assert assigned["status"] == "applied"
    base = assigned["applied_revision"]
    no_op = mutation("could_read", base)
    assert push(settings, no_op)["status"] == "applied"
    assert len(run_log.load_runs(settings.interaction_log_path)) == 1
    assert push(settings, first)["status"] == "already_applied"
    previous = "could_read"
    change = mutation("dont_read", base)
    if conflict_resolution:
        repositories.insert_or_update_label_verdict(
            db, item_key="P1", original_derived_priority="must_read",
            user_priority="should_read", comment="concurrent server edit",
        )
        conflict = push(settings, change)
        assert conflict["status"] == "conflict"
        assert conflict["canonical"]["value"] == "should_read"
        assert push(settings, change) == conflict
        assert len(run_log.load_runs(settings.interaction_log_path)) == 1
        change = mutation("dont_read", conflict["conflict_revision"],
                          resolves_mutation_id=change["mutation_id"])
        previous = "should_read"
    changed = push(settings, change)
    assert changed["status"] == "applied"
    assert changed["previous"]["value"] == previous
    assert changed["previous"]["model_priority"] == "must_read"
    assert repositories.get_label_verdict(db, "P1")["user_priority"] == "dont_read"
    delete = mutation(None, changed["applied_revision"], operation="delete")
    assert push(settings, delete)["status"] == "applied"
    assert push(settings, delete)["status"] == "already_applied"
    assert repositories.get_label_verdict(db, "P1") is None
    raw = run_log.load_runs(settings.interaction_log_path)
    assert [row["transition"] for row in raw] == ["assigned", "changed", "retracted"]
    expected_source = "sync_conflict_resolution" if conflict_resolution else "offline_sync"
    assert [row["source"] for row in raw] == ["offline_sync", expected_source, "offline_sync"]
    assert raw[1]["previous_user_priority"] == previous != raw[1]["model_priority"]
    cutoff = datetime.now(timezone.utc)  # Align with real emitter clock, not helper October 8.
    assert all(datetime.fromisoformat(row["ts"]) <= cutoff for row in raw)
    target = settings.feedback_analysis_dir / "retained"
    manifest = freeze_inputs(settings, target, cutoff=cutoff, provenance=PROVENANCE)
    assert (target / "inputs/interaction-events.jsonl").read_bytes() == settings.interaction_log_path.read_bytes()
    result = retained_result(settings, target)
    observed = log_events(result)
    assert [(row["source"], row["surface"], row["previous"], row["value"], row["prediction"])
            for row in observed] == [("offline_sync", "offline_sync", None, "could_read", "must_read"),
                                     (expected_source, "offline_sync", previous, "dont_read", "must_read"),
                                     ("offline_sync", "offline_sync", "dont_read", None, "must_read")]
    assert all(row["recorded_time"] == original["ts"] and row["event_gate_identity"] == ""
               and row["inference_model_identity"] is None for row, original in zip(observed, raw))
    assert all(row["eligibility"] == "eligible" for row in observed)
    assert result["input_content_digest"] == manifest["content_digest"]
    assert [row["kind"] for row in observed] == ["label_transition"] * 3
    period = result["periods"]["all_time"]["interaction_log"]
    assert period["recorded_transition_observations"][previous]["dont_read"] == 1
    assert period["recorded_transition_observations"]["dont_read"]["retracted"] == 1
    assert period["unique_papers"] == 1
    assert period["category_changes_lower_bound"] is None
    assert all(row["reconciliation"] == "unmatched_unconfirmed" for row in observed)
    assert all(row["eligibility"] == "unknown_time" for row in result["events"] if row["channel"] == "journal")


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("variant", ["transition", "approve", "reject", "priority", "outcome"])
def test_exact_source_event_pair_matrix(tmp_path, source, variant):
    settings = project(tmp_path)
    if variant == "transition":
        raw = event("A", source=source, previous_user_priority="could_read",
                    new_user_priority="dont_read", model_priority="must_read", history_known=True)
        expected = "label_transition" if source in (*COMPATIBILITY_SOURCES, "user", "zotero") else "nonpreference"
    elif variant == "outcome":
        raw = event("A", kind="outcome_resolved", source=source)
        expected = "outcome"
    else:
        human = {"kind": "priority" if variant == "priority" else "feedback",
                 "value": "should_read" if variant == "priority" else variant}
        raw = event("A", kind="human_feedback", source=source, human=human)
        expected = ("priority_observation" if variant == "priority" else "feedback") if source in {
            "user", "zotero"} else "provisional" if source == "machine_add" else "nonpreference"
    log(settings, [raw])
    result = retained_result(settings)
    observed = log_events(result)
    assert len(observed) == 1
    assert observed[0]["source"] == source
    assert observed[0]["surface"] == raw["surface"]
    assert observed[0]["kind"] == expected
    assert result["coverage"]["sources"]["interaction_log"]["malformed"] == 0
    assert result["coverage"]["sources"]["interaction_log"]["unknown"] == 0


@pytest.mark.parametrize("source", COMPATIBILITY_SOURCES)
@pytest.mark.parametrize("change,classification", [
    ({"source": []}, "malformed"), ({"source": {}}, "malformed"),
    ({"schema": 9}, "unknown"), ({"schema": True}, "unknown"),
    ({"event": []}, "unknown"), ({"stable_id": []}, "malformed"),
    ({"history_known": "false"}, "malformed"), ({"new_user_priority": []}, "malformed"),
    ({"model_priority": []}, "malformed"), ({"ts": 42}, "malformed"),
    ({"item_key": " "}, "malformed"),
])
def test_strict_parser_rejects_invalid_records_with_locators(tmp_path, source, change, classification):
    settings = project(tmp_path)
    log(settings, [event("A", source=source, **{k: v for k, v in change.items() if k != "source"})
                   | ({"source": change["source"]} if "source" in change else {})])
    result = retained_result(settings)
    coverage = result["coverage"]["sources"]["interaction_log"]
    assert coverage[classification] == 1
    assert coverage["records"] == 1
    assert log_events(result) == []
    located = next(g for g in result["gaps"] if g["reason"] == classification + "_record")
    assert located["locator"] == {"path": "inputs/interaction-events.jsonl", "line": 1,
                                  "byte_start": 0, "byte_end": settings.interaction_log_path.stat().st_size}


@pytest.mark.parametrize("source", COMPATIBILITY_SOURCES)
def test_history_prediction_time_and_identity_unknowns_preserved(tmp_path, source):
    settings = project(tmp_path)
    feed(settings)
    feed(settings, OTHER, 42, 2, "OTHER001")
    rows = [event("A", source=source, history_known=False, previous_user_priority="must_read",
                  model_priority=None, gate_sha="event-time-gate"),
            event("B", source=source, when=None), event("C", source=source, when="2026-10-07"),
            event("D", source=source, when="invalid"),
            event("E", source=source, when="2026-10-09T00:00:00Z"),
            event("feed:42", source=source),
            event("F", source=source, new_user_priority="urgent"),
            event("G", source=source, model_priority="unknown")]
    log(settings, rows)
    observed = log_events(retained_result(settings))
    assert observed[0]["previous"] is observed[0]["prediction"] is None
    assert observed[0]["event_gate_identity"] == "event-time-gate"
    assert all(row["inference_model_identity"] is None for row in observed)
    assert [row["eligibility"] for row in observed] == [
        "eligible", "unknown_time", "unknown_time", "unknown_time", "after_cutoff",
        "ambiguous_identity", "eligible", "eligible",
    ]
    assert [row["time_basis"] for row in observed[1:4]] == [
        "missing_time", "naive_origin_unproven", "invalid_time",
    ]
    assert observed[5]["identity_status"] == "ambiguous"
    assert observed[6]["kind"] == "nonpreference" and observed[6]["value"] == "urgent"
    assert observed[7]["prediction"] is None
    assert observed[0]["kind"] == observed[7]["kind"] == "label_transition"


@pytest.mark.parametrize("source", COMPATIBILITY_SOURCES)
def test_multiplicity_channels_and_retained_only_replay(tmp_path, source):
    settings = project(tmp_path)
    feed(settings)
    change = event("feed:42", source=source, previous_user_priority="could_read",
                   new_user_priority="dont_read", history_known=True, comment="literal local")
    revision(settings, STABLE, "could_read", "2026-10-06T00:00:00Z")
    revision(settings, STABLE, "dont_read", change["ts"], comment=change["comment"])
    log(settings, [change, change, event(STABLE, kind="human_feedback", source=source)])
    target = bundle(settings)
    before = {p.relative_to(target): (p.read_bytes(), p.stat().st_mode)
              for p in target.rglob("*") if p.is_file()}
    result = retained_result(settings, target)
    observed = log_events(result)
    assert observed[0]["content_group"] == observed[1]["content_group"]
    assert observed[0]["locator"] != observed[1]["locator"]
    assert result["coverage"]["content_multiplicity"][observed[0]["content_group"]] == 2
    assert all(row["reconciliation"] == "consistent_unconfirmed" for row in observed[:2])
    assert observed[2]["kind"] == "nonpreference"
    periods = result["periods"]["all_time"]
    assert periods["interaction_log"]["raw_observations"] == 3
    assert periods["interaction_log"]["unique_papers"] == periods["journal"]["unique_papers"] == 1
    assert periods["interaction_log"]["category_changes_lower_bound"] is None
    assert periods["journal"]["category_changes_lower_bound"] == 1
    assert periods["interaction_log"]["recorded_transition_observations"]["could_read"]["dont_read"] == 2
    assert "total_decisions" not in result["coverage"] and "total_decisions" not in periods
    settings.triage_db_path.unlink()
    settings.interaction_log_path.unlink()
    assert analyze_inputs(target) == result
    assert before == {p.relative_to(target): (p.read_bytes(), p.stat().st_mode)
                      for p in target.rglob("*") if p.is_file()}
    assert result["schema_version"] == 1
    assert result["cutoff"] == CUTOFF.isoformat().replace("+00:00", "Z")
    assert result["provenance"] == PROVENANCE
    assert result["result_digest"] == hashlib.sha256(json.dumps(
        {k: v for k, v in result.items() if k != "result_digest"},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()
