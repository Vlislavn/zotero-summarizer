"""Numerical and channel-firewall checks on retained synthetic observations."""
import math

import pytest

from test_feedback_analysis_history import analyze, event, log, project, revision, sql


def feedback(settings, key, signal, prediction, when="2026-10-07T00:00:00Z"):
    sql(settings, "INSERT INTO user_feedback(item_id,feedback_type,signal,original_priority,created_at) "
        "VALUES (?,'explicit',?,?,?)", (key, "explicit_" + signal, prediction, when))


def test_saved_prediction_preserved_with_unknowns_and_class_intervals(tmp_path):
    settings = project(tmp_path)
    rows = [("TP", "approve", "must_read"), ("TN", "reject", "dont_read"),
            ("FP", "reject", "should_read"), ("FN", "approve", "could_read"),
            ("unknown", "approve", "unknown")]
    for key, signal, pred in rows:
        feedback(settings, key, signal, pred)
        sql(settings, "INSERT INTO triage_results(item_id,title,response_json,reading_priority,created_at) "
            "VALUES (?,?,?,'must_read','2026-10-07T12:00:00Z')", (key, key, '{}'))

    result = analyze(settings)

    period = result["periods"]["all_time"]["current_feedback"]
    assert period["unique_papers"] == 5
    assert period["agreement"]["with_prediction_count"] == 4
    assert period["agreement"]["false_positive_count"] == period["agreement"]["false_negative_count"] == 1
    assert period["agreement"]["agreement_rate"] == .5
    assert period["agreement_ci"]["n_unique_papers"] == 4
    assert period["agreement_ci"]["low"] == pytest.approx(.1500389891521495)
    assert period["agreement_ci"]["high"] == pytest.approx(.8499610108478505)
    assert period["class_support"]["approve"]["unique_papers"] == 3
    assert len(period["disagreements"]) == 2
    unknown = next(e for e in result["events"] if e["item_key"] == "unknown" and e["channel"] == "current_feedback")
    assert unknown["prediction"] is None


@pytest.mark.parametrize("n,approved", [(0, 0), (1, 0), (1, 1), (5, 5)])
def test_confidence_edges_and_no_undefined_nan(tmp_path, n, approved):
    settings = project(tmp_path)
    for i in range(n):
        feedback(settings, str(i), "approve" if i < approved else "reject", "must_read")

    result = analyze(settings)

    ci = result["periods"]["all_time"]["current_feedback"]["agreement_ci"]
    if n == 0:
        assert ci["low"] is ci["high"] is None
    else:
        assert 0 <= ci["low"] <= ci["high"] <= 1
        assert math.isfinite(ci["low"]) and math.isfinite(ci["high"])
    assert ci["n_unique_papers"] == n
    assert result["ranking"]["precision_at_10"] is result["ranking"]["ndcg_at_10"] is None
    assert result["ranking"]["status"] == "unscorable"


def test_independent_channels_never_summed_and_machine_outcomes_distinct(tmp_path):
    settings = project(tmp_path)
    revision(settings, "A", "should_read", "2026-10-07T00:00:00Z")
    feedback(settings, "A", "approve", "could_read")
    log(settings, [event("A"), event("A", kind="human_feedback", human={"kind": "priority", "value": "should_read"}),
                   event("A", kind="human_feedback"),
                   event("B", kind="human_feedback", source="machine_add", human={"kind": "keep", "value": "should_read"}),
                   event("C", kind="outcome_resolved", human={"kind": "outcome", "value": "trashed"}),
                   event("D", kind="outcome_resolved", human={"kind": "outcome", "value": "moved_out"})])

    result = analyze(settings)

    periods = result["periods"]["all_time"]
    assert periods["journal"]["unique_papers"] == 1
    assert periods["interaction_log"]["unique_papers"] == 1
    assert periods["current_feedback"]["unique_papers"] == 1
    assert periods["outcomes"]["counts"] == {"trashed": 1, "moved_out": 1}
    assert len([e for e in result["events"] if e["kind"] == "provisional"]) == 1
    assert "total_decisions" not in periods
    assert any(g["reason"] == "unconfirmed_channel_overlap" for g in result["gaps"])


def test_journal_comment_edit_not_preference_or_reversal(tmp_path):
    settings = project(tmp_path)
    revision(settings, "A", "should_read", "2026-10-01T00:00:00Z", comment="before")
    revision(settings, "A", "should_read", "2026-10-02T00:00:00Z", comment="edited")
    revision(settings, "B", "should_read", "2026-10-01T00:00:00Z", source="machine_add")
    revision(settings, "B", "must_read", "2026-10-02T00:00:00Z", source="auto_quality")

    result = analyze(settings)

    period = result["periods"]["all_time"]["journal"]
    assert period["unique_papers"] == 1
    assert period["category_changes_lower_bound"] == period["reversals_lower_bound"] == 0
    assert period["comment_edits"] == 1
    assert period["transitions"]["unknown"]["should_read"] == 1


def test_jsonl_duplicate_change_observations_are_not_confirmed_action_lower_bound(tmp_path):
    settings = project(tmp_path)
    change = event("A", previous_user_priority="should_read", new_user_priority="dont_read", history_known=True)
    log(settings, [change, change])

    result = analyze(settings)

    period = result["periods"]["all_time"]["interaction_log"]
    assert period["raw_observations"] == 2
    assert period["category_changes_lower_bound"] is None
    assert period["recorded_transition_observations"]["should_read"]["dont_read"] == 2
    assert period["unique_papers"] == 1


def test_transition_prediction_error_mean_median_and_capability_breakdown(tmp_path):
    settings = project(tmp_path)
    log(settings, [event("A", model_priority="must_read", new_user_priority="dont_read", surface="annotate"),
                   event("B", model_priority="should_read", new_user_priority="should_read", surface="review"),
                   event("C", model_priority="could_read", new_user_priority="should_read", surface="review")])

    result = analyze(settings)

    numeric = result["periods"]["all_time"]["interaction_log"]["ordinal_error"]
    assert numeric["mean"] == pytest.approx(4 / 3)
    assert numeric["median"] == 1
    assert numeric["by_surface"]["annotate"]["mean"] == 3
    assert numeric["by_surface"]["review"]["median"] == .5
    assert result["periods"]["all_time"]["interaction_log"]["class_support"]["should_read"]["unique_papers"] == 2
    assert "competence" not in numeric
