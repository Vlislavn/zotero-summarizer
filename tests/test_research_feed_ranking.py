"""B2 machine-I/O/order controls; supplied reviews are not human utility evidence."""
from __future__ import annotations

from copy import deepcopy
import json
import sqlite3

import pytest

from tests.test_research_feed import _review, _settings
from tests.test_research_feed_history_source import (
    END, START, TOPICAL, _assert_one_home, _body, _paper, _run,
)
from zotero_summarizer.models import ResearchCandidate, ResearchFeedTriage
from zotero_summarizer.services.model.rank_blend import blend_scores
from zotero_summarizer.services.research_feed import runner, source
from zotero_summarizer.services.research_feed.profile import load_profile
from zotero_summarizer.storage import repositories


def _row(composite=3, *, summary=None, aux=None):
    return {"composite_score": composite, "shap_contribs_json": json.dumps({
        "summary": {} if summary is None else summary, "aux_context": aux,
    })}


def _stored(settings, index, composite=3, *, summary=None, aux=None,
            decision="triaged_pending", priority="should_read", **source_fields):
    key = _paper(settings, index, **source_fields)
    row = _row(composite, summary={"triage_confidence": 0.4} if summary is None else summary, aux=aux)
    with sqlite3.connect(settings.triage_db_path) as conn:
        conn.execute(
            "UPDATE processed_feed_items SET composite_score=?, reading_priority=?, "
            "decision=?, shap_contribs_json=? WHERE stable_feed_key=?",
            (composite, priority, decision, row["shap_contribs_json"], key),
        )
    return key


def _pair(key, *, score=4, confidence=0.4, include=True, title=None):
    return (
        ResearchCandidate(source_id=key, source="fixture", title=title or key,
                          abstract=TOPICAL, url=f"https://example.test/{key}"),
        ResearchFeedTriage(include=include, score=score, confidence=confidence, rationale="Owned input"),
    )


@pytest.fixture
def rank():
    # Keep boundary cases collectible against old production for classified RED.
    from zotero_summarizer.services.research_feed.ranking import rank_candidates

    return rank_candidates


def test_weekly_continuous_score_breaks_descriptive_tie_before_budget(tmp_path):
    settings = _settings(tmp_path)
    high = _stored(settings, 1, 3.9)
    low = _stored(settings, 2, 3.1)

    payload = _run(settings, shortlist_budget=1, card_budget=1)

    assert [record["candidate"]["source_id"] for record in payload["cards"]] == [high]
    assert payload["cards"][0]["triage"]["score"] == 4
    entries = payload["metadata"]["ranking"]["inputs"]
    assert entries[high]["relevance"] == 3.9 and entries[low]["relevance"] == 3.1
    assert entries[high]["order"] == 1 and entries[low]["order"] == 2
    _assert_one_home(payload)


@pytest.mark.parametrize(("shortlist", "cards"), [(2, 1), (1, 2)])
@pytest.mark.parametrize("outcome", ["ok", "missing", "review_error", "card_error"])
def test_whole_cohort_precedes_both_budgets_and_review_never_backfills(
    tmp_path, monkeypatch, shortlist, cards, outcome,
):
    settings = _settings(tmp_path)
    leader = _stored(settings, 1, 5)
    goal = _stored(settings, 2, 3, aux={"goal_sims": {"owned goal": 0.8}})
    filler = _stored(settings, 3, 1)
    generated, loaded = [], []
    build = runner.build_card
    retained = {key: _review(key) for key in (leader, goal, filler)}

    def ensure(_settings, candidates, timeout, actual_profile):
        assert actual_profile == load_profile(_settings.data_dir)
        generated.append(([candidate.source_id for candidate in candidates], timeout))
        return {}

    def review(key):
        loaded.append(key)
        if outcome == "missing":
            return None
        if outcome == "review_error":
            raise RuntimeError("owned review failure")
        return deepcopy(retained[key])

    def card(candidate, *args, **kwargs):
        if outcome == "card_error":
            raise ValueError("owned card failure")
        return build(candidate, *args, **kwargs)

    monkeypatch.setattr(runner, "_ensure_reviews", ensure)
    monkeypatch.setattr(runner, "build_card", card)
    payload = _run(settings, shortlist_budget=shortlist, card_budget=cards,
                   generate_reviews=True, review_timeout_seconds=1, review_loader=review)

    entries = payload["metadata"]["ranking"]["inputs"]
    assert [entries[key]["blend_key"] for key in (leader, goal, filler)] == pytest.approx([0.6, 0.7, 0])
    assert [entries[key]["order"] for key in (goal, leader, filler)] == [1, 2, 3]
    assert generated == [([goal], 1)] and loaded == [goal]
    assert payload["metadata"]["counts"]["shortlisted"] == shortlist
    assert len(payload["cards"]) == int(outcome == "ok")
    assert payload["manual_full_text_required"] == []
    assert len(payload["unqualified_reviews"]) == int(outcome == "missing")
    if outcome == "missing":
        assert payload["unqualified_reviews"][0]["candidate"]["source_id"] == goal
        assert payload["unqualified_reviews"][0]["reason"] == "review_missing"
    assert len(payload["failed"]) == int(outcome in {"review_error", "card_error"})
    assert all(entry["admission"]["status"] == "eligible"
               for entry in payload["metadata"]["source_history"].values())
    _assert_one_home(payload)


@pytest.mark.parametrize(("native", "fallback", "value", "origin"), [
    (0, 5, 0.0, "native_column"), ("0", False, 0.0, "native_column"),
    (None, 0, 0.0, "summary_fallback"), (None, " 4.25 ", 4.25, "summary_fallback"),
    (4, [], 4.0, "native_column"), (None, None, None, "unobserved"),
])
def test_chosen_raw_observation_precedence_does_not_invent_default(rank, native, fallback, value, origin):
    pair = _pair("one")
    rows = {"one": _row(native, summary={"composite_relevance_score": fallback})}
    before = deepcopy((pair, rows))

    ordered, metadata, failed = rank([pair], rows, ["one"])

    entry = metadata["inputs"]["one"]
    assert ordered == [pair] and failed == []
    assert entry["relevance"] == value and entry["relevance_origin"] == origin
    assert entry["status"] == ("unscored" if value is None else "scored")
    assert (pair, rows) == before
    assert ordered[0][0] is pair[0] and ordered[0][1] is pair[1]


def test_unscored_tail_preserves_old_key_and_exact_ties_without_consuming_aux(rank):
    pairs = [_pair("a", score=4, confidence=0.8, title="A"),
             _pair("z", score=4, confidence=0.8, title="Z"),
             _pair("same", score=4, confidence=0.8, title="Z"),
             _pair("confidence", score=4, confidence=0.9, title="0"),
             _pair("priority", score=5, confidence=0), _pair("zero", score=1)]
    order = [pair[0].source_id for pair in pairs]
    rows = {key: _row(None, aux=False) for key in order}
    rows["zero"] = _row(0)

    ordered, metadata, failed = rank(list(reversed(pairs)), rows, order)

    assert [pair[0].source_id for pair in ordered] == ["zero", "priority", "confidence", "z", "same", "a"]
    assert failed == []
    for key in order[:-1]:
        entry = metadata["inputs"][key]
        assert set(entry) == {"input_order", "status", "reason", "relevance", "relevance_origin",
                              "blend_key", "order"}
        assert entry["reason"] == "no_observed_composite" and entry["blend_key"] is None


@pytest.mark.parametrize(("goals", "citation", "expected"), [
    (None, None, None), ({}, 0, None), ({"a": -1, "b": -0.2}, "0", -0.2),
    ({"a": 0}, 1, 0.0), ({"a": "-1", "b": "4e-1", "c": 1}, "1", 1.0),
])
def test_signed_goal_max_and_known_zero_citation_remain_observations(rank, goals, citation, expected):
    ordered, metadata, failed = rank([_pair("one")], {
        "one": _row(aux={"goal_sims": goals, "citation_percentile": citation}),
    }, ["one"])

    entry = metadata["inputs"]["one"]
    assert len(ordered) == 1 and failed == []
    assert entry["goal_sim"] == expected
    assert entry["citation_percentile"] == (None if citation is None else float(citation))
    json.dumps(metadata, allow_nan=False)


@pytest.mark.parametrize("raw", [None, "{}", '{"summary":{}}', '{"aux_context":null}',
                                  '{"aux_context":{}}'])
def test_missing_aux_objects_are_not_malformed(rank, raw):
    ordered, metadata, failed = rank([_pair("one")], {
        "one": {"composite_score": 3, "shap_contribs_json": raw},
    }, ["one"])

    assert len(ordered) == 1 and failed == []
    assert metadata["inputs"]["one"]["goal_sim"] is None
    assert metadata["inputs"]["one"]["citation_percentile"] is None


@pytest.mark.parametrize("field", ["summary", "aux_context"])
def test_nullable_history_fields_preserve_unknown_and_original_payload(field):
    payload = json.dumps({field: None})
    row = {"shap_contribs_json": payload}

    assert source.summary(row, field) == {}
    assert row["shap_contribs_json"] == payload


@pytest.mark.parametrize(("field", "raw"), [
    *[("aux", value) for value in (False, 0, "", [], 1)],
    *[("map", value) for value in (False, 0, "", [], 1)],
    *[("goal", value) for value in (None, False, True, "", "private input", "nan", "inf", "-inf",
                                    float("nan"), float("inf"), [], {}, -1.01, 1.01)],
    *[("citation", value) for value in (False, True, "", "nan", "inf", float("nan"),
                                        float("-inf"), [], {}, -0.1, 1.1)],
])
def test_invalid_consumed_aux_is_bounded_and_cannot_contaminate_healthy_cohort(rank, field, raw):
    aux = {"goal_sims": {"valid higher goal": 0.9, "invalid nonwinner": raw}} if field == "goal" else {}
    if field == "aux":
        aux = raw
    elif field == "map":
        aux["goal_sims"] = raw
    elif field == "citation":
        aux["citation_percentile"] = raw
    rows = {"bad": _row(1, aux=aux), "leader": _row(5),
            "goal": _row(3, aux={"goal_sims": {"goal": 0.8}})}
    before = json.dumps(rows, sort_keys=True)

    ordered, metadata, failed = rank([_pair(key) for key in rows], rows, list(rows))

    assert [pair[0].source_id for pair in ordered] == ["leader", "goal"]
    assert failed == [{"source_id": "bad", "error": "ValueError: ranking_input_invalid"}]
    assert metadata["inputs"]["bad"] == {
        "input_order": 0, "status": "failed", "reason": "ranking_input_invalid",
    }
    assert [metadata["inputs"][key]["blend_key"] for key in ("leader", "goal")] == pytest.approx(
        blend_scores([5, 3], [None, 0.8], [None, None]))
    assert "private input" not in json.dumps((metadata, failed), allow_nan=False)
    assert json.dumps(rows, sort_keys=True) == before


@pytest.mark.parametrize("fallback", [False, -1, 5.1, "nan", "inf", [], {}])
def test_invalid_chosen_fallback_fails_without_default_or_alternate_rescue(rank, fallback):
    ordered, metadata, failed = rank([_pair("bad"), _pair("healthy")], {
        "bad": _row(None, summary={"composite_relevance_score": fallback}), "healthy": _row(4),
    }, ["bad", "healthy"])

    assert [pair[0].source_id for pair in ordered] == ["healthy"]
    assert metadata["inputs"]["bad"]["status"] == "failed"
    assert failed == [{"source_id": "bad", "error": "ValueError: ranking_input_invalid"}]


@pytest.mark.parametrize("raw", [False, True, -1, 5.1, "nan", float("inf"), "", [], {}])
def test_present_invalid_native_cannot_borrow_valid_fallback(rank, raw):
    ordered, metadata, failed = rank([_pair("bad")], {
        "bad": _row(raw, summary={"composite_relevance_score": 5}),
    }, ["bad"])

    assert ordered == [] and metadata["inputs"]["bad"]["status"] == "failed"
    assert failed == [{"source_id": "bad", "error": "ValueError: ranking_input_invalid"}]


def test_unconsumed_rows_do_not_activate_multirole_goal_exception_or_parse_aux(rank):
    rows = {"one": _row(3, aux={"goal_sims": {"g": 0.8}}),
            "excluded": {"shap_contribs_json": "private invalid JSON"},
            "failed": {"composite_score": False, "shap_contribs_json": "private invalid JSON"},
            "unscored": _row(None, aux=False)}
    pairs = [_pair("one"), _pair("excluded", include=False), _pair("unscored")]

    ordered, metadata, failed = rank(pairs, rows, list(rows))

    assert [pair[0].source_id for pair in ordered] == ["one", "unscored"] and failed == []
    assert metadata["inputs"]["one"]["blend_key"] == pytest.approx(0.5)
    for key in ("excluded", "failed"):
        assert metadata["inputs"][key] == {"input_order": list(rows).index(key), "status": "not_applicable"}
    assert metadata["inputs"]["unscored"]["status"] == "unscored"
    assert "private" not in json.dumps(metadata, allow_nan=False)


@pytest.mark.parametrize(("order", "keys"), [
    (["one", "one"], ["one"]), (["one"], ["one", "one"]),
    (["one"], ["absent"]), ([], ["one"]),
])
@pytest.mark.parametrize("include", [True, False])
def test_cohort_identity_errors_are_loud_not_per_paper_failures(rank, order, keys, include):
    with pytest.raises(ValueError, match="^ranking cohort identities are invalid$"):
        rank([_pair(key, include=include) for key in keys], {}, order)


@pytest.mark.parametrize("permutation", [("c", "a", "b"), ("b", "c", "a")])
def test_scored_ties_use_source_order_and_one_aligned_blend_call(rank, monkeypatch, permutation):
    from zotero_summarizer.services.research_feed import ranking

    pairs = {"a": _pair("a", score=1, confidence=0, title="A"),
             "b": _pair("b", score=5, confidence=1, title="Z"), "c": _pair("c")}
    rows = {key: _row(3) for key in pairs}
    order = ["a", "b", "c"]
    before = deepcopy((pairs, rows, order))
    calls = []

    def blend(relevance, goals, citation):
        calls.append((relevance, goals, citation))
        return blend_scores(relevance, goals, citation)

    monkeypatch.setattr(ranking, "blend_scores", blend)
    ordered, metadata, failed = rank([pairs[key] for key in permutation], rows, order)

    assert [pair[0].source_id for pair in ordered] == order and failed == []
    assert calls == [([3.0] * 3, [None] * 3, [None] * 3)]
    assert metadata["contract"] == "weekly_rank_v1"
    assert metadata["cohort_scope"] == "b1_eligible_valid_observed"
    assert set(metadata) == {"contract", "cohort_scope", "inputs"}
    assert (pairs, rows, order) == before
    assert [metadata["inputs"][key]["input_order"] for key in order] == [0, 1, 2]
    assert [metadata["inputs"][key]["order"] for key in order] == [1, 2, 3]
    assert all(set(entry) == {"input_order", "status", "relevance", "relevance_origin", "goal_sim",
                              "citation_percentile", "blend_key", "order"}
               for entry in metadata["inputs"].values())


@pytest.mark.parametrize(("goals", "expected"), [
    ([0.8, None], [0.7, 0.3]), ([0, None], [0.5, 0.3]), ([-0.2, None], [0.5, 0.3]),
    ([0.7, 0.7], [0.5, 0.5]), ([None, None], [0.5, 0.5]),
])
def test_sole_positive_zero_negative_and_tied_goal_rules_are_unchanged(rank, goals, expected):
    rows = {str(index): _row(3, aux={"goal_sims": None if goal is None else {"g": goal}})
            for index, goal in enumerate(goals)}
    _, metadata, failed = rank([_pair(key) for key in rows], rows, list(rows))

    assert failed == []
    assert [metadata["inputs"][key]["blend_key"] for key in rows] == pytest.approx(expected)
    assert [metadata["inputs"][key]["goal_sim"] for key in rows] == goals


def test_even_known_citation_median_is_inside_primitive_not_a_fabricated_observation(rank):
    citation = [0.1, 0.9, None, 0]
    rows = {str(index): _row(3, aux={"citation_percentile": value})
            for index, value in enumerate(citation)}
    _, metadata, _ = rank([_pair(key) for key in rows], rows, list(rows))
    entries = metadata["inputs"]

    assert [entries[key]["citation_percentile"] for key in rows] == citation
    assert [entries[key]["blend_key"] for key in rows] == pytest.approx(
        blend_scores([3] * 4, [None] * 4, citation))
    _, even, _ = rank([_pair(key) for key in ("0", "1", "2")], rows, ["0", "1", "2"])
    keys = [even["inputs"][key]["blend_key"] for key in ("0", "1", "2")]
    assert keys[0] < keys[2] < keys[1]
    assert keys[2] == pytest.approx(0.5)


@pytest.mark.parametrize("keys", [[], ["one", "two"]])
def test_empty_and_all_failed_cohorts_do_not_crash_or_invent_order(rank, keys):
    ordered, metadata, failed = rank([_pair(key) for key in keys],
                                   {key: _row(aux=False) for key in keys}, keys)

    assert ordered == [] and len(failed) == len(keys)
    assert all(entry["status"] == "failed" and "order" not in entry
               for entry in metadata["inputs"].values())
    json.dumps((metadata, failed), allow_nan=False)


@pytest.mark.parametrize("raw", ["private invalid JSON", "[]", '{"aux_context":false}'])
def test_present_invalid_payload_fails_only_consumed_scored_pair(rank, raw):
    ordered, _, failed = rank([_pair("one")], {
        "one": {"composite_score": 3, "shap_contribs_json": raw},
    }, ["one"])

    assert ordered == []
    assert failed == [{"source_id": "one", "error": "ValueError: ranking_input_invalid"}]


def test_source_helpers_are_canonical_and_absent_row_is_not_an_observed_default(rank):
    assert runner.summary is source.summary and runner.number is source.number
    assert source.summary({"shap_contribs_json": '{"summary":{},"aux_context":null}'}) == {}
    assert source.number(None, default=0.5, maximum=1) == 0.5
    with pytest.raises(ValueError, match="^stored triage summary is invalid JSON$"):
        source.summary({"shap_contribs_json": '{"aux_context":false}'}, field="aux_context")
    ordered, metadata, failed = rank([_pair("one")], {}, ["one"])
    assert len(ordered) == 1 and failed == []
    assert metadata["inputs"]["one"]["relevance"] is None


def test_unused_aux_fields_are_neither_validated_nor_prestige_substitutes(rank):
    _, metadata, failed = rank([_pair("one")], {"one": _row(aux={
        "prestige_score": 5, "cold_start_prestige": 5, "max_author_field_percentile": 1,
        "citation_count": "nan", "goal_relevance": False, "future": {"invalid": []},
    })}, ["one"])

    assert failed == []
    entry = metadata["inputs"]["one"]
    assert entry["goal_sim"] is entry["citation_percentile"] is None
    assert entry["blend_key"] == 0.5


def test_parallel_signals_follow_explicit_source_order_not_assessed_permutation(rank, monkeypatch):
    from zotero_summarizer.services.research_feed import ranking

    rows = {"low": _row(1.25, aux={"goal_sims": {"a": -0.8, "b": -0.3}, "citation_percentile": 0.8}),
            "high": _row(4.75, aux={"citation_percentile": "0"}),
            "mid": _row(3.5, aux={"goal_sims": {"g": ".7"}})}
    calls = []

    def blend(relevance, goals, citation):
        calls.append((relevance, goals, citation))
        return blend_scores(relevance, goals, citation)

    monkeypatch.setattr(ranking, "blend_scores", blend)
    ordered, _, failed = rank([_pair(key) for key in ("high", "mid", "low")], rows, list(rows))

    assert calls == [([1.25, 4.75, 3.5], [-0.3, None, 0.7], [0.8, 0.0, None])]
    assert [pair[0].source_id for pair in ordered] == ["mid", "high", "low"] and failed == []


def test_real_zero_and_selected_unobserved_default_have_different_rank_bases(tmp_path):
    settings = _settings(tmp_path)
    zero = _stored(settings, 1, 0, summary={"composite_relevance_score": 5}, decision="selected")
    missing = _stored(settings, 2, None, summary={}, decision="selected", priority=None, aux=False)

    payload = _run(settings, shortlist_budget=1, card_budget=1, queue_zotero=True, dry_run=True)

    assert [record["candidate"]["source_id"] for record in payload["cards"]] == [zero]
    inputs = payload["metadata"]["ranking"]["inputs"]
    assert inputs[zero]["status"] == "scored" and inputs[zero]["relevance"] == 0
    assert inputs[missing]["status"] == "unscored" and inputs[missing]["relevance"] is None
    assert inputs[missing]["order"] == 2
    assert payload["metadata"]["writebacks"]["attempted"] == 0
    _assert_one_home(payload)


def test_real_admission_callback_recovery_cohort_mask_queue_and_metadata_stay_separate(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    recovered = "\n agent harness benchmark — Δ\u00a0 \t"
    leader = _stored(settings, 1, 5, stored=recovered, materialized="GOOD-LEADER")
    goal = _stored(settings, 2, 3, aux={"goal_sims": {"g": 0.8}}, materialized="GOOD-GOAL")
    bad = _stored(settings, 3, 1, aux={"goal_sims": {"g": 0.9, "bad": False}}, materialized="BAD-RANK")
    veto = _stored(settings, 4, 0, decision="rejected_low_score", aux=False, materialized="BAD-VETO")
    invalid = _stored(settings, 5, summary={"triage_confidence": False}, materialized="BAD-TRIAGE")
    withheld = _stored(settings, 6, 0, aux={"goal_sims": {"g": 1}}, materialized="OLD-TARGET")
    unscored = _stored(settings, 7, None, summary={}, priority=None, decision="selected", aux=False)
    excluded = _stored(settings, 8, 5, priority="dont_read", aux=False)
    with sqlite3.connect(settings.triage_db_path) as conn:
        conn.execute(
            "INSERT INTO processed_feed_items "
            "(feed_library_id, feed_item_id, source_type, stable_feed_key, guid, title, decision, "
            "composite_score, run_id, abstract, shap_contribs_json, materialized_zotero_key) "
            "SELECT feed_library_id, feed_item_id+10000, 'zotero', stable_feed_key, guid, title, "
            "'selected', 0, 'new-bad', 'PRIVATE', 'PRIVATE', 'BAD-WITHHELD' "
            "FROM processed_feed_items WHERE stable_feed_key=?", (withheld,),
        )
    originals = source.load_candidates(settings.triage_db_path, start=START, end=END, limit=100, venue="")
    rows, seen = runner._latest_rows(settings.triage_db_path), []
    triage = runner.triage_candidate

    def inspect(candidate, row, profile):
        seen.append(candidate.source_id)
        assert row == rows[candidate.source_id]
        assert candidate.abstract == (recovered if candidate.source_id == leader else TOPICAL)
        return triage(candidate, row, profile)

    monkeypatch.setattr(runner, "triage_candidate", inspect)
    payload = _run(settings, shortlist_budget=3, card_budget=2, queue_zotero=True, dry_run=False)

    assert set(seen) == set(rows) - {withheld}
    assert len(seen) == 7
    assert [record["candidate"]["source_id"] for record in payload["cards"]] == [leader, goal]
    assert payload["metadata"]["counts"] == {
        "discovered": 8, "deduplicated": 8, "triaged": 6, "shortlisted": 3,
        "full_text_available": 2, "cards_generated": 2, "failed": 3,
    }
    inputs = payload["metadata"]["ranking"]["inputs"]
    assert inputs[bad]["status"] == "failed" and inputs[unscored]["status"] == "unscored"
    assert all(inputs[key]["status"] == "not_applicable" for key in (veto, invalid, withheld, excluded))
    assert [inputs[key]["order"] for key in (leader, goal, unscored)] == [1, 2, 3]
    history = payload["metadata"]["source_history"]
    assert history[bad]["admission"]["status"] == "eligible"
    assert payload["failed"].count({"source_id": bad, "error": "ValueError: ranking_input_invalid"}) == 1
    for original in originals:
        effective, expected = source.admit_history(original, rows.get(original.source_id))
        entry = dict(history[original.source_id])
        entry.pop("admission")
        entry.pop("candidate", None)
        entry.pop("candidate_ref", None)
        assert entry == expected and _body(payload, original.source_id) == effective.model_dump(mode="json")
    assert "PRIVATE" not in json.dumps(payload, allow_nan=False)
    with repositories.with_db_path(settings.triage_db_path):
        assert {change["item_key"] for change in repositories.get_pending_changes(status=None)} == {
            "GOOD-LEADER", "GOOD-GOAL",
        }
    assert payload["metadata"]["writebacks"]["succeeded"] == 2
    _assert_one_home(payload)
