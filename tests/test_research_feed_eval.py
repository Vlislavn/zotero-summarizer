import json

from tools.eval_research_feed import FIXTURE, evaluate


def test_research_feed_fixture_cannot_false_green_offline_acceptance() -> None:
    metrics = evaluate(json.loads(FIXTURE.read_text(encoding="utf-8")))
    assert metrics["papers"] == 30
    assert metrics["reported_code_link_precision"] >= 0.9
    assert metrics["fabricated_urls"] == []
    assert metrics["estimated_review_minutes"] <= 30
    assert metrics["read_skim_skip_agreement"] < 0.8
    assert metrics["passes"] is False


def test_human_labels_are_not_inputs_to_research_feed_triage() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    baseline = evaluate(payload)
    for row in payload["papers"]:
        row["decision"] = "selected" if row["decision"] != "selected" else "user_rejected"
        row["projects"] = ["label leakage sentinel"]
    changed = evaluate(payload)
    assert changed["shortlist_precision_at_10"] == baseline["shortlist_precision_at_10"]
    assert changed["must_not_miss_recall"] == baseline["must_not_miss_recall"]
