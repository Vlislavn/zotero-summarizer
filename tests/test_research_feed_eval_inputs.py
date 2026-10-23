"""Hostile/incomplete capture qualification; no new scorer or authentic data."""
from copy import deepcopy
import json

import pytest

from tests.test_research_feed_eval import _machine, _v2_payload
from tools import eval_research_feed as evaluator
from zotero_summarizer.services.research_feed import ranking


_M = ("papers", 0, "frozen_machine")
_C = (*_M, "candidate")
_T = (*_M, "triage")
_A = (*_M, "admission")
_P = (*_M, "provenance")
_H = (*_P, "source_history")
_R = (*_M, "rank_row")
_E = ("frozen_cohort",)


def _at(payload, path):
    for part in path:
        payload = payload[part]
    return payload


def _set(payload, path, value):
    _at(payload, path[:-1])[path[-1]] = value


def _forbidden(*args, **kwargs):
    pytest.fail("an incomplete snapshot cannot invoke the production helper or legacy fallback")


@pytest.mark.parametrize(("path", "expected"), [
    *(((*_E, name), f"frozen_cohort.{name}") for name in (
        "schema_version", "rank_contract", "cohort_id", "capture_phase", "captured_at", "producer_revision",
        "source_order_ids")),
    *(((*_M, name), f"frozen_machine.{name}") for name in (
        "schema_version", "candidate", "admission", "triage", "rank_row", "provenance")),
    *(((*_C, name), f"frozen_machine.candidate.{name}") for name in (
        "source_id", "abstract", "url", "authors", "doi", "updated_at", "dataset_urls")),
    *(((*_T, name), f"frozen_machine.triage.{name}") for name in (
        "include", "rationale", "confidence", "novelty_claim", "matched_projects")),
    ((*_R, "composite_score"), "frozen_machine.rank_row.composite_score"),
    ((*_R, "shap_contribs_json"), "frozen_machine.rank_row.shap_contribs_json"),
    ((*_P, "basis"), "frozen_machine.provenance.basis"),
    ((*_P, "evidence_basis"), "frozen_machine.provenance.evidence_basis"),
    *(((*_H, name), f"frozen_machine.provenance.source_history.{name}") for name in (
        "history", "reason", "text_basis", "currentness", "content_version")),
    (("papers", 0, "source_id"), "source_id"),
    (("papers", 0, "human_include"), "human_include"),
    (("papers", 0, "must_not_miss"), "must_not_miss"),
])
def test_missing_required_capture_paths_are_unscorable_without_defaulting_or_reduced_cohort(
    monkeypatch, path, expected,
):
    payload = _v2_payload(1)
    del _at(payload, path[:-1])[path[-1]]
    monkeypatch.setattr(ranking, "rank_candidates", _forbidden)
    monkeypatch.setattr(evaluator, "_legacy_evaluate", _forbidden)

    result = evaluator.evaluate(payload)

    assert result["rank_replay"]["status"] == "unscorable"
    assert result["rank_replay"]["basis"] == "incomplete_v2_snapshot"
    assert result["shortlist_precision_at_10"] is result["eligible_must_not_miss_recall_at_10"] is None
    assert any(expected in paths for paths in result["unscorable_fields"].values())
    assert result["legacy_v1_diagnostic"] is None and result["passes"] is False


def test_missing_entire_manifest_member_is_not_a_smaller_cohort(monkeypatch):
    payload = _v2_payload(2)
    payload["papers"].pop()
    monkeypatch.setattr(ranking, "rank_candidates", _forbidden)
    result = evaluator.evaluate(payload)
    assert result["rank_replay"]["source_cohort_size"] == 2
    assert result["rank_replay"]["eligible_count"] is None
    assert result["unscorable_fields"]["native:1"] == ["frozen_machine", "source_id"]


@pytest.mark.parametrize("version", [True, False, 0, 1, 3, 2.0, "2", None, [], {}])
@pytest.mark.parametrize("path", [(*_E, "schema_version"), (*_M, "schema_version")])
def test_declared_bad_versions_are_loud_even_when_other_evidence_is_missing(version, path):
    payload = _v2_payload(1)
    del payload["papers"][0]["frozen_machine"]["provenance"]
    _set(payload, path, version)
    with pytest.raises(ValueError, match="^malformed frozen replay:"):
        evaluator.evaluate(payload)


def test_machine_version_without_envelope_declares_incomplete_v2_not_legacy(monkeypatch):
    payload = _v2_payload(1)
    del payload["frozen_cohort"]
    monkeypatch.setattr(evaluator, "_legacy_evaluate", _forbidden)
    monkeypatch.setattr(ranking, "rank_candidates", _forbidden)
    result = evaluator.evaluate(payload)
    assert result["inclusion_basis"] == "unscorable_incomplete_v2_snapshot"
    assert result["legacy_v1_diagnostic"] is None
    assert "frozen_cohort" in result["unscorable_fields"]["case-0"]


@pytest.mark.parametrize(("path", "value"), [
    (_E, None), (_E, []), (_M, None), (_M, False), (_C, None), (_C, []),
    ((*_C, "source_id"), False), ((*_C, "source_id"), " "), ((*_C, "title"), None),
    ((*_C, "abstract"), None), ((*_C, "abstract"), 1), ((*_C, "url"), False),
    ((*_C, "url"), "ftp://example.test/p"), ((*_C, "url"), "https://"),
    ((*_C, "url"), "https://[invalid"), ((*_C, "authors"), "author"),
    ((*_C, "code_urls"), [False]), ((*_C, "published_at"), 1),
    ((*_C, "updated_at"), "not an ISO date"), (_T, None), (_T, []),
    ((*_T, "include"), 1), ((*_T, "score"), True), ((*_T, "score"), 4.0),
    ((*_T, "score"), "4"), ((*_T, "score"), 0), ((*_T, "score"), 6),
    *(((*_T, "confidence"), value) for value in (False, True, ".4", None, -0.1, 1.1,
                                               float("nan"), float("inf"), float("-inf"))),
    ((*_T, "matched_themes"), None), ((*_T, "matched_projects"), [1]),
    ((*_T, "artifact_signal"), "maybe"), ((*_T, "rationale"), False),
    (_A, []), ((*_A, "status"), "pending"), ((*_A, "reason"), "invented"),
    ((*_A, "native_decision"), "UNKNOWN"), ((*_A, "native_decision"), False),
    (_P, None), ((*_P, "basis"), "post_human"), ((*_P, "evidence_basis"), "guessed"),
    ((*_H, "history"), "stale"), ((*_H, "reason"), "invented"),
    ((*_H, "currentness"), "attested"), ((*_H, "content_version"), None),
    ((*_H, "text_basis"), "generated_summary"), (_R, None), (_R, []),
    ((*_R, "shap_contribs_json"), {}), ((*_R, "shap_contribs_json"), False),
    ((*_E, "rank_contract"), "weekly_rank_v2"), ((*_E, "capture_phase"), "post_human"),
    ((*_E, "producer_revision"), ""), ((*_E, "cohort_id"), False),
    ((*_E, "captured_at"), None), ((*_E, "captured_at"), "2026-08-29T00:00:00"),
    ((*_E, "source_order_ids"), {}), ((*_E, "source_order_ids"), [True]),
    (("papers", 0, "source_id"), None), (("papers", 0, "human_include"), 1),
    (("papers", 0, "must_not_miss"), 0),
])
def test_present_malformed_outer_values_are_bounded_errors(path, value):
    payload = _v2_payload(1)
    _set(payload, path, value)
    with pytest.raises(ValueError, match="^malformed frozen replay:"):
        evaluator.evaluate(payload)


@pytest.mark.parametrize(("name", "value"), [("abstract", " \n\t"), ("url", "")])
def test_blank_original_evidence_is_unavailable_not_fabricated(name, value):
    payload = _v2_payload(1)
    payload["papers"][0]["frozen_machine"]["candidate"][name] = value
    result = evaluator.evaluate(payload)
    assert f"frozen_machine.candidate.{name}" in result["unscorable_fields"]["case-0"]
    assert result["shortlist_precision_at_10"] is None


@pytest.mark.parametrize("path", [_M, _C, _T, _A, _P, _H, _R, _E])
def test_closed_capture_objects_cannot_hide_bodies_scores_or_unknown_fields(path):
    payload = _v2_payload(1)
    _at(payload, path)["private_unknown_blob"] = {"candidate": "not another home"}
    with pytest.raises(ValueError) as error:
        evaluator.evaluate(payload)
    assert str(error.value).startswith("malformed frozen replay:")
    assert "private_unknown_blob" not in str(error.value)


@pytest.mark.parametrize("change", ["duplicate_machine", "extra_machine", "duplicate_label", "extra_label",
                                    "duplicate_order", "normalized_native_collision"])
def test_identity_conflicts_are_not_silently_merged_or_matched_by_position(change):
    payload = _v2_payload(2)
    if change == "duplicate_machine":
        payload["papers"][1]["frozen_machine"]["candidate"]["source_id"] = "native:0"
    elif change == "extra_machine":
        payload["papers"][1]["frozen_machine"]["candidate"]["source_id"] = "foreign"
    elif change == "duplicate_label":
        payload["papers"][1]["source_id"] = " native:0 "
    elif change == "extra_label":
        payload["papers"][1]["source_id"] = "foreign"
    elif change == "duplicate_order":
        payload["frozen_cohort"]["source_order_ids"][1] = "native:0"
    else:
        payload["frozen_cohort"]["source_order_ids"][1] = " native:0 "
    with pytest.raises(ValueError, match="^malformed frozen replay:"):
        evaluator.evaluate(payload)


@pytest.mark.parametrize("case_id", ["case-0", " case-0 "])
@pytest.mark.parametrize("incomplete", [False, True])
def test_duplicate_grader_case_ids_are_loud_even_with_missing_machine_evidence(case_id, incomplete):
    payload = _v2_payload(2)
    payload["papers"][1]["id"] = case_id
    if incomplete:
        del payload["papers"][0]["frozen_machine"]["provenance"]
    with pytest.raises(ValueError, match="^malformed frozen replay: papers.id$"):
        evaluator.evaluate(payload)


def test_native_ids_are_preserved_and_only_grader_join_strips_outer_whitespace():
    payload = _v2_payload(1)
    payload["frozen_cohort"]["source_order_ids"] = [" native:0 "]
    payload["papers"][0]["frozen_machine"]["candidate"]["source_id"] = " native:0 "
    result = evaluator.evaluate(payload)
    assert result["rank_replay"]["ordered_source_ids"] == [" native:0 "]
    assert result["shortlist_precision_at_10"] == 1


@pytest.mark.parametrize("raw", [False, -1, 5.1, "nan", "inf", float("nan"), float("inf"), [], {}])
def test_complete_invalid_native_consumed_values_fail_loud_not_drop(raw):
    payload = _v2_payload()
    payload["papers"][1]["frozen_machine"]["rank_row"]["composite_score"] = raw
    with pytest.raises(ValueError, match="^malformed frozen replay: rank_row.consumed_inputs$"):
        evaluator.evaluate(payload)


@pytest.mark.parametrize(("native", "blob"), [
    (3, "private invalid JSON"), (3, "[]"), (None, '{"summary":false}'),
    (None, '{"summary":{"composite_relevance_score":false}}'),
    (3, '{"aux_context":false}'), (3, '{"aux_context":{"goal_sims":[]}}'),
    *[(3, json.dumps({"aux_context": {"goal_sims": {"valid": 0.9, "nonwinner": value}}}))
      for value in (None, False, "private invalid goal", "nan", "inf", -1.1, 1.1)],
    *[(3, json.dumps({"aux_context": {"citation_percentile": value}}))
      for value in (False, "nan", "inf", -0.1, 1.1)],
])
def test_complete_consumed_json_errors_never_produce_smaller_cohort_metrics(native, blob, monkeypatch):
    payload = _v2_payload()
    payload["papers"][1]["frozen_machine"]["rank_row"].update(composite_score=native, shap_contribs_json=blob)
    actual, calls = ranking.rank_candidates, []

    def inspect(assessed, rows, ids):
        calls.append((len(assessed), len(rows), list(ids)))
        return actual(assessed, rows, ids)

    monkeypatch.setattr(ranking, "rank_candidates", inspect)
    with pytest.raises(ValueError) as error:
        evaluator.evaluate(payload)
    assert str(error.value) == "malformed frozen replay: rank_row.consumed_inputs"
    assert len(calls) == 1 and calls[0] == (3, 3, payload["frozen_cohort"]["source_order_ids"])
    assert "private" not in str(error.value)


def test_incomplete_opaque_content_is_unassessed_but_outer_malformation_is_still_loud(monkeypatch):
    payload = _v2_payload(1)
    del payload["frozen_cohort"]["producer_revision"]
    payload["papers"][0]["frozen_machine"]["rank_row"]["shap_contribs_json"] = "private invalid JSON"
    monkeypatch.setattr(ranking, "rank_candidates", _forbidden)
    result = evaluator.evaluate(payload)
    assert result["rank_replay"]["status"] == "unscorable"
    assert result["rank_replay"]["ordered_source_ids"] is None
    payload["papers"][0]["frozen_machine"]["rank_row"]["shap_contribs_json"] = False
    with pytest.raises(ValueError, match="^malformed frozen replay:"):
        evaluator.evaluate(payload)


@pytest.mark.parametrize("change", ["include", "failed_triage", "excluded_null", "selected_status",
                                    "withheld_reason", "missing_native", "foreign_provenance"])
def test_recorded_output_cross_fields_are_checked_without_rerunning_admission(change):
    payload = _v2_payload(1)
    machine = payload["papers"][0]["frozen_machine"]
    if change == "include":
        machine["triage"]["include"] = False
        del machine["triage"]["rationale"]  # Missing peer must not mask this contradiction.
    elif change == "failed_triage":
        machine["admission"].update(status="failed", reason="skipped_error", native_decision="skipped_error")
    elif change == "excluded_null":
        machine["admission"]["status"] = "excluded"
        machine["triage"] = None
    elif change == "selected_status":
        machine["admission"].update(status="excluded", reason="selected", native_decision="selected")
    elif change == "withheld_reason":
        machine["provenance"]["source_history"].update(history="withheld", reason="history_title_mismatch")
    elif change == "missing_native":
        machine["provenance"]["source_history"].update(history="missing", reason="no_prior_history")
        machine["admission"].update(status="failed", reason="triage_history_invalid")
        machine["triage"] = None
    else:
        machine["provenance"]["source_history"].update(history="missing", reason="no_prior_history", row_id=0)
    with pytest.raises(ValueError, match="^malformed frozen replay:"):
        evaluator.evaluate(payload)


@pytest.mark.parametrize("row_id", [0, -7])
def test_permitted_nullable_provenance_and_existing_optional_dto_domains_are_not_narrowed(row_id):
    payload = _v2_payload(1)
    machine = payload["papers"][0]["frozen_machine"]
    machine["provenance"]["source_history"].update(
        row_id=row_id, run_id=None, model_version=None, created_at=None, updated_at="literal stored text",
    )
    machine["candidate"].update(published_at="2026-08-29T12:00:00", pdf_url="opaque optional locator",
                                code_urls=["opaque original string"])
    before = deepcopy(payload)
    result = evaluator.evaluate(payload)
    assert result["rank_replay"]["status"] == "scorable" and payload == before


@pytest.mark.parametrize("row_id", [False, True, None, 1.0, "1"])
def test_row_id_is_strict_nonboolean_integer_not_coerced(row_id):
    payload = _v2_payload(1)
    payload["papers"][0]["frozen_machine"]["provenance"]["source_history"]["row_id"] = row_id
    with pytest.raises(ValueError, match="^malformed frozen replay:"):
        evaluator.evaluate(payload)
