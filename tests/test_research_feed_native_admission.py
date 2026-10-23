"""Native admission belongs to _assess, not the public pure triage projection."""
from __future__ import annotations

import json
import sqlite3

import pytest

from tests.test_research_feed import _review, _settings
from tests.test_research_feed_history_source import (
    END, START, TOPICAL, _assert_one_home, _body, _paper, _run,
)
from tests.test_research_feed_numeric_admission import _input
from zotero_summarizer.models import ResearchCandidate, ResearchProfile
from zotero_summarizer.services.research_feed import runner, source
from zotero_summarizer.storage import feeds as fs, repositories


STATES = [
    (fs.DECISION_SELECTED, "eligible", "selected"),
    (fs.DECISION_USER_REJECTED, "excluded", "native_veto"),
    (fs.DECISION_GATE_REJECTED, "excluded", "native_veto"),
    (fs.DECISION_REJECTED_LOW_SCORE, "excluded", "native_veto"),
    (fs.DECISION_REJECTED_DEDUP_LIBRARY, "excluded", "duplicate"),
    (fs.DECISION_REJECTED_DEDUP_PROCESSED, "excluded", "duplicate"),
    (fs.DECISION_SKIPPED_ERROR, "failed", "skipped_error"),
    (fs.DECISION_TRIAGED_PENDING, "eligible", "triage_rule"),
    (fs.DECISION_AWAITING_REVIEW, "eligible", "triage_rule"),
    (fs.DECISION_REJECTED_DAILY_CUTOFF, "eligible", "triage_rule"),
    (fs.DECISION_REJECTED_ELBOW, "eligible", "triage_rule"),
    (fs.DECISION_USER_APPROVED, "eligible", "triage_rule"),
    (fs.DECISION_BLACK_SWAN, "eligible", "triage_rule"),
]


@pytest.mark.parametrize(("decision", "status", "reason"), STATES)
def test_all_thirteen_native_states_have_explicit_prebudget_disposition(tmp_path, decision, status, reason):
    settings, key = _input(tmp_path, decision=decision)
    row = runner._latest_rows(settings.triage_db_path)[key]
    original = source.load_candidates(settings.triage_db_path, start=START, end=END, limit=1, venue="")[0]
    effective, _entry = source.admit_history(original, row)
    expected = runner.triage_candidate(effective, row, runner.load_profile(settings.data_dir)).model_dump(mode="json")
    expected["include"] = status == "eligible"
    payload = _run(settings)
    assert len(payload["cards"]) == int(status == "eligible")
    assert payload["metadata"]["counts"]["failed"] == int(status == "failed")
    assert payload["metadata"]["source_history"][key]["admission"] == {
        "status": status, "reason": reason, "native_decision": decision,
    }
    represented = payload["cards"] + payload["rejected_near_threshold"]
    if status == "failed":
        assert represented == []
        assert payload["failed"] == [{"source_id": key, "error": "ValueError: skipped_error"}]
    else:
        assert represented[0]["triage"] == expected  # Preserve descriptive fields, not synthetic quality.
    _assert_one_home(payload)


@pytest.mark.parametrize("decision", [fs.DECISION_SELECTED, fs.DECISION_TRIAGED_PENDING,
                                      fs.DECISION_AWAITING_REVIEW, fs.DECISION_REJECTED_DAILY_CUTOFF,
                                      fs.DECISION_REJECTED_ELBOW, fs.DECISION_USER_APPROVED,
                                      fs.DECISION_BLACK_SWAN])
def test_only_selected_bypasses_existing_topic_rule(tmp_path, decision):
    settings, key = _input(tmp_path, decision=decision, stored="Neutral observations")
    payload = _run(settings)
    selected = decision == fs.DECISION_SELECTED
    assert len(payload["cards"]) == int(selected)
    admission = payload["metadata"]["source_history"][key]["admission"]
    assert admission == {"status": "eligible" if selected else "excluded",
                         "reason": "selected" if selected else "triage_rule", "native_decision": decision}
    triage = (payload["cards"] + payload["rejected_near_threshold"])[0]["triage"]
    assert triage["matched_projects"] == triage["matched_themes"] == []
    assert triage["score"] == 4 and triage["confidence"] == 0.4


@pytest.mark.parametrize("decision", ["fixture-private-state", "", " selected", "SELECTED"])
def test_unknown_or_empty_native_string_is_failed_not_ordinary_prediction(tmp_path, decision):
    settings, key = _input(tmp_path, decision=decision)
    payload = _run(settings)
    assert payload["cards"] == [] and payload["rejected_near_threshold"] == []
    assert payload["failed"] == [{"source_id": key, "error": "ValueError: invalid_native_decision"}]
    assert payload["metadata"]["source_history"][key]["admission"] == {
        "status": "failed", "reason": "invalid_native_decision", "native_decision": None,
    }
    assert "fixture-private-state" not in json.dumps(payload)


@pytest.mark.parametrize("case", ["missing", None, False, 0, 7, [], {}])
def test_missing_null_or_wrong_native_type_fails_at_owned_row_boundary(tmp_path, monkeypatch, case):
    settings, key = _input(tmp_path)
    rows = runner._latest_rows(settings.triage_db_path)
    # SQL enforces NOT NULL/TEXT: inject only otherwise-unrepresentable raw shapes.
    if case == "missing":
        del rows[key]["decision"]
    else:
        rows[key]["decision"] = case
    monkeypatch.setattr(runner, "_latest_rows", lambda _path: rows)
    payload = _run(settings)
    assert payload["failed"] == [{"source_id": key, "error": "ValueError: invalid_native_decision"}]
    assert payload["metadata"]["source_history"][key]["admission"] == {
        "status": "failed", "reason": "invalid_native_decision", "native_decision": None,
    }


@pytest.mark.parametrize(("decision", "_status", "_reason"), STATES)
def test_valid_native_observation_survives_numeric_failure_even_veto_or_stage_error(tmp_path, decision, _status, _reason):
    settings, key = _input(tmp_path, decision=decision, summary={"triage_confidence": False})
    payload = _run(settings)
    assert payload["failed"] == [{"source_id": key, "error": "ValueError: triage_history_invalid"}]
    assert payload["metadata"]["source_history"][key]["admission"] == {
        "status": "failed", "reason": "triage_history_invalid", "native_decision": decision,
    }


def test_consumed_invalid_input_precedes_unknown_native_state_without_exporting_it(tmp_path):
    settings, key = _input(tmp_path, decision="fixture-private-state", summary=[])
    payload = _run(settings)
    assert payload["metadata"]["source_history"][key]["admission"] == {
        "status": "failed", "reason": "triage_history_invalid", "native_decision": None,
    }
    assert payload["failed"] == [{"source_id": key, "error": "ValueError: triage_history_invalid"}]
    assert "fixture-private-state" not in json.dumps(payload)


def test_no_prior_history_keeps_public_operational_path_and_distinct_reason(tmp_path):
    settings, key = _input(tmp_path)
    with sqlite3.connect(settings.triage_db_path) as conn:
        conn.execute("DELETE FROM processed_feed_items WHERE stable_feed_key=?", (key,))
    candidate = source.load_candidates(settings.triage_db_path, start=START, end=END, limit=1, venue="")[0]
    expected = runner.triage_candidate(candidate, None, runner.load_profile(settings.data_dir)).model_dump(mode="json")
    payload = _run(settings)
    entry = payload["metadata"]["source_history"][key]
    assert entry["history"] == "missing" and entry["reason"] == "no_prior_history"
    assert entry["admission"] == {"status": "excluded", "reason": "no_prior_history", "native_decision": None}
    assert payload["rejected_near_threshold"][0]["triage"] == expected
    assert payload["failed"] == [] and _body(payload, key) == candidate.model_dump(mode="json")


@pytest.mark.parametrize("decision", [fs.DECISION_USER_REJECTED, "fixture-private-state"])
def test_withheld_identity_does_not_export_native_observations_or_validate_bad_payload(tmp_path, monkeypatch, decision):
    settings, key = _input(tmp_path, decision=decision)
    with sqlite3.connect(settings.triage_db_path) as conn:
        conn.execute("UPDATE processed_feed_items SET source_type='zotero', shap_contribs_json='PRIVATE fixture' "
                     "WHERE stable_feed_key=?", (key,))

    def forbidden(_candidate, _row, _profile):
        pytest.fail("withheld history must not reach triage")

    monkeypatch.setattr(runner, "triage_candidate", forbidden)
    payload = _run(settings)
    entry = payload["metadata"]["source_history"][key]
    assert entry["history"] == "withheld" and entry["reason"] == "history_source_type_unverifiable"
    assert entry["admission"] == {"status": "failed", "reason": "history_withheld", "native_decision": None}
    assert not {"row_id", "run_id", "model_version", "created_at", "updated_at"} & entry.keys()
    assert _body(payload, key)["abstract"] == ""
    assert payload["failed"] == [{"source_id": key, "error": "ValueError: history_source_type_unverifiable"}]
    assert "PRIVATE" not in json.dumps(payload) and "fixture-private-state" not in json.dumps(payload)


@pytest.mark.parametrize("decision", [fs.DECISION_USER_REJECTED, fs.DECISION_GATE_REJECTED,
                                      fs.DECISION_REJECTED_LOW_SCORE, fs.DECISION_REJECTED_DEDUP_LIBRARY,
                                      fs.DECISION_REJECTED_DEDUP_PROCESSED, fs.DECISION_SKIPPED_ERROR,
                                      "fixture-private-state"])
def test_new_native_exclusions_and_failures_never_queue_bad_target_while_peer_continues(tmp_path, decision):
    settings, bad = _input(tmp_path, decision=decision)
    good = _paper(settings, 2, materialized="GOOD-TARGET")
    with sqlite3.connect(settings.triage_db_path) as conn:
        conn.execute("UPDATE processed_feed_items SET materialized_zotero_key='BAD-TARGET' WHERE stable_feed_key=?", (bad,))
    payload = _run(settings, queue_zotero=True, dry_run=False)
    assert [record["candidate"]["source_id"] for record in payload["cards"]] == [good]
    assert payload["metadata"]["writebacks"]["succeeded"] == 1
    with repositories.with_db_path(settings.triage_db_path):
        assert repositories.get_pending_changes(status=None, item_key="BAD-TARGET") == []
        assert len(repositories.get_pending_changes(status=None, item_key="GOOD-TARGET")) == 1
    _assert_one_home(payload)


def test_public_three_positional_callback_sees_exact_recovered_text_and_original_row(tmp_path, monkeypatch):
    recovered = "\n agent harness benchmark — Δ\u00a0 \t"
    settings, key = _input(tmp_path, stored=recovered)
    row_before = runner._latest_rows(settings.triage_db_path)[key]
    original = runner.triage_candidate
    seen = []

    def inspect(candidate, row, profile):
        assert candidate.abstract == recovered and row == row_before
        before = candidate.model_dump(mode="json"), dict(row)
        result = original(candidate, row, profile)
        assert (candidate.model_dump(mode="json"), row) == before
        seen.append(candidate.source_id)
        return result

    monkeypatch.setattr(runner, "triage_candidate", inspect)
    payload = _run(settings)
    assert seen == [key] and _body(payload, key)["abstract"] == recovered
    assert payload["cards"][0]["provenance"]["basis"] == "full_text"


def test_public_callback_failure_is_bounded_and_keeps_valid_native_observation(tmp_path, monkeypatch):
    settings, bad = _input(tmp_path, decision=fs.DECISION_USER_REJECTED)
    good = _paper(settings, 2)
    original = runner.triage_candidate

    def fail_one(candidate, row, profile):
        if candidate.source_id == bad:
            raise RuntimeError("PRIVATE callback fixture")
        return original(candidate, row, profile)

    monkeypatch.setattr(runner, "triage_candidate", fail_one)
    payload = _run(settings)
    assert [record["candidate"]["source_id"] for record in payload["cards"]] == [good]
    assert payload["failed"] == [{"source_id": bad, "error": "RuntimeError: triage_history_invalid"}]
    assert payload["metadata"]["source_history"][bad]["admission"] == {
        "status": "failed", "reason": "triage_history_invalid", "native_decision": fs.DECISION_USER_REJECTED,
    }
    assert "PRIVATE" not in json.dumps(payload)


def test_prebudget_metadata_is_one_addition_across_all_homes_failures_and_cuts(tmp_path):
    settings = _settings(tmp_path)
    keys = {index: _paper(settings, index) for index in range(1, 25)}
    with sqlite3.connect(settings.triage_db_path) as conn:
        for index in range(9, 21):
            conn.execute("UPDATE processed_feed_items SET decision=? WHERE stable_feed_key=?",
                         (fs.DECISION_REJECTED_LOW_SCORE, keys[index]))
        conn.execute("UPDATE processed_feed_items SET shap_contribs_json=? WHERE stable_feed_key=?",
                     (json.dumps({"summary": {"triage_confidence": False}}), keys[21]))
        conn.execute("UPDATE processed_feed_items SET decision='fixture-private-state' WHERE stable_feed_key=?", (keys[22],))
        conn.execute("UPDATE processed_feed_items SET decision=? WHERE stable_feed_key=?", (fs.DECISION_SKIPPED_ERROR, keys[23]))
        conn.execute("UPDATE processed_feed_items SET source_type='zotero' WHERE stable_feed_key=?", (keys[24],))
    admitted = source.load_candidates(settings.triage_db_path, start=START, end=END, limit=100, venue="")
    rows = runner._latest_rows(settings.triage_db_path)

    def loader(key):
        if key == keys[7]:
            return None
        if key == keys[6]:
            raise RuntimeError("owned downstream review failure")
        return _review(key)

    payload = _run(settings, shortlist_budget=5, card_budget=3, review_loader=loader)
    history = payload["metadata"]["source_history"]
    assert list(history) == [candidate.source_id for candidate in admitted]
    assert payload["metadata"]["counts"] == {
        "discovered": 24, "deduplicated": 24, "triaged": 20, "shortlisted": 5,
        "full_text_available": 1, "cards_generated": 1, "failed": 5,
    }
    assert history[keys[8]]["candidate_ref"] == "/cards/0/candidate"
    assert history[keys[7]]["candidate_ref"] == "/unqualified_reviews/0/candidate"
    assert payload["manual_full_text_required"] == []
    assert payload["unqualified_reviews"][0]["candidate"]["source_id"] == keys[7]
    assert payload["unqualified_reviews"][0]["reason"] == "review_missing"
    assert len(payload["rejected_near_threshold"]) == 10
    assert sum("candidate" in history[keys[index]] for index in range(9, 21)) == 2
    for index in range(1, 9):
        assert history[keys[index]]["admission"] == {
            "status": "eligible", "reason": "triage_rule", "native_decision": fs.DECISION_TRIAGED_PENDING,
        }  # Includes the downstream failure, missing review and all budget cuts.
    for original in admitted:
        effective, expected_entry = source.admit_history(original, rows.get(original.source_id))
        actual_entry = dict(history[original.source_id])
        assert set(actual_entry.pop("admission")) == {"status", "reason", "native_decision"}
        actual_entry.pop("candidate", None)
        actual_entry.pop("candidate_ref", None)
        assert actual_entry == expected_entry
        assert _body(payload, original.source_id) == effective.model_dump(mode="json")
    _assert_one_home(payload)
    capped = _run(settings, source_limit=4)
    assert list(capped["metadata"]["source_history"]) == [candidate.source_id for candidate in admitted[:4]]
    _assert_one_home(capped)


@pytest.mark.parametrize("decision", [None, fs.DECISION_REJECTED_LOW_SCORE,
                                      fs.DECISION_REJECTED_DEDUP_LIBRARY, fs.DECISION_SKIPPED_ERROR,
                                      "fixture-private-state"])
def test_pure_projection_remains_legal_without_native_identity_or_expanded_state_gate(decision):
    candidate = ResearchCandidate(source_id="projected", source="fixture", title="Neutral", abstract=TOPICAL)
    projected = {"composite_score": 4, "reading_priority": "should_read", "shap_contribs_json": '{"summary":{}}'}
    if decision is not None:
        projected["decision"] = decision
    before = dict(projected)
    result = runner.triage_candidate(candidate, projected, ResearchProfile(themes=["agent"], projects=[]))
    assert result.include is True and result.score == 4 and result.confidence == 0.5
    assert projected == before
