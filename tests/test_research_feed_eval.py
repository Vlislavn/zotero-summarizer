import json

from tools.eval_research_feed import FIXTURE, evaluate


def test_research_feed_fixture_meets_offline_acceptance_gates() -> None:
    metrics = evaluate(json.loads(FIXTURE.read_text(encoding="utf-8")))
    assert metrics["papers"] == 30
    assert metrics["shortlist_precision_at_10"] >= 0.8
    assert metrics["must_not_miss_recall"] == 1
    assert metrics["reported_code_link_precision"] >= 0.9
    assert metrics["fabricated_urls"] == []
    assert metrics["estimated_review_minutes"] <= 30
    assert metrics["passes"] is True
