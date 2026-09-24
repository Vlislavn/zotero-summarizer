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
    first = evaluate(payload)
    payload["papers"].reverse()
    second = evaluate(payload)
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
    baseline = evaluate(payload)
    assert baseline["shortlist_precision_at_10"] is not None
    for row in payload["papers"]:
        row["decision"] = "selected" if row["decision"] != "selected" else "user_rejected"
        row["projects"] = ["label leakage sentinel"]
    changed = evaluate(payload)
    assert changed["shortlist_precision_at_10"] == baseline["shortlist_precision_at_10"]
    assert changed["must_not_miss_recall"] == baseline["must_not_miss_recall"]
