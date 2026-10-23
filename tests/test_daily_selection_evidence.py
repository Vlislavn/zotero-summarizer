"""Synthetic Today source observations; no user-pool or calibration claims."""
from __future__ import annotations

import json
import math
import sqlite3
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from tests._daily_select_helpers import _DEFAULT_NOW, _create_db, _insert
from zotero_summarizer.api.routes.daily import _slate_to_payload
from zotero_summarizer.services import library
from zotero_summarizer.services.library import review_eligibility
from zotero_summarizer.services.triage import daily_select
from zotero_summarizer.services.triage.daily_select import DailySlate, SlatePaper
from zotero_summarizer.services.triage.daily_select import _allocation as allocator
from zotero_summarizer.services.triage.daily_select import _candidate as candidate
from zotero_summarizer.services.triage.daily_select._selection_evidence import (
    review_observations,
    source_observations,
)


@pytest.fixture
def source_row(tmp_path):
    db = tmp_path / "triage.db"
    _create_db(db)
    _insert(db, item_key="fixture", decision="awaiting_review", composite_score=3.0,
            corpus_affinity=0.2, feed_name="Synthetic feed")
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE processed_feed_items SET reading_priority='must_read'")
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM processed_feed_items").fetchone())
    return db, row


def _paper(**overrides):
    values = {
        "item_key": "fixture",
        "item_id": 1,
        "stable_feed_key": "",
        "title": "Test paper",
        "authors": "",
        "venue": "",
        "role": "model",
        "composite_score": 3.0,
        "surprise_score": 0.0,
        "corpus_affinity": 0.2,
        "prestige_score": 4.2 / 5.0,
        "rationale": "",
        "shap_top": [
            {"feature": "venue_h_index", "contribution": 0.12},
            {"feature": "year_recency", "contribution": -0.04},
            {"feature": "title_log_len", "contribution": 0.01},
        ],
        "decision": "awaiting_review",
        "max_author_h_index": None,
        "feed_name": "Synthetic feed",
        "quality": {},
        "review_ready": False,
        "abstract": "",
        "pub_year": None,
        "why": [],
        "goal_sim": None,
        "url": "",
    }
    values.update(overrides)
    return SlatePaper(**values)


def _old_candidate(cand):
    return {key: value for key, value in cand.items() if key != "selection_evidence"}


class _Reviews(dict):
    def __init__(self, entries):
        super().__init__(entries)
        self.gets = []

    def get(self, key, default=None):
        self.gets.append(key)
        return super().get(key, default)


def _join_snapshot(monkeypatch, cohort, reviews):
    reads = []

    def current_reviews():
        reads.append("current_reviews")
        return reviews

    monkeypatch.setattr(library, "deep_review", SimpleNamespace(current_reviews=current_reviews),
                        raising=False)
    matched = candidate.attach_quality_from_reviews(cohort)
    assert reads == ["current_reviews"]
    return matched


def test_dto_defaults_and_api_projection_are_additive_and_independent():
    first, second = _paper(), _paper()
    assert first.selection_evidence == second.selection_evidence == {"basis": "not_observed"}
    assert first.selection_evidence is not second.selection_evidence
    first.selection_evidence["fixture"] = True
    assert second.selection_evidence == {"basis": "not_observed"}
    slate = DailySlate(papers=[second], pool_size=1, lookback_hours=168)
    assert slate.selection_policy == "not_observed"
    payload = _slate_to_payload(slate)
    assert payload == {
        "papers": [asdict(second)], "pool_size": 1, "lookback_hours": 168,
        "empty_role_events": [], "fellback_to_recent": False,
        "low_relevance_hidden": 0, "weak_slate": False, "selection_policy": "not_observed",
    }
    payload["papers"][0]["selection_evidence"]["fixture"] = True
    payload["empty_role_events"].append("fixture")
    assert second.selection_evidence == {"basis": "not_observed"}
    assert slate.empty_role_events == []


@pytest.mark.parametrize("score,priority,known", [
    (None, "must_read", "must_read"),
    (0.0, "dont_read", "dont_read"),
    ("0.0", "must_read", "must_read"),
    (3.2, "could_read", "could_read"),
    (4.1, "should_read", "should_read"),
    (4.8, None, None),
    (2.5, "", None),
    (2.5, "MUST_READ", None),
    (2.5, " must_read ", None),
    (2.5, "private-calibrator-label", None),
])
def test_native_sql_presence_and_priority_are_not_reconstructed(source_row, score, priority, known):
    db, _ = source_row
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE processed_feed_items SET composite_score=?, reading_priority=?",
                     (score, priority))
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM processed_feed_items").fetchone())
    before = deepcopy(row)
    cand = candidate.make_candidate(row)
    assert cand["composite_score"] == (0.0 if score is None else float(score))
    assert "reading_priority" not in cand
    evidence = cand["selection_evidence"]
    assert evidence["stored"] == {
        "composite_recorded": score is not None, "reading_priority": known,
    }
    assert evidence["review"] == {
        "join": "not_observed", "join_basis": None, "quality_origin": "none",
        "needs_pdf": None, "digest_usable": None,
    }
    assert evidence["rank"]["basis"] == evidence["selection"]["basis"] == "not_observed"
    assert evidence.get("mode", "not_observed") == "not_observed"
    assert row == before


@pytest.mark.parametrize("raw,quality", [
    (None, {}), ("", {}), ("{}", {}), (" { } ", {}),
    ('{"grade":"A"}', {"grade": "A"}), ('{"grade":""}', {"grade": ""}),
])
def test_parsed_quality_truthiness_uses_one_existing_parse(source_row, monkeypatch, raw, quality):
    _, row = source_row
    row["quality_review_json"] = raw
    before, parsed = deepcopy(row), []
    original = json.loads

    def loads(text, *args, **kwargs):
        parsed.append(text)
        return original(text, *args, **kwargs)

    monkeypatch.setattr(json, "loads", loads)
    cand = candidate.make_candidate(row)
    expected_parses = [row["shap_contribs_json"]]
    if raw and raw.strip():
        expected_parses.append(raw.strip())
    assert parsed == expected_parses
    assert cand["quality"] == quality
    assert cand["selection_evidence"]["review"]["quality_origin"] == (
        "legacy_row" if quality else "none"
    )
    assert row == before


@pytest.mark.parametrize("field,value", [
    ("shap_contribs_json", "broken-json"),
    ("shap_contribs_json", "[]"),
    ("shap_contribs_json", '{"summary":[]}'),
    ("shap_contribs_json", '{"aux_context":[]}'),
    ("quality_review_json", "broken-json"),
    ("quality_review_json", "[]"),
])
def test_source_integrity_failures_stay_loud(source_row, field, value):
    _, row = source_row
    row[field] = value
    before = deepcopy(row)
    with pytest.raises(ValueError):
        candidate.make_candidate(row)
    assert row == before


def test_source_and_review_projection_do_not_parse_read_or_score(source_row, monkeypatch):
    _, row = source_row
    row.update(shap_contribs_json="not-json", quality_review_json="not-json",
               model_version="synthetic-nonidentity-calibrator")
    entry = {"needs_pdf": False, "digest": {"tldr": "Synthetic body"},
             "quality": {"grade": "A"}, "error": "not a status reader"}
    before = deepcopy((row, entry))

    def forbidden(*args, **kwargs):
        pytest.fail("pure observation projection attempted parsing, I/O, policy or scoring")

    monkeypatch.setattr(json, "loads", forbidden)
    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(library, "deep_review", SimpleNamespace(current_reviews=forbidden),
                        raising=False)
    for module, name in [
        (daily_select, "rank_quality_first_enabled"), (daily_select, "rank_interleave_enabled"),
        (allocator, "band_primary_enabled"), (candidate, "blend_scores"),
        (candidate, "quality_first_key"), (candidate, "unified_quality"),
    ]:
        monkeypatch.setattr(module, name, forbidden)
    evidence = source_observations(row)
    assert evidence["review"]["quality_origin"] == "none"
    observed = review_observations(evidence, join_basis="materialized", entry=entry,
                                   digest_usable=False)
    assert observed["review"] == {
        "join": "matched", "join_basis": "materialized", "quality_origin": "current_entry",
        "needs_pdf": False, "digest_usable": False,
    }
    assert evidence["review"]["join"] == "not_observed"
    assert set(observed) <= {"mode", "stored", "review", "rank", "selection"}
    assert observed.get("mode", "not_observed") == "not_observed"
    assert (row, entry) == before


@pytest.mark.parametrize("materialized,stable,basis,quality", [
    (None, None, None, {"grade": "A"}),
    ({}, None, None, {"grade": "A"}),
    (None, {}, "stable_feed", {}),
    ({}, {}, "stable_feed", {}),
    (None, {"quality": {"grade": "C"}}, "stable_feed", {"grade": "C"}),
    ({}, {"quality": {"grade": "C"}}, "stable_feed", {"grade": "C"}),
    ({"quality": {"grade": "B"}}, None, "materialized", {"grade": "B"}),
    ({"quality": {"grade": "B"}}, {}, "materialized", {"grade": "B"}),
    ({"quality": {"grade": "B"}}, {"quality": {"grade": "C"}}, "materialized", {"grade": "B"}),
])
def test_chosen_join_preserves_or_then_is_none_semantics(
    source_row, monkeypatch, materialized, stable, basis, quality,
):
    _, row = source_row
    row.update(materialized_zotero_key=" LIBRARY1 ", stable_feed_key=" feed:fixture ",
               quality_review_json='{"grade":"A"}')
    cand = candidate.make_candidate(row)
    expected_old = deepcopy(_old_candidate(cand))
    reviews = _Reviews({"LIBRARY1": materialized, "feed:fixture": stable})
    before, usability_calls = deepcopy(dict(reviews)), []
    original = review_eligibility.usable_review

    def usable(entry):
        usability_calls.append(entry)
        return original(entry)

    monkeypatch.setattr(review_eligibility, "usable_review", usable)
    matched = _join_snapshot(monkeypatch, [cand], reviews)
    assert matched == int(basis is not None)
    assert reviews.gets == (["LIBRARY1"] if materialized else ["LIBRARY1", "feed:fixture"])
    assert len(usability_calls) == matched
    if basis is not None:
        assert usability_calls[0] is (materialized if basis == "materialized" else stable)
        expected_old.update(quality=quality, review_ready=False)
    assert _old_candidate(cand) == expected_old
    assert cand["selection_evidence"]["review"] == {
        "join": "matched" if matched else "unavailable", "join_basis": basis,
        "quality_origin": "current_entry" if matched else "legacy_row",
        "needs_pdf": None, "digest_usable": False if matched else None,
    }
    assert dict(reviews) == before


def test_one_cohort_snapshot_marks_keyless_unavailable_without_extra_lookup(source_row, monkeypatch):
    _, row = source_row
    cohort = [candidate.make_candidate({**row, "id": number, "guid": str(number)})
              for number in (1, 2, 3)]
    cohort[1]["stable_feed_key"] = " feed:fixture "
    cohort[2].update(materialized_zotero_key=" LIBRARY1 ", stable_feed_key="feed:fixture")
    reviews = _Reviews({"LIBRARY1": {"quality": {"grade": "B"}},
                        "feed:fixture": {"quality": {"grade": "C"}}})
    assert _join_snapshot(monkeypatch, cohort, reviews) == 2
    assert reviews.gets == ["feed:fixture", "LIBRARY1"]
    assert cohort[0]["selection_evidence"]["review"] == {
        "join": "unavailable", "join_basis": None, "quality_origin": "none",
        "needs_pdf": None, "digest_usable": None,
    }
    assert [c["quality"] for c in cohort] == [{}, {"grade": "C"}, {"grade": "B"}]


@pytest.mark.parametrize("entry,needs_pdf,usable", [
    ({}, None, False),
    ({"quality": {"grade": "A"}}, None, False),
    ({"needs_pdf": True, "quality": {"grade": "A"}, "digest": {"tldr": "body"}}, True, False),
    ({"needs_pdf": False, "digest": {"tldr": "body"}}, False, True),
    ({"digest": {"key_findings": ["finding"]}}, None, True),
    ({"error": "synthetic failure", "quality": {"grade": "C"},
      "needs_pdf": False, "digest": {"tldr": "body"}}, False, False),
    ({"needs_pdf": None, "digest": {"tldr": "body"}}, None, True),
    ({"needs_pdf": 0, "digest": {"tldr": "body"}}, None, True),
    ({"needs_pdf": 1, "digest": {"tldr": "body"}}, None, False),
    ({"needs_pdf": "false", "digest": {"tldr": "body"}}, None, False),
    ({"needs_pdf": "", "digest": {"tldr": "body"}}, None, True),
])
def test_current_source_flag_digest_usability_and_quality_origin_are_separate(
    source_row, monkeypatch, entry, needs_pdf, usable,
):
    _, row = source_row
    row.update(stable_feed_key="feed:fixture", quality_review_json='{"grade":"A"}')
    cand = candidate.make_candidate(row)
    before = deepcopy(entry)
    assert _join_snapshot(monkeypatch, [cand], {"feed:fixture": entry}) == 1
    assert cand["quality"] == (entry.get("quality") or {})
    assert cand["review_ready"] is usable
    assert cand["selection_evidence"]["review"] == {
        "join": "matched", "join_basis": "stable_feed", "quality_origin": "current_entry",
        "needs_pdf": needs_pdf, "digest_usable": usable,
    }
    assert entry == before


@pytest.mark.parametrize("quality_first", [False, True])
def test_cohort_evidence_captures_the_one_actual_key_vector(source_row, monkeypatch, quality_first):
    _, row = source_row
    cohort = []
    for number, score, goals, citation, grade, alignment in [
        (1, 4.8, {"g1": 0.1, "g2": 0.8}, 0.1, "B", 1.0),
        (2, 4.1, None, None, "C", 5.0),
        (3, 0.0, {"g1": 0.2}, 0.9, "A", None),
    ]:
        payload = {"aux_context": {"goal_sims": goals, "citation_percentile": citation},
                   "summary": {"prestige_score": 5.0,
                               "triage_dimensions": {"goal_alignment": alignment}}}
        cohort.append(candidate.make_candidate({
            **row, "id": number, "guid": str(number), "composite_score": score,
            "quality_review_json": json.dumps({"grade": grade}),
            "shap_contribs_json": json.dumps(payload),
        }))
    before = deepcopy([_old_candidate(c) for c in cohort])
    name = "quality_first_key" if quality_first else "blend_scores"
    original, calculations = getattr(candidate, name), []

    def calculate(*args, **kwargs):
        keys = original(*args, **kwargs)
        calculations.append((args, list(keys)))
        return keys

    monkeypatch.setattr(candidate, name, calculate)
    candidate.attach_rank_scores(cohort, quality_first=quality_first)
    assert len(calculations) == 1
    args, keys = calculations[0]
    assert args[0] == ([0.75, 0.4, 1.0] if quality_first else [4.8, 4.1, 0.0])
    assert args[1] == [0.8, None, 0.2]
    assert args[2] == ([1.0, 5.0, None] if quality_first else [0.1, None, 0.9])
    for cand, old, key in zip(cohort, before, keys):
        old["rank_score"] = key
        assert _old_candidate(cand) == old
        evidence = cand["selection_evidence"]
        assert set(evidence) == {"mode", "stored", "review", "rank", "selection"}
        assert evidence["mode"] == ("quality_first" if quality_first else "control")
        assert evidence["rank"] == {
            "basis": "quality_first" if quality_first else "relevance_goal_citation", "key": key,
        }
        assert evidence["selection"]["basis"] == "not_observed"


@pytest.mark.parametrize("key", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_observation_does_not_reject_or_rewrite_operational_key(source_row, monkeypatch, key):
    _, row = source_row
    cand = candidate.make_candidate(row)
    monkeypatch.setattr(candidate, "blend_scores", lambda *args, **kwargs: [key])
    candidate.attach_rank_scores([cand])
    assert cand["rank_score"] is key and not math.isfinite(cand["rank_score"])
    assert cand["selection_evidence"]["rank"] == {"basis": "relevance_goal_citation", "key": None}
    json.dumps(cand["selection_evidence"], allow_nan=False)


@pytest.mark.parametrize("pinned", [None, False, True])
def test_assembly_and_api_use_the_same_post_review_mode_and_preserve_old_payload(
    source_row, monkeypatch, pinned,
):
    db, _ = source_row
    events = []
    resolved = True if pinned is None else pinned
    rank, allocate = daily_select.attach_rank_scores, daily_select.allocate

    def current_reviews():
        events.append("reviews")
        return {}

    def policy():
        assert events == ["reviews"]
        events.append("policy")
        return True

    def ranked(cohort, *, quality_first):
        events.append(("rank", quality_first))
        return rank(cohort, quality_first=quality_first)

    def why(cohort):
        events.append("why")
        for cand in cohort:
            cand["why"] = ["fixture why"]

    def allocated(**kwargs):
        events.append(("allocate", kwargs["quality_first"]))
        return allocate(**kwargs)

    monkeypatch.setattr(library, "deep_review", SimpleNamespace(current_reviews=current_reviews),
                        raising=False)
    monkeypatch.setattr(daily_select, "rank_interleave_enabled", lambda: False)
    monkeypatch.setattr(daily_select, "rank_quality_first_enabled", policy)
    monkeypatch.setattr(allocator, "band_primary_enabled", lambda: False)
    monkeypatch.setattr(daily_select, "attach_rank_scores", ranked)
    monkeypatch.setattr(daily_select, "attach_why", why)
    monkeypatch.setattr(daily_select, "allocate", allocated)
    slate = daily_select.assemble_daily_slate(
        db_path=db, K=1, roles={"model": 1}, now=_DEFAULT_NOW, quality_first=pinned,
    )
    assert events == (["reviews", "policy"] if pinned is None else ["reviews"]) + [
        ("rank", resolved), "why", ("allocate", resolved),
    ]
    mode = "quality_first" if resolved else "control"
    payload = _slate_to_payload(slate)
    assert slate.selection_policy == payload["selection_policy"] == mode
    assert payload["papers"] == [asdict(slate.papers[0])]
    assert payload["papers"][0]["selection_evidence"]["mode"] == mode
    legacy = deepcopy(payload)
    legacy.pop("selection_policy")
    legacy["papers"][0].pop("selection_evidence")
    expected_paper = asdict(_paper(why=["fixture why"]))
    expected_paper.pop("selection_evidence")
    assert legacy == {
        "papers": [expected_paper], "pool_size": 1, "lookback_hours": 168,
        "empty_role_events": [], "fellback_to_recent": False,
        "low_relevance_hidden": 0, "weak_slate": True,
    }
