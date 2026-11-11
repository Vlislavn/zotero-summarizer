"""Conditional replay wiring, not authentic-capture or human-usefulness evidence."""
from copy import deepcopy
import json
import sys

import pytest

from tests.test_research_feed_eval import _machine, _v2_payload
from tools import eval_research_feed as evaluator
from zotero_summarizer.models import ResearchCandidate, ResearchFeedTriage
from zotero_summarizer.services.research_feed import ranking, runner


def test_qualified_v2_uses_frozen_outputs_not_legacy_profile_or_native_replay(monkeypatch):
    payload = _v2_payload()
    before = deepcopy(payload)

    def forbidden(*args, **kwargs):
        pytest.fail("v2 must not recompute native admission, triage, profile or legacy predictions")

    monkeypatch.setattr(evaluator, "_legacy_evaluate", forbidden)
    monkeypatch.setattr(evaluator, "triage_candidate", forbidden)
    monkeypatch.setattr(evaluator, "ResearchProfile", forbidden)
    monkeypatch.setattr(runner, "_assess", forbidden)
    monkeypatch.setattr(runner, "_latest_rows", forbidden)
    monkeypatch.setattr(runner, "load_profile", forbidden)
    result = evaluator.evaluate(payload)

    assert payload == before
    assert result["evaluation_contract_version"] == 2
    assert result["inclusion_basis"] == "frozen_v2_conditional_b2_rank"
    assert result["legacy_v1_diagnostic"] is None
    assert result["rank_replay"] == {
        "status": "scorable", "basis": "pre_human_b1_output", "contract": "weekly_rank_v1",
        "scope": "conditional_b1_eligible", "source_cohort_size": 3, "eligible_count": 3,
        "scored_count": 3, "unscored_count": 0, "ordered_source_ids": ["native:0", "native:1", "native:2"],
    }
    assert result["shortlist_precision_at_10"] == pytest.approx(1 / 3)
    assert result["eligible_must_not_miss_recall_at_10"] == 1
    assert result["must_not_miss_recall"] is None and result["passes"] is False
    assert result["undefined_metrics"] == {} and result["unscorable_fields"] == {}
    for key in ("read_skim_skip_agreement", "reported_code_link_precision", "fabricated_urls",
                "artifact_availability_accuracy", "project_use_coverage", "estimated_review_minutes",
                "runtime_seconds", "llm_tokens", "llm_cost"):
        assert result[key] is None
    assert result["reading_policy_fixture_agreement"] < 0.8
    json.dumps(result, allow_nan=False)


def test_full_cohort_reaches_actual_shared_helper_once_before_top_ten(monkeypatch):
    payload = _v2_payload(12)
    ids = payload["frozen_cohort"]["source_order_ids"]
    for index, row in enumerate(payload["papers"]):
        raw = 3 if index == 9 else 1 if index == 10 else 2 if index == 11 else 5
        row["frozen_machine"] = _machine(ids[index], raw, goals={"g": 0.8} if index == 9 else None)
        row["human_include"] = row["must_not_miss"] = index == 9
    machines = [row["frozen_machine"] for row in payload["papers"]]
    pairs = [(ResearchCandidate.model_validate(m["candidate"]), ResearchFeedTriage.model_validate(m["triage"]))
             for m in machines]
    rows = {m["candidate"]["source_id"]: m["rank_row"] for m in machines}
    actual = ranking.rank_candidates
    expected, _, _ = actual(pairs, rows, ids)
    calls = []

    def inspect(assessed, accepted_rows, source_order_ids):
        calls.append((assessed, deepcopy(accepted_rows), list(source_order_ids)))
        result = actual(assessed, accepted_rows, source_order_ids)
        assert result[1]["inputs"][ids[9]]["blend_key"] == pytest.approx(0.7)
        return result

    monkeypatch.setattr(ranking, "rank_candidates", inspect)
    result = evaluator.evaluate(payload)

    assert len(calls) == 1 and calls[0][1] == rows and calls[0][2] == ids
    assert len(calls[0][0]) == 12
    assert result["rank_replay"]["ordered_source_ids"] == [pair[0].source_id for pair in expected]
    assert result["rank_replay"]["ordered_source_ids"] == [ids[9], *ids[:9], ids[11], ids[10]]
    assert result["shortlist_precision_at_10"] == 0.1
    assert result["eligible_must_not_miss_recall_at_10"] == 1


def test_zero_missing_excluded_and_failed_preserve_actual_consumption_masks(monkeypatch):
    payload = _v2_payload(4)
    ids = payload["frozen_cohort"]["source_order_ids"]
    payload["papers"][0]["frozen_machine"] = _machine(ids[0], 0, citation=0)
    tail = payload["papers"][1]["frozen_machine"] = _machine(ids[1], None)
    tail["admission"].update(reason="selected", native_decision="selected")
    tail["triage"].update(score=5, confidence=1)
    tail["rank_row"]["shap_contribs_json"] = '{"summary":{},"aux_context":false}'
    payload["papers"][2]["frozen_machine"] = _machine(ids[2], status="excluded")
    payload["papers"][3]["frozen_machine"] = _machine(ids[3], status="failed")
    for row in payload["papers"][2:]:
        row["frozen_machine"]["rank_row"] = {"composite_score": False, "shap_contribs_json": "private invalid JSON"}
    actual, captured = ranking.rank_candidates, []

    def inspect(assessed, rows, order):
        result = actual(assessed, rows, order)
        captured.append(result[1]["inputs"])
        assert result[2] == []
        return result

    monkeypatch.setattr(ranking, "rank_candidates", inspect)
    result = evaluator.evaluate(payload)

    assert len(captured) == 1
    assert captured[0][ids[0]]["relevance"] == 0 and captured[0][ids[0]]["citation_percentile"] == 0
    assert captured[0][ids[1]]["relevance"] is None and "goal_sim" not in captured[0][ids[1]]
    assert all(captured[0][key]["status"] == "not_applicable" for key in ids[2:])
    assert result["rank_replay"]["ordered_source_ids"] == ids[:2]
    assert result["rank_replay"]["eligible_count"] == 2
    assert result["rank_replay"]["scored_count"] == result["rank_replay"]["unscored_count"] == 1
    assert "private" not in json.dumps(result, allow_nan=False)


def test_nonempty_measured_zero_is_not_incomplete_or_undefined():
    payload = _v2_payload(11)
    for index, row in enumerate(payload["papers"]):
        row["human_include"] = row["must_not_miss"] = index == 10

    result = evaluator.evaluate(payload)

    assert result["shortlist_precision_at_10"] == result["eligible_must_not_miss_recall_at_10"] == 0.0
    assert result["rank_replay"]["status"] == "scorable" and result["undefined_metrics"] == {}
    del payload["frozen_cohort"]["producer_revision"]
    missing = evaluator.evaluate(payload)
    assert missing["shortlist_precision_at_10"] is missing["eligible_must_not_miss_recall_at_10"] is None
    assert missing["rank_replay"]["status"] == "unscorable" and missing["passes"] is False


@pytest.mark.parametrize("count", [0, 3])
def test_empty_selected_set_and_no_eligible_gold_are_reasoned_nulls(count):
    payload = _v2_payload(count)
    for index, row in enumerate(payload["papers"]):
        row["frozen_machine"] = _machine(row["source_id"], status="excluded" if index else "failed")

    result = evaluator.evaluate(payload)

    assert result["rank_replay"]["status"] == "scorable"
    assert result["rank_replay"]["ordered_source_ids"] == []
    assert result["shortlist_precision_at_10"] is result["eligible_must_not_miss_recall_at_10"] is None
    assert result["undefined_metrics"] == {
        "shortlist_precision_at_10": "empty_selected_set",
        "eligible_must_not_miss_recall_at_10": "no_eligible_must_not_miss_gold",
    }


def test_nonempty_precision_with_no_eligible_must_gold_has_only_recall_undefined():
    payload = _v2_payload()
    for row in payload["papers"]:
        row["must_not_miss"] = False
    result = evaluator.evaluate(payload)
    assert result["shortlist_precision_at_10"] == pytest.approx(1 / 3)
    assert result["eligible_must_not_miss_recall_at_10"] is None
    assert result["undefined_metrics"] == {"eligible_must_not_miss_recall_at_10": "no_eligible_must_not_miss_gold"}


def test_independent_machine_and_label_permutations_never_use_label_side_inputs():
    payload = _v2_payload(12)
    for index, row in enumerate(payload["papers"]):
        row["frozen_machine"]["triage"].update(score=1 + index % 5, confidence=index / 12)
        row["frozen_machine"]["candidate"]["title"] = f"Adversarial {12 - index}"
    baseline = evaluator.evaluate(payload)
    machines = [deepcopy(row["frozen_machine"]) for row in reversed(payload["papers"])]
    labels = [deepcopy(row) for row in payload["papers"][3:] + payload["papers"][:3]]
    for index, (row, machine) in enumerate(zip(labels, machines, strict=True)):
        row.update(id=f"new-case-{index}", title="Other label text", abstract="Not an original",
                   source_url="https://gold.test/not-machine", verified_code_url="https://gold.test/code",
                   decision="selected", projects=["leakage sentinel"], source_id=f" {row['source_id']} ")
        row["frozen_machine"] = machine
    payload["papers"] = labels

    changed = evaluator.evaluate(payload)

    assert changed["rank_replay"] == baseline["rank_replay"]
    assert changed["shortlist_precision_at_10"] == baseline["shortlist_precision_at_10"]
    assert changed["eligible_must_not_miss_recall_at_10"] == baseline["eligible_must_not_miss_recall_at_10"]
    for row in payload["papers"]:
        row["human_include"] = True
        row["must_not_miss"] = row["source_id"].strip() == "native:11"
    gold_changed = evaluator.evaluate(payload)
    assert gold_changed["rank_replay"] == baseline["rank_replay"]
    assert gold_changed["shortlist_precision_at_10"] == 1
    assert gold_changed["eligible_must_not_miss_recall_at_10"] == 0


def test_legacy_diagnostic_preserves_old_calculation_and_actual_three_arg_callback(monkeypatch):
    payload = {"papers": [{"id": "legacy", "title": "Original", "human_include": True,
                           "must_not_miss": True, "frozen_machine": {
                               "source_url": "https://example.test/p", "abstract": "Agent benchmark evaluation",
                               "composite_score": 4.25, "reading_priority": "should_read", "summary": {},
                           }}]}
    actual, seen = evaluator.triage_candidate, []

    def inspect(candidate, row, profile):
        seen.append((candidate.source_id, dict(row)))
        assert "decision" not in row
        return actual(candidate, row, profile)

    monkeypatch.setattr(evaluator, "triage_candidate", inspect)
    before = evaluator._legacy_evaluate(deepcopy(payload))
    result = evaluator.evaluate(deepcopy(payload))
    after = dict(result["legacy_v1_diagnostic"])
    before.pop("evaluator_runtime_seconds")
    after.pop("evaluator_runtime_seconds")
    assert before == after and len(seen) == 2
    assert result["shortlist_precision_at_10"] is result["must_not_miss_recall"] is None
    assert result["rank_replay"]["basis"] == "requires_v2_snapshot" and result["passes"] is False


@pytest.mark.parametrize("fallback", [0, "0", "4.25", "5e0"])
def test_observed_summary_fallback_and_signed_numeric_strings_use_production_projection(fallback, monkeypatch):
    payload = _v2_payload(1)
    machine = payload["papers"][0]["frozen_machine"]
    machine["rank_row"] = {"composite_score": None, "shap_contribs_json": json.dumps({
        "summary": {"composite_relevance_score": fallback},
        "aux_context": {"goal_sims": {"a": "-1", "b": "-.2"}, "citation_percentile": "0"},
    })}
    actual, observed = ranking.rank_candidates, []

    def inspect(assessed, rows, order):
        result = actual(assessed, rows, order)
        observed.append(result[1]["inputs"]["native:0"])
        return result

    monkeypatch.setattr(ranking, "rank_candidates", inspect)
    result = evaluator.evaluate(payload)
    assert result["rank_replay"]["status"] == "scorable" and len(observed) == 1
    assert observed[0]["relevance_origin"] == "summary_fallback"
    assert observed[0]["relevance"] == float(fallback)
    assert observed[0]["goal_sim"] == -0.2 and observed[0]["citation_percentile"] == 0


@pytest.mark.parametrize(("native", "reason", "status"), [
    ("selected", "selected", "eligible"),
    *[(native, "native_veto", "excluded") for native in ("user_rejected", "gate_rejected", "rejected_low_score")],
    *[(native, "duplicate", "excluded") for native in ("rejected_dedup_library", "rejected_dedup_processed")],
    ("skipped_error", "skipped_error", "failed"),
    *[(native, "triage_rule", "eligible") for native in (
        "triaged_pending", "awaiting_review", "rejected_daily_cutoff", "rejected_elbow", "user_approved", "black_swan")],
])
def test_recorded_native_states_validate_output_shape_not_prehuman_authenticity(native, reason, status):
    payload = _v2_payload(1)
    machine = payload["papers"][0]["frozen_machine"] = _machine("native:0", status=status)
    machine["admission"].update(reason=reason, native_decision=native)
    result = evaluator.evaluate(payload)
    assert result["rank_replay"]["status"] == "scorable"
    assert result["rank_replay"]["eligible_count"] == int(status == "eligible")
    assert result["must_not_miss_recall"] is None and result["passes"] is False


def test_existing_cli_entry_reads_supplied_fixture_and_keeps_check_nonzero(tmp_path, monkeypatch, capsys):
    fixture = tmp_path / "owned-v2.json"
    fixture.write_text(json.dumps(_v2_payload()), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["eval_research_feed.py", "--fixture", str(fixture), "--check"])

    status = evaluator.main()

    result = json.loads(capsys.readouterr().out)
    assert status == 1 and result["evaluation_contract_version"] == 2
    assert result["fixture"] == "synthetic-v2-wiring-only" and result["passes"] is False
