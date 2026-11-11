import json

from tools.eval_research_feed import FIXTURE, evaluate


def test_research_feed_fixture_cannot_false_green_offline_acceptance() -> None:
    metrics = evaluate(json.loads(FIXTURE.read_text(encoding="utf-8")))
    assert metrics["papers"] == 30
    assert metrics["inclusion_basis"] == "unscorable_missing_frozen_machine"
    assert len(metrics["unscorable_ids"]) == 30
    assert metrics["shortlist_precision_at_10"] is metrics["must_not_miss_recall"] is None
    assert metrics["reported_code_link_precision"] is metrics["fabricated_urls"] is None
    assert metrics["estimated_review_minutes"] is metrics["read_skim_skip_agreement"] is None
    assert metrics["reading_policy_fixture_agreement"] < 0.8
    assert metrics["passes"] is False


def test_frozen_machine_can_score_a_genuine_zero_without_using_fixture_order() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for row in payload["papers"]:
        row["frozen_machine"] = {
            "source_url": f"https://example.test/{row['id']}",
            "abstract": "A measured but off-topic result.",
            "composite_score": 1.0,
            "reading_priority": "dont_read",
            "summary": {},
        }
    first_main = evaluate(payload)
    payload["papers"].reverse()
    second_main = evaluate(payload)
    # Approved versioned migration: old math is diagnostic, never main B2 evidence.
    first, second = first_main["legacy_v1_diagnostic"], second_main["legacy_v1_diagnostic"]
    assert first_main["shortlist_precision_at_10"] is second_main["shortlist_precision_at_10"] is None
    assert first_main["must_not_miss_recall"] is second_main["must_not_miss_recall"] is None
    assert first_main["inclusion_basis"] == "unscorable_requires_v2_snapshot"
    assert first["inclusion_basis"] == "frozen_machine"
    assert first["shortlist_precision_at_10"] == second["shortlist_precision_at_10"] == 0.0
    assert first["must_not_miss_recall"] == second["must_not_miss_recall"] == 0.0
    assert first["passes"] is False  # no review/artifact evidence


def test_null_frozen_machine_values_do_not_become_measured_predictions() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for row in payload["papers"]:
        row["frozen_machine"] = dict.fromkeys((
            "source_url", "abstract", "composite_score", "reading_priority",
        )) | {"summary": {}}
    metrics = evaluate(payload)
    assert metrics["inclusion_basis"] == "unscorable_missing_frozen_machine"
    assert len(metrics["unscorable_ids"]) == metrics["papers"]
    assert metrics["shortlist_precision_at_10"] is None
    assert metrics["must_not_miss_recall"] is None
    assert metrics["passes"] is False


def test_human_labels_are_not_inputs_to_research_feed_triage() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for row in payload["papers"]:
        row["frozen_machine"] = {
            "source_url": f"https://example.test/{row['id']}",
            "abstract": "Agent benchmark evaluation with independent analysis.",
            "composite_score": 5.0, "reading_priority": "must_read", "summary": {},
        }
    baseline_main = evaluate(payload)
    baseline = baseline_main["legacy_v1_diagnostic"]
    assert baseline_main["shortlist_precision_at_10"] is None
    assert baseline["shortlist_precision_at_10"] is not None
    for row in payload["papers"]:
        row["decision"] = "selected" if row["decision"] != "selected" else "user_rejected"
        row["projects"] = ["label leakage sentinel"]
    changed_main = evaluate(payload)
    changed = changed_main["legacy_v1_diagnostic"]
    assert changed_main["shortlist_precision_at_10"] is None
    assert changed["shortlist_precision_at_10"] == baseline["shortlist_precision_at_10"]
    assert changed["must_not_miss_recall"] == baseline["must_not_miss_recall"]


def _machine(source_id, composite=3, *, status="eligible", goals=None, citation=None):
    """Synthetic captured output for IO controls, not an authentic pre-human receipt."""
    from zotero_summarizer.models import ResearchCandidate, ResearchFeedTriage

    candidate = ResearchCandidate(source_id=source_id, source="app_rss", title=f"Original {source_id}",
                                  abstract="Captured original — Δ\u00a0 text", url="https://example.test/paper")
    triage = ResearchFeedTriage(include=status == "eligible", score=4, confidence=0.4, rationale="Captured")
    return {
        "schema_version": 2, "candidate": candidate.model_dump(mode="json"),
        "admission": {"status": status, "reason": "skipped_error" if status == "failed" else "triage_rule",
                      "native_decision": "skipped_error" if status == "failed" else "triaged_pending"},
        "triage": None if status == "failed" else triage.model_dump(mode="json"),
        "rank_row": {"composite_score": composite, "shap_contribs_json": json.dumps({
            "summary": {}, "aux_context": {"goal_sims": goals, "citation_percentile": citation},
        })} if status == "eligible" else None,
        "provenance": {"basis": "native_pre_human_b1_output", "evidence_basis": "consumed_field_projection",
                       "source_history": {"history": "accepted", "reason": "identity_compatible",
                                          "text_basis": "rss", "currentness": "not_attested",
                                          "content_version": "not_attested"}},
    }


def _v2_payload(count=3):
    ids = [f"native:{index}" for index in range(count)]
    return {
        "fixture": "synthetic-v2-wiring-only",
        "frozen_cohort": {"schema_version": 2, "rank_contract": "weekly_rank_v1",
                          "cohort_id": "owned-synthetic-cohort", "capture_phase": "pre_human_b1_output",
                          "captured_at": "2026-08-29T00:00:00Z", "producer_revision": "owned-test-revision",
                          "source_order_ids": ids},
        "papers": [{"id": f"case-{index}", "source_id": source_id, "human_include": index == 0,
                    "must_not_miss": index == 0, "title": "Grader-only title", "decision": "user_rejected",
                    "projects": [], "frozen_machine": _machine(source_id)}
                   for index, source_id in enumerate(ids)],
    }
