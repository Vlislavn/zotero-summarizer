"""Consumed-history boundary regressions; owned fixtures are not scientific labels."""
from __future__ import annotations

import json
import sqlite3

import pytest

from tests.test_research_feed import _settings
from tests.test_research_feed_history_source import TOPICAL, _body, _paper, _run
from zotero_summarizer.models import ResearchCandidate, ResearchProfile
from zotero_summarizer.services.research_feed import runner, source


def _input(tmp_path, *, summary=None, composite=4, priority="should_read",
           decision="triaged_pending", stored=TOPICAL):
    settings = _settings(tmp_path)
    key = _paper(settings, 1, stored=stored)
    if summary is None:
        summary = {"triage_confidence": 0.4}
    with sqlite3.connect(settings.triage_db_path) as conn:
        conn.execute(
            "UPDATE processed_feed_items SET shap_contribs_json=?, composite_score=?, "
            "reading_priority=?, decision=? WHERE stable_feed_key=?",
            (json.dumps({"summary": summary}), composite, priority, decision, key),
        )
    return settings, key


def _triage(payload):
    represented = payload["cards"] + payload["rejected_near_threshold"]
    assert len(represented) == 1
    return represented[0]["triage"]


def _invalid(payload, key, decision="triaged_pending"):
    assert payload["cards"] == []
    assert payload["metadata"]["counts"]["failed"] == 1
    assert payload["failed"] == [{"source_id": key, "error": "ValueError: triage_history_invalid"}]
    entry = payload["metadata"]["source_history"][key]
    assert entry["history"] == "accepted" and entry["reason"] == "identity_compatible"
    assert entry["admission"] == {
        "status": "failed", "reason": "triage_history_invalid", "native_decision": decision,
    }
    assert _body(payload, key)["abstract"] == TOPICAL


@pytest.mark.parametrize(("raw", "expected"), [
    (0.0, 0.0), (0, 0.0), ("0", 0.0), (None, 0.5), (0.4, 0.4),
])
def test_confidence_preserves_present_zero_not_truthiness_default(tmp_path, raw, expected):
    settings, _ = _input(tmp_path, summary={"triage_confidence": raw})
    assert _triage(_run(settings))["confidence"] == expected


@pytest.mark.parametrize("raw", [False, True, "", "nan", "inf", -0.1, 1.1])
def test_invalid_present_confidence_is_failed_not_prediction(tmp_path, raw):
    settings, key = _input(tmp_path, summary={"triage_confidence": raw})
    _invalid(_run(settings), key)


@pytest.mark.parametrize("raw", [False, 0, "", []])
def test_falsy_wrong_summary_shape_is_not_empty_object(tmp_path, raw):
    settings, key = _input(tmp_path, summary=raw)
    _invalid(_run(settings), key)


def test_native_real_zero_does_not_borrow_summary_five(tmp_path):
    settings, key = _input(tmp_path, composite=0.0, priority=None,
                           summary={"composite_relevance_score": 5, "triage_confidence": 0.4})
    payload = _run(settings)
    assert payload["cards"] == [] and payload["failed"] == []
    assert _triage(payload)["score"] == 1
    assert payload["metadata"]["source_history"][key]["admission"] == {
        "status": "excluded", "reason": "triage_rule", "native_decision": "triaged_pending",
    }


@pytest.mark.parametrize("raw", [False, True, -1, 5.1, "nan", "inf", "-inf",
                                  float("nan"), float("inf"), float("-inf"), "", [], {}])
def test_invalid_summary_score_fallback_is_failed(tmp_path, raw):
    settings, key = _input(tmp_path, composite=None, priority=None,
                           summary={"composite_relevance_score": raw, "triage_confidence": 0.4})
    _invalid(_run(settings), key)


@pytest.mark.parametrize("raw", [0.0, False])
def test_zero_and_malformed_false_cannot_win_budget_one_over_point_four(tmp_path, raw):
    settings, first = _input(tmp_path, summary={"triage_confidence": raw})
    other = _paper(settings, 2)
    with sqlite3.connect(settings.triage_db_path) as conn:
        conn.execute(
            "UPDATE processed_feed_items SET composite_score=4, reading_priority='should_read', "
            "shap_contribs_json=? WHERE stable_feed_key=?",
            (json.dumps({"summary": {"triage_confidence": 0.4}}), other),
        )
    payload = _run(settings, shortlist_budget=1, card_budget=1)
    assert [record["candidate"]["source_id"] for record in payload["cards"]] == [other]
    assert payload["cards"][0]["triage"]["confidence"] == 0.4
    assert payload["metadata"]["counts"]["failed"] == int(raw is False)
    entry = payload["metadata"]["source_history"][first]
    assert entry["admission"]["status"] == ("failed" if raw is False else "eligible")
    assert "candidate" in entry  # Valid zero remains eligible despite the budget cut.
    assert _body(payload, first)["abstract"] == TOPICAL


@pytest.mark.parametrize(("raw", "confidence", "expected", "observed"), [
    ("4.25", "0", 4, 0.0), ("0", "0.0", 1, 0.0),
    (" 3.5 ", "1", 4, 1.0), ("4e0", "4e-1", 4, 0.4),
    (2.5, 0.5, 2, 0.5), (0, 1, 1, 1.0), (5, 1, 5, 1.0),
])
def test_finite_numeric_strings_and_valid_raw_domain_keep_old_ordinal(tmp_path, raw, confidence, expected, observed):
    settings, _ = _input(tmp_path, composite=raw, priority=None,
                         summary={"triage_confidence": confidence})
    triage = _triage(_run(settings))
    assert triage["score"] == expected and triage["confidence"] == observed


@pytest.mark.parametrize("raw", [False, True, "", "nan", "-inf", float("inf"),
                                  float("-inf"), -1, 5.1, [], {}])
def test_invalid_unused_summary_fallback_and_future_fields_do_not_cull_native_value(tmp_path, raw):
    settings, _ = _input(tmp_path, summary={
        "composite_relevance_score": raw, "triage_confidence": "0.4",
        "goal_relevance": False, "citation_count": "nan", "future_observation": [],
    })
    payload = _run(settings)
    assert len(payload["cards"]) == 1 and payload["failed"] == []
    assert _triage(payload)["score"] == 4


@pytest.mark.parametrize(("summary", "expected"), [({}, 1), ({"composite_relevance_score": None}, 1),
                                                  ({"composite_relevance_score": 0}, 1),
                                                  ({"composite_relevance_score": "4"}, 4)])
def test_missing_native_column_uses_only_chosen_fallback_or_operational_one(tmp_path, summary, expected):
    settings, _ = _input(tmp_path, composite=None, priority=None, summary=summary)
    triage = _triage(_run(settings))
    assert triage["score"] == expected and triage["confidence"] == 0.5


@pytest.mark.parametrize("raw", [None, "{}", '{"summary":null}', '{"summary":{}}'])
def test_only_unrecorded_or_valid_empty_summary_uses_operational_defaults(tmp_path, raw):
    settings, _ = _input(tmp_path, composite=None, priority=None)
    with sqlite3.connect(settings.triage_db_path) as conn:
        conn.execute("UPDATE processed_feed_items SET shap_contribs_json=?", (raw,))
    triage = _triage(_run(settings))
    assert triage["score"] == 1 and triage["confidence"] == 0.5


@pytest.mark.parametrize("raw", ["", "not JSON", "null", "false", "0", '""', "[]", "[{}]",
                                  '{"summary":false}', '{"summary":0}', '{"summary":""}',
                                  '{"summary":[]}', '{"summary":1}', '{"summary":"text"}'])
def test_present_malformed_top_or_summary_is_failed_with_healthy_peer(tmp_path, raw):
    settings, bad = _input(tmp_path)
    good = _paper(settings, 2)
    with sqlite3.connect(settings.triage_db_path) as conn:
        conn.execute("UPDATE processed_feed_items SET shap_contribs_json=? WHERE stable_feed_key=?", (raw, bad))
    payload = _run(settings)
    assert [record["candidate"]["source_id"] for record in payload["cards"]] == [good]
    assert payload["failed"] == [{"source_id": bad, "error": "ValueError: triage_history_invalid"}]
    assert payload["metadata"]["source_history"][bad]["admission"]["reason"] == "triage_history_invalid"


@pytest.mark.parametrize("raw", [False, 0, 1.0, {}, [], b"{}"])
def test_nonstring_top_payload_is_not_coerced_or_preparsed(raw):
    with pytest.raises(ValueError, match="^stored triage summary is invalid JSON$"):
        source.summary({"shap_contribs_json": raw})


@pytest.mark.parametrize("field", ["composite_score", "triage_confidence"])
@pytest.mark.parametrize("raw", [False, True, "", "private fixture value", "nan", "inf", "-inf",
                                  float("nan"), float("inf"), float("-inf"), [], {}, -0.1, 6])
def test_pure_consumed_numbers_reject_types_nonfinite_and_domain_without_raw_echo(field, raw):
    candidate = ResearchCandidate(source_id="pure", source="fixture", title="Neutral", abstract=TOPICAL)
    before = candidate.model_dump(mode="json")
    summary = {"triage_confidence": 0.4}
    row = {"composite_score": 4, "reading_priority": "should_read"}
    if field == "composite_score":
        row[field] = raw  # Direct raw boundary: SQLite REAL cannot attest original bool/NaN.
    else:
        summary[field] = raw
    row["shap_contribs_json"] = json.dumps({"summary": summary})
    row_before = dict(row)
    with pytest.raises(ValueError, match="^stored triage number is invalid$"):
        runner.triage_candidate(candidate, row, ResearchProfile(themes=["agent"], projects=[]))
    assert row == row_before and candidate.model_dump(mode="json") == before


@pytest.mark.parametrize("decision", ["selected", "user_rejected", "gate_rejected", "rejected_low_score"])
@pytest.mark.parametrize(("composite", "summary"), [
    (-1, {"triage_confidence": 0.4}), (5.1, {"triage_confidence": 0.4}),
    (4, {"triage_confidence": False}), (4, []),
])
def test_validation_precedes_selected_and_all_native_vetoes_with_known_priority(tmp_path, decision, composite, summary):
    settings, key = _input(tmp_path, decision=decision, composite=composite, summary=summary)
    _invalid(_run(settings), key, decision)


@pytest.mark.parametrize("raw", ["", "fixture nonnumeric", "nan", "inf", "-inf", -1, 5.1])
def test_present_bad_native_composite_cannot_borrow_valid_summary_five(tmp_path, raw):
    settings, key = _input(tmp_path, composite=raw, summary={
        "composite_relevance_score": 5, "triage_confidence": 0.4,
    })
    _invalid(_run(settings), key)


@pytest.mark.parametrize(("raw", "expected"), [("4.25", 4), ("0", 1), (" 3.5 ", 4)])
def test_public_projected_numeric_strings_are_accepted_without_row_or_candidate_mutation(raw, expected):
    candidate = ResearchCandidate(source_id="pure", source="fixture", title="Neutral", abstract=TOPICAL)
    row = {"composite_score": raw, "shap_contribs_json": '{"summary":{"triage_confidence":"0"}}'}
    before = dict(row), candidate.model_dump(mode="json")
    result = runner.triage_candidate(candidate, row, ResearchProfile(themes=["agent"], projects=[]))
    assert result.score == expected and result.confidence == 0.0
    assert (row, candidate.model_dump(mode="json")) == before


def test_valid_native_zero_retains_existing_descriptive_priority_mapping(tmp_path):
    settings, _ = _input(tmp_path, composite=0, priority="should_read",
                         summary={"composite_relevance_score": False, "triage_confidence": 0.4})
    payload = _run(settings)
    assert _triage(payload)["score"] == 4 and payload["failed"] == []


def test_invalid_consumed_number_does_not_echo_input_into_warning(tmp_path, caplog):
    settings, bad = _input(tmp_path, summary={"triage_confidence": "private fixture value"})
    good = _paper(settings, 2)
    with caplog.at_level("WARNING", logger=runner.LOGGER.name):
        payload = _run(settings)
    warnings = [record for record in caplog.records if record.name == runner.LOGGER.name]
    assert len(warnings) == 1
    assert "private fixture value" not in warnings[0].getMessage()
    assert warnings[0].getMessage() == f"research-feed triage failed source={bad}: triage_history_invalid"
    assert warnings[0].exc_info is None
    assert [record["candidate"]["source_id"] for record in payload["cards"]] == [good]
    assert payload["failed"] == [{"source_id": bad, "error": "ValueError: triage_history_invalid"}]
    assert "private fixture value" not in json.dumps(payload)


def test_numeric_failure_is_bounded_and_does_not_block_healthy_peer(tmp_path):
    settings, bad = _input(tmp_path, summary={"triage_confidence": "private fixture value"})
    good = _paper(settings, 2)
    payload = _run(settings)
    assert [record["candidate"]["source_id"] for record in payload["cards"]] == [good]
    assert payload["failed"] == [{"source_id": bad, "error": "ValueError: triage_history_invalid"}]
    assert "private fixture value" not in json.dumps(payload)
