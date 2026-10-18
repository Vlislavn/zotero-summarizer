"""Synthetic weekly execution, content identity and cursor failure contracts."""
import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_feedback_analysis_history import event, log, project, revision
from zotero_summarizer.services.golden.feedback_analysis import analyze_inputs
from zotero_summarizer.services.golden.feedback_analysis import operation, scheduler, schedule_state

MONDAY = datetime(2026, 10, 12, tzinfo=timezone.utc)


def enabled(tmp_path):
    return replace(project(tmp_path), feedback_schedule_enabled=True)


def due(settings, now=MONDAY):
    return asyncio.run(scheduler.run_due_feedback(settings, now=now))


def test_schedule_initialization_creates_private_operation_parent_first(tmp_path):
    settings = enabled(tmp_path)
    schedule_state.private_root(settings)
    assert settings.feedback_analysis_dir.stat().st_mode & 0o077 == 0
    assert settings.feedback_schedule_dir.stat().st_mode & 0o077 == 0


def test_two_actual_weekly_executions_late_observation_and_frozen_replay(tmp_path):
    settings = enabled(tmp_path)
    log(settings, [event("A")])
    first = due(settings)
    log(settings, [event("A"), event("B", when="2026-09-20T00:00:00Z")])
    revision(settings, "C", "must_read", "2026-09-21T00:00:00Z")
    second = due(settings, MONDAY + timedelta(days=7))

    assert first["status"] == second["status"] == "published"
    assert first["completed_windows"] == second["completed_windows"] == 1
    receipt = second["last_receipt"]
    run = Path(receipt["run_path"])
    result = json.loads((run / "result.json").read_bytes())
    manifest = json.loads((run / "output-manifest.json").read_bytes())
    assert result == analyze_inputs(run / "retained", budgets=manifest["analysis_budgets"])
    assert any(e["item_key"] == "B" for e in result["events"])
    assert receipt["late_observations"]["at_or_before_previous_cutoff"] == 3
    journal = next(e for e in result["events"] if e["channel"] == "journal")
    assert type(journal["revision"]) is int and journal["item_key"] == "C"
    assert receipt["owned_published"] is True and receipt["publication_action"] == "committed"
    for receipt in (first["last_receipt"], second["last_receipt"]):
        original = json.loads(Path(receipt["operation_receipt_path"]).read_bytes())
        assert original["child"]["reaped"] and original["child"]["group_absent"]
    assert due(settings, MONDAY + timedelta(days=7))["status"] == "no_due"


def test_cursor_failure_recovers_without_another_child(tmp_path, monkeypatch):
    settings = enabled(tmp_path)
    log(settings, [event("A")])
    original = scheduler.commit_cursor
    monkeypatch.setattr(scheduler, "commit_cursor", lambda *a: (_ for _ in ()).throw(OSError("synthetic")))
    with pytest.raises(OSError):
        due(settings)
    assert not (settings.feedback_schedule_dir / "cursor.json").exists()
    monkeypatch.setattr(scheduler, "commit_cursor", original)
    monkeypatch.setattr(operation, "_launch", lambda *a: pytest.fail("recovery must not launch"))

    recovered = due(settings)

    assert recovered["last_receipt"]["publication_action"] == "reused"
    assert recovered["last_receipt"]["owned_published"] is False
    assert recovered["last_receipt"]["recovery"] is True
    assert len(list((settings.feedback_analysis_dir / "runs").iterdir())) == 1


def test_fresh_same_window_reuses_identical_but_changed_seed_publishes(tmp_path):
    settings = enabled(tmp_path)
    log(settings, [event("A")])
    intent = scheduler.weekly_intent(MONDAY)
    def fresh(seed):
        return operation.run_scheduled_feedback_analysis(settings, cutoff=MONDAY,
            provenance={"seed": seed}, schedule_intent=intent)
    first, same, changed = fresh(42), fresh(42), fresh(43)

    assert first["content_key"] == same["content_key"] != changed["content_key"]
    assert same["publication_action"] == "reused" and same["owned_published"] is False
    assert changed["publication_action"] == "committed"
    log(settings, [event("A"), event("B")])
    changed_input = fresh(43)
    settings.config_path.write_text("research_goals: []\n")
    changed_config = fresh(43)
    assert len({first["content_key"], changed["content_key"], changed_input["content_key"], changed_config["content_key"]}) == 4
    assert len(list((settings.feedback_analysis_dir / "runs").iterdir())) == 4


@pytest.mark.parametrize("field", ["input_content_digest", "code", "config", "environment", "seed"])
def test_full_content_identity_not_attempt_clock_or_uuid(field):
    payload = {"input_content_digest": "a" * 64, "code": {"sha256": "b" * 64},
               "config": {"sha256": "c" * 64}, "environment": {"python": "3.13", "packages": {"numpy": "x"}},
               "seed": 42, "attempt_id": "one", "run_id": "first", "attempt_clock": "old"}
    key = schedule_state.content_key(payload, scheduler.weekly_intent(MONDAY))
    assert key == schedule_state.content_key(payload | {"attempt_id": "two", "run_id": "next", "attempt_clock": "new"},
                                            scheduler.weekly_intent(MONDAY))
    changed = payload | {field: "different"}
    assert key != schedule_state.content_key(changed, scheduler.weekly_intent(MONDAY))


@pytest.mark.parametrize("failure_kind", ["before_write", "after_write", "schedule_sidecar"])
def test_missing_success_receipt_holds_no_cursor_no_attempt_pile(tmp_path, monkeypatch, failure_kind):
    settings = enabled(tmp_path)
    original = operation.write_protocol
    def broken(path, payload):
        if path.name == ("schedule-receipt.json" if failure_kind == "schedule_sidecar" else "receipt.json"):
            if failure_kind == "after_write":
                original(path, payload)
            raise OSError("synthetic receipt failure")
        return original(path, payload)
    monkeypatch.setattr(operation, "write_protocol", broken)
    if failure_kind == "schedule_sidecar":
        monkeypatch.setattr(schedule_state, "write_protocol", broken)
    with pytest.raises(OSError):
        due(settings)
    attempts = list((settings.feedback_analysis_dir / "attempts").iterdir())
    monkeypatch.setattr(operation, "_launch", lambda *a: pytest.fail("held attempt must not relaunch"))
    with pytest.raises(schedule_state.UnconfirmedPublication):
        due(settings)
    assert list((settings.feedback_analysis_dir / "attempts").iterdir()) == attempts
    assert not (settings.feedback_schedule_dir / "cursor.json").exists()


def test_scheduled_native_collision_is_not_owned_publication(tmp_path, monkeypatch):
    settings = enabled(tmp_path)
    original = operation._publish
    def collision(staging, destination, guard):
        destination.mkdir()
        (destination / "foreign").write_bytes(b"foreign")
        return original(staging, destination, guard)
    monkeypatch.setattr(operation, "_publish", collision)
    with pytest.raises(FileExistsError):
        due(settings)
    failure = json.loads(next(settings.feedback_analysis_dir.glob("attempts/*/failure.json")).read_bytes())
    assert failure["published"] is False
    assert (Path(failure["run_path"]) / "foreign").read_bytes() == b"foreign"
    assert failure["child"]["reaped"] and failure["child"]["group_absent"]
    assert not (settings.feedback_schedule_dir / "cursor.json").exists()


def test_cursor_receipt_unknown_schema_fails_before_new_child(tmp_path, monkeypatch):
    settings = enabled(tmp_path)
    first = due(settings)
    path = settings.feedback_schedule_dir / "cursor.json"
    cursor = json.loads(path.read_bytes())
    cursor["last_receipt"]["schema_version"] = 2
    path.write_text(json.dumps(cursor))
    monkeypatch.setattr(operation, "_launch", lambda *a: pytest.fail("invalid cursor cannot launch"))
    with pytest.raises(ValueError):
        due(settings)
    assert first["last_receipt"]["confirmed_report"] is True
