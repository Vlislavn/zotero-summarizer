"""Cursor corruption, retained-report admission and failure isolation controls."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from unittest.mock import Mock

import pytest

from tests.test_research_feed import _review, _seed
from tests.test_research_feed_cursor import environment, run, stage_calls, state_path  # noqa: F401 — shared synthetic boundary
from zotero_summarizer.models import ResearchCandidate, ResearchFeedTriage
from zotero_summarizer.services.library._digest_audit import canonical_hash
from zotero_summarizer.services.research_feed import cursor, runner


def _one(environment):
    settings, reviews = environment
    _seed(settings, "paper", "Agent harness benchmark")
    reviews["paper"] = _review("paper")
    payload, result = run(settings)
    return settings, reviews, payload, result


@pytest.mark.parametrize("change", ["json", "envelope", "version", "state_version", "entry", "triage", "typed", "checksum", "path"])
def test_present_malformed_cursor_is_loud_not_an_empty_success(environment, change):
    settings, _reviews, _payload, _result = _one(environment)
    path = state_path(settings)
    state = json.loads(path.read_text())
    entry = state["candidate_cursor"]["entries"]["paper"]
    if change == "json":
        path.write_text("not JSON")
    else:
        if change == "envelope":
            state["candidate_cursor"] = []
        elif change == "version":
            state["candidate_cursor"]["schema_version"] = True
        elif change == "state_version":
            state["schema_version"] = 999
        elif change == "entry":
            entry["unknown"] = "not admitted"
        elif change in {"triage", "typed"}:
            entry["triage"]["include"] = 1
            if change == "typed":
                entry["triage_sha256"] = canonical_hash(entry["triage"])
        elif change == "checksum":
            entry["triage"]["rationale"] = "changed without checksum"
        else:
            entry["card"]["run_id"] = "../outside"
        path.write_text(json.dumps(state))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        run(settings)
    assert path.read_bytes() == before


@pytest.mark.parametrize("link", ["state_file", "report_root"])
def test_cursor_refuses_symlinked_state_owners_before_processing(environment, link):
    settings, _reviews, _payload, _result = _one(environment)
    path = state_path(settings)
    before = path.read_bytes()
    if link == "state_file":
        outside = settings.data_dir / "unrelated-state.json"
        outside.write_bytes(before)
        path.unlink()
        path.symlink_to(outside)
    else:
        root = path.parent
        outside = settings.data_dir / "retained-original-root"
        root.rename(outside)
        root.symlink_to(outside, target_is_directory=True)
    with stage_calls() as calls, pytest.raises(ValueError):
        run(settings)
    assert calls == {"triage": 0, "card": 0, "rank": 0}
    assert path.read_bytes() == before


@pytest.mark.parametrize(("damage", "issue"), [
    ("missing", "report_unavailable"), ("symlink", "report_unavailable"),
    ("json", "report_invalid"), ("group", "report_invalid"),
    ("checksum", "record_checksum_mismatch"), ("candidate", "record_checksum_mismatch"),
])
def test_unusable_report_reference_is_explicit_and_reprocessed_not_repaired(environment, damage, issue):
    settings, _reviews, original, result = _one(environment)
    path = Path(result["json_path"])
    payload = deepcopy(original)
    if damage == "missing":
        path.unlink()
    elif damage == "symlink":
        outside = settings.data_dir / "unrelated.json"
        outside.write_text('{"not":"a weekly report"}')
        path.unlink()
        path.symlink_to(outside)
    elif damage == "json":
        path.write_text("not JSON")
    else:
        if damage == "group":
            payload["research_ideas_by_project"] = {"agent harness": {}}
        elif damage == "candidate":
            payload["cards"][0]["candidate"]["source_id"] = "other-candidate"
        else:
            payload["cards"][0]["card"]["problem"] = "changed without cursor checksum"
        path.write_text(json.dumps(payload))
    before = path.read_bytes() if path.exists() else None
    with stage_calls() as calls:
        rebuilt, _ = run(settings)
    assert calls == {"triage": 0, "card": 1, "rank": 1}
    assert rebuilt["metadata"]["candidate_cursor"]["issues"] == {"paper": issue}
    assert rebuilt["cards"] == original["cards"]
    assert (path.read_bytes() if path.exists() else None) == before


def test_fresh_review_currentness_rejection_never_replays_old_card(environment):
    settings, reviews, _payload, _result = _one(environment)
    reviews["paper"]["current"] = False
    with stage_calls() as calls:
        missing, _ = run(settings)
    assert calls == {"triage": 0, "card": 0, "rank": 1}
    assert missing["cards"] == []
    assert missing["manual_full_text_required"] == []
    assert missing["unqualified_reviews"][0]["candidate"]["source_id"] == "paper"
    assert missing["unqualified_reviews"][0]["reason"] == "review_missing"
    assert missing["metadata"]["candidate_cursor"]["cards_reused"] == 0


@pytest.mark.parametrize("failure", ["source", "native", "ranking"])
def test_fresh_source_native_and_numeric_guards_still_run_on_previously_completed_paper(environment, failure):
    settings, _reviews, _payload, _result = _one(environment)
    with sqlite3.connect(settings.triage_db_path) as connection:
        if failure == "source":
            connection.execute("UPDATE rss_items SET title='incompatible title'")
        elif failure == "native":
            connection.execute("UPDATE processed_feed_items SET decision='skipped_error'")
        else:
            connection.execute("UPDATE processed_feed_items SET shap_contribs_json=?", (json.dumps({
                "summary": {}, "aux_context": {"goal_sims": {"valid": 0.8, "bad": None}},
            }),))
    payload, _ = run(settings)
    assert payload["cards"] == [] and len(payload["failed"]) == 1
    assert payload["metadata"]["candidate_cursor"]["cards_reused"] == 0
    assert payload["metadata"]["ranking"]["inputs"]["paper"]["status"] == (
        "failed" if failure == "ranking" else "not_applicable"
    )
    repeated, _ = run(settings)
    assert repeated["cards"] == [] and len(repeated["failed"]) == 1
    assert repeated["metadata"]["candidate_cursor"]["cards_reused"] == 0


def test_atomic_state_failure_retains_old_successful_cursor_and_retries(environment, monkeypatch):
    settings, _reviews, _payload, result = _one(environment)
    before = state_path(settings).read_bytes()
    old_report = Path(result["json_path"]).read_bytes()
    with sqlite3.connect(settings.triage_db_path) as connection:
        connection.execute("UPDATE processed_feed_items SET composite_score=3.25")
    write = runner.write_json_atomic
    monkeypatch.setattr(runner, "write_json_atomic", Mock(side_effect=OSError("synthetic state failure")))
    with pytest.raises(OSError):
        run(settings)
    assert state_path(settings).read_bytes() == before
    assert Path(result["json_path"]).read_bytes() == old_report
    monkeypatch.setattr(runner, "write_json_atomic", write)
    with stage_calls() as calls:
        payload, _ = run(settings)
    assert calls == {"triage": 1, "card": 1, "rank": 1}
    assert payload["metadata"]["ranking"]["inputs"]["paper"]["relevance"] == 3.25


def test_legacy_window_pointer_is_cold_and_upgrades_only_after_publication(environment):
    settings, reviews = environment
    _seed(settings, "paper", "Agent harness benchmark")
    reviews["paper"] = _review("paper")
    path = state_path(settings)
    path.parent.mkdir(parents=True)
    path.write_text('{"schema_version":1,"last_to":"legacy-window"}')
    with stage_calls() as first_calls:
        first, _ = run(settings)
    assert first_calls == {"triage": 1, "card": 1, "rank": 1}
    assert first["metadata"]["candidate_cursor"]["prior"] == "legacy"
    assert json.loads(path.read_text())["candidate_cursor"]["schema_version"] == 1


def test_stale_stage_schema_output_is_not_reused_or_required_to_match_current_dto(environment):
    settings, _reviews, _payload, _result = _one(environment)
    path = state_path(settings)
    state = json.loads(path.read_text())
    entry = state["candidate_cursor"]["entries"]["paper"]
    entry["input_sha256"] = "0" * 64  # An old stage contract cannot match current input qualification.
    del entry["triage"]["rationale"]
    entry["triage_sha256"] = canonical_hash(entry["triage"])
    path.write_text(json.dumps(state))
    with stage_calls() as calls:
        payload, _ = run(settings)
    assert calls == {"triage": 1, "card": 1, "rank": 1}
    assert payload["metadata"]["candidate_cursor"]["issues"]["paper"] == "input_changed"


def test_retention_uses_existing_source_ceiling_and_keeps_latest_successful_versions(environment):
    settings, _reviews, _payload, _result = _one(environment)
    bound = cursor.MAX_ENTRIES
    assert bound == 5000
    checkpoint = cursor.CandidateCursor(settings)
    triage = ResearchFeedTriage.model_validate(checkpoint.entries["paper"]["triage"])
    identity = checkpoint.entries["paper"]["input_sha256"]
    # No database/model work is needed to exercise bounded cursor bookkeeping.
    for index in range(bound):
        key = f"synthetic:{index}"
        checkpoint.inputs[key] = identity
        checkpoint.remember_triage(key, triage)
    assert len(checkpoint.entries) == bound and "paper" not in checkpoint.entries
    checkpoint.inputs["synthetic:0"] = identity
    checkpoint.remember_triage("synthetic:0", triage)
    checkpoint.inputs["next"] = identity
    checkpoint.remember_triage("next", triage)
    assert len(checkpoint.entries) == bound
    assert "synthetic:0" in checkpoint.entries and "synthetic:1" not in checkpoint.entries
    candidate = ResearchCandidate.model_validate(_payload["cards"][0]["candidate"])
    row = runner._latest_rows(settings.triage_db_path)["paper"]
    reused = checkpoint.assessment(candidate, row, runner.load_profile(settings.data_dir))
    assert reused == triage  # The current invocation can still consult its immutable starting snapshot.
    checkpoint.remember_triage("paper", reused)
    assert len(checkpoint.entries) == bound and checkpoint.entries["paper"]["card"] is not None
