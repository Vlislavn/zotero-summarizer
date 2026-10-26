"""Source-bound projection contracts, not a semantic usefulness benchmark."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.test_research_feed import _review, _seed, _settings
from tests.test_research_feed_cursor import environment, run, stage_calls, state_path
from zotero_summarizer.models import ResearchCandidate, ResearchFeedTriage, ResearchProfile
from zotero_summarizer.services.research_feed import card, runner


def _inputs():
    return (
        ResearchCandidate(source_id="paper", source="app_rss", title="Agent harness benchmark",
                          abstract="Clinical agents, activation interpretability, evaluation and planning."),
        ResearchFeedTriage(include=True, score=5, confidence=0.9, rationale="Topical lure",
                           matched_projects=["agent harness", "activation oracle", "biomedical agents"],
                           contribution_kind=["benchmark"]),
        ResearchProfile(themes=["evaluation"], projects=["agent harness", "activation oracle", "biomedical agents"]),
    )


def test_keyword_lure_and_implementation_prose_do_not_produce_project_fit_or_experiment():
    candidate, triage, profile = _inputs()
    review = _review("paper")
    before = deepcopy(review)

    projected = card.build_card(candidate, triage, review, profile)

    assert projected.project_uses == []  # Unassessed, not the negative not_applicable verdict.
    assert projected.research_ideas == []
    assert projected.project_assessment_status == "not_assessed"
    assert projected.project_assessments == [] and projected.assessment_evidence == {}
    assert projected.project_use_notes == ["Project mechanisms and validation experiments have not been assessed."]
    assert projected.worth_reading == "skim" and projected.code_urls == ["https://github.com/example/paper"]
    assert review == before


def test_exact_project_goal_evidence_is_preserved_without_upgrading_to_project_use():
    candidate, triage, profile = _inputs()
    review = _review("paper", goal_summaries=[{
        "goal": "agent harness", "retrieval_state": "hit", "relevant": True, "abstained": False,
        "summary": "The paper records failed tool trajectories.",
        "supporting_quotes": ["We retain unsuccessful tool actions."],
    }])
    before = deepcopy(review)

    projected = card.build_card(candidate, triage, review, profile)

    assert projected.project_uses == [] and projected.research_ideas == []
    assert projected.project_assessment_status == "not_assessed"
    assert projected.project_assessments == [] and projected.assessment_origin is None
    assert projected.project_use_notes == [
        "Project mechanisms and validation experiments have not been assessed.",
        "Retained goal observation (unverified) — agent harness: The paper records failed tool trajectories.",
    ]
    assert review == before


@pytest.mark.parametrize("change", ["undeclared", "not_retrieved", "abstained", "no_quotes", "false_as_zero"])
def test_degraded_or_unbound_goal_evidence_is_not_project_evidence(change):
    candidate, triage, profile = _inputs()
    goal = {"goal": "agent harness", "retrieval_state": "hit", "relevant": True,
            "abstained": False, "summary": "Retain failed actions.", "supporting_quotes": ["Failed actions."]}
    if change == "undeclared":
        goal["goal"] = "unconfigured project"
    elif change == "not_retrieved":
        goal["retrieval_state"] = "not_retrieved"
    elif change == "abstained":
        goal["abstained"] = True
    elif change == "no_quotes":
        goal["supporting_quotes"] = []
    else:
        goal["abstained"] = 0
    review = _review("paper", goal_summaries=[goal])

    projected = card.build_card(candidate, triage, review, profile)

    assert projected.project_assessment_status == "not_assessed"
    assert projected.project_use_notes == ["Project mechanisms and validation experiments have not been assessed."]
    assert projected.project_uses == [] and projected.research_ideas == []


@pytest.mark.parametrize("field,value", [
    ("summary", None), ("summary", 3), ("summary", []), ("summary", ""), ("summary", " "),
    ("supporting_quotes", None), ("supporting_quotes", "literal"),
    ("supporting_quotes", []), ("supporting_quotes", [" "]),
    ("supporting_quotes", [None]), ("supporting_quotes", ["Failed actions.", False]),
    ("relevant", 1), ("abstained", 0), ("retrieval_state", "miss"),
    ("goal", "AGENT HARNESS"), ("goal", "agent harness "),
])
def test_malformed_or_nonexact_goal_observations_cannot_be_project_notes(field, value):
    candidate, triage, profile = _inputs()
    goal = {"goal": "agent harness", "retrieval_state": "hit", "relevant": True,
            "abstained": False, "summary": "Retain failed actions.", "supporting_quotes": ["Failed actions."]}
    goal[field] = value
    review = _review("paper", goal_summaries=[goal])
    before = deepcopy(review)

    projected = card.build_card(candidate, triage, review, profile)

    assert projected.project_use_notes == ["Project mechanisms and validation experiments have not been assessed."]
    assert projected.project_assessment_status == "not_assessed"
    assert projected.project_uses == projected.research_ideas == projected.project_assessments == []
    assert review == before


@pytest.mark.parametrize("goals", [None, {}, "not a board", 0, False, ()])
def test_nonlist_goal_boards_remain_unassessed_without_mutation(goals):
    candidate, triage, profile = _inputs()
    review = _review("paper", goal_summaries=goals)
    before = deepcopy(review)

    projected = card.build_card(candidate, triage, review, profile)

    assert projected.project_use_notes == ["Project mechanisms and validation experiments have not been assessed."]
    assert projected.project_assessment_status == "not_assessed"
    assert review == before


def test_goal_notes_preserve_exact_configured_keys_text_order_and_duplicates():
    candidate, triage, profile = _inputs()
    profile.projects = [" 生物 Δ ", "agent harness"]
    first = {"goal": " 生物 Δ ", "retrieval_state": "hit", "relevant": True,
             "abstained": False, "summary": "  原文 <keep> & compare.\nSecond line.  ",
             "supporting_quotes": [" 原文 & compare. "]}
    second = {**first, "goal": "agent harness", "summary": "Retained second observation."}
    review = _review("paper", goal_summaries=[first, None, {**first, "goal": "生物 Δ"}, second, deepcopy(first)])
    before, profile_before = deepcopy(review), profile.model_dump(mode="json")

    projected = card.build_card(candidate, triage, review, profile)

    expected = [f"Retained goal observation (unverified) — {row['goal']}: {row['summary']}"
                for row in (first, second, first)]
    assert projected.project_use_notes == ["Project mechanisms and validation experiments have not been assessed.", *expected]
    assert projected.project_uses == projected.research_ideas == projected.project_assessments == []
    assert projected.project_assessment_status == "not_assessed"
    assert projected.assessment_evidence == {} and projected.scientific_verification is None
    assert review == before and profile.model_dump(mode="json") == profile_before


def test_weekly_persists_goal_observation_once_without_project_proposal_or_model_work(tmp_path, monkeypatch):
    from zotero_summarizer.services.llm import factory
    from zotero_summarizer.services.research_feed.profile import profile_path
    from tests.test_research_feed_cursor import START, END

    candidate, triage, profile = _inputs()
    settings = _settings(tmp_path)
    _seed(settings, "paper", "Agent harness benchmark")
    profile_path(settings.data_dir).parent.mkdir(parents=True, exist_ok=True)
    profile_path(settings.data_dir).write_text(profile.model_dump_json(), encoding="utf-8")
    review = _review("paper", goal_summaries=[{"goal": "agent harness", "retrieval_state": "hit", "relevant": True,
        "abstained": False, "summary": "Retain failed actions. 原文", "supporting_quotes": ["Failed actions."]}])
    before = deepcopy(review)
    client = Mock(side_effect=AssertionError("qualified retained projection must not start a model"))
    monkeypatch.setattr(factory, "resolve_stage_client", client)
    monkeypatch.setattr(factory, "build_client_for_provider", client)

    result = runner.run_weekly(settings, start=START, end=END, review_loader=lambda key: deepcopy(review))
    payload = json.loads(Path(result["json_path"]).read_text())
    projected = payload["cards"][0]["card"]
    note = "Retained goal observation (unverified) — agent harness: Retain failed actions. 原文"

    assert projected["project_use_notes"] == ["Project mechanisms and validation experiments have not been assessed.", note]
    assert projected["project_assessment_status"] == "not_assessed"
    assert projected["project_uses"] == projected["research_ideas"] == []
    assert payload["research_ideas_by_project"] == {}
    assert Path(result["markdown_path"]).read_text().count(note) == 1
    assert result["failed"] == 0 and review == before
    client.assert_not_called()


@pytest.mark.parametrize("basis", [None, "abstract", "unknown", "full_text"])
@pytest.mark.parametrize("binding", [None, {}, {"fixture": "paper"},
    {"source_kind": "none", "source_path": "", "source_sha256": "a" * 64, "generation_sha256": "b" * 64}])
def test_digest_basis_and_truthy_but_unbound_review_never_authorize_cards(basis, binding):
    candidate, triage, profile = _inputs()
    review = _review("paper")
    review["review_identity"] = binding
    review["digest"]["basis"] = basis

    records, missing, failed, unqualified, evidence = runner._records(
        [(candidate, triage)], lambda _key: review, profile,
    )

    assert records == missing == failed == []
    assert unqualified[0]["candidate"] == candidate.model_dump(mode="json")
    assert evidence["paper"]["availability"] == "unknown"
    with pytest.raises(ValueError):
        card.build_card(candidate, triage, review, profile)


@pytest.mark.parametrize(("field", "value", "available"), [
    ("source_sha256", "missing", False), ("source_sha256", True, False),
    ("source_path", None, False), ("source_kind", "invented", False),
    ("generation_sha256", None, True),
])
def test_invalid_current_review_binding_fields_cannot_authorize_projection(field, value, available):
    candidate, triage, profile = _inputs()
    review = _review("paper")
    review["review_identity"][field] = value

    records, missing, failed, unqualified, evidence = runner._records(
        [(candidate, triage)], lambda _key: review, profile,
    )

    assert records == missing == failed == []
    assert len(unqualified) == 1
    assert evidence["paper"]["availability"] == ("available" if available else "unknown")


@pytest.mark.parametrize("basis", [None, "abstract", "unknown"])
def test_available_bound_source_without_explicit_full_text_digest_remains_unqualified(basis):
    candidate, triage, profile = _inputs()
    review = _review("paper")
    review["digest"]["basis"] = basis

    records, missing, failed, unqualified, evidence = runner._records(
        [(candidate, triage)], lambda _key: review, profile,
    )

    assert records == missing == failed == []
    assert evidence["paper"]["availability"] == "available"
    assert unqualified[0]["reason"] == "digest_basis_unqualified"


def test_absent_review_and_provider_failure_are_not_acquisition_unavailable():
    candidate, triage, profile = _inputs()
    for outcomes in ({}, {"paper": "provider unavailable"}):
        records, missing, failed, unqualified, evidence = runner._records(
            [(candidate, triage)], lambda _key: None, profile, outcomes,
        )
        assert records == missing == []
        assert evidence["paper"]["availability"] == "unknown"
        assert unqualified[0] == {"candidate": candidate.model_dump(mode="json"), "reason": "review_missing"}
        assert failed == ([{"source_id": "paper", "error": "provider unavailable"}] if outcomes else [])


@pytest.mark.parametrize("outcome", [None, "offline_uncached", "needs_login", "fetch_failed", "no_oa_source"])
def test_only_structured_acquisition_unavailable_outcome_authorizes_manual_list(outcome):
    candidate, triage, profile = _inputs()
    review = {"needs_pdf": True, "digest": None, "acquire_outcome": outcome}

    records, missing, failed, unqualified, evidence = runner._records(
        [(candidate, triage)], lambda _key: review, profile,
    )

    assert records == failed == []
    assert missing == ([candidate.model_dump(mode="json")] if outcome is not None else [])
    assert evidence["paper"]["availability"] == ("unavailable" if outcome is not None else "unknown")
    assert len(unqualified) == int(outcome is None)


@pytest.mark.parametrize("change", ["source", "generation", "contract", "basis", "placeholder"])
def test_direct_card_projection_requires_base_review_qualification(change):
    candidate, triage, profile = _inputs()
    review = _review("paper")
    if change == "source":
        review["review_identity"]["source_sha256"] = "missing"
    elif change == "generation":
        review["review_identity"]["generation_sha256"] = None
    elif change == "contract":
        review["review_contract_version"] = -1
    elif change == "basis":
        review["digest"]["basis"] = "abstract"
    else:
        review["needs_pdf"] = True
        review["acquire_outcome"] = "no_oa_source"
    before = deepcopy(review)

    with pytest.raises(ValueError):
        card.build_card(candidate, triage, review, profile)

    assert review == before


@pytest.mark.parametrize("outcome", [None, "", "browser_not_attempted", "browser_extra_unavailable",
    "acquired_arxiv", "invented", False, 0, {}, []])
def test_unobserved_or_incompatible_acquisition_codes_do_not_claim_source_unavailable(outcome):
    candidate, triage, profile = _inputs()
    review = {"needs_pdf": True, "digest": None, "acquire_outcome": outcome}

    records, missing, failed, unqualified, evidence = runner._records(
        [(candidate, triage)], lambda _key: review, profile,
    )

    assert records == missing == failed == []
    assert unqualified == [{"candidate": candidate.model_dump(mode="json"), "reason": "acquisition_not_observed"}]
    assert evidence["paper"]["availability"] == "unknown" and evidence["paper"]["qualified"] is False


@pytest.mark.parametrize("flag", [0, 1, "true", [], {}])
def test_malformed_review_source_status_fails_per_paper_instead_of_coercing_availability(flag):
    candidate, triage, profile = _inputs()
    review = {"needs_pdf": flag, "acquire_outcome": "no_oa_source", "digest": None}

    records, missing, failed, unqualified, evidence = runner._records(
        [(candidate, triage)], lambda _key: review, profile,
    )

    assert records == missing == unqualified == []
    assert failed == [{"source_id": "paper", "error": "TypeError: review needs_pdf must be a boolean"}]
    assert evidence["paper"] == {"availability": "unknown", "qualified": False}


def test_qualified_projection_keeps_assessment_confidence_unknown_and_legacy_triage_confidence():
    candidate, triage, profile = _inputs()
    review = _review("paper")
    review.pop("provenance")
    review.pop("reviewed_at")

    records, missing, failed, unqualified, evidence = runner._records(
        [(candidate, triage)], lambda _key: review, profile,
    )

    assert missing == failed == unqualified == []
    provenance = records[0]["provenance"]
    for field in ("provider", "model", "prompt_schema_version", "generated_at", "assessment_confidence"):
        assert provenance[field] is None
    assert provenance["confidence"] == triage.confidence
    assert provenance["triage"]["confidence"] == triage.confidence
    assert provenance["source_id"] == candidate.source_id
    assert provenance["source_binding"] == review["review_identity"]
    assert provenance["basis"] == "full_text" and provenance["abstained"] is True
    assert provenance["project_use_assessment"] == "not_assessed"
    assert evidence["paper"]["qualified"] is True


def test_bound_availability_survives_projection_failure_without_card_backfill(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _seed(settings, "paper", "Agent harness benchmark")
    monkeypatch.setattr(runner, "build_card", Mock(side_effect=ValueError("owned projection failure")))
    from tests.test_research_feed_cursor import START, END

    retained = _review('paper')
    result = runner.run_weekly(settings, start=START, end=END, review_loader=lambda _key: deepcopy(retained))
    payload = json.loads(Path(result["json_path"]).read_text())

    assert result["full_text_available"] == 1 and result["cards_generated"] == 0
    assert result["failed"] == 1
    observed = payload["metadata"]["full_text_evidence"]["paper"]
    assert observed["availability"] == "available"
    assert observed['source_binding'] == {key: retained['review_identity'][key]
                                           for key in ('source_kind', 'source_sha256', 'generation_sha256')}
    assert 'source_path' not in observed['source_binding'] and 'focus_prompt' not in observed['source_binding']
    assert payload["manual_full_text_required"] == []
    assert payload["metadata"]["source_history"]["paper"]["candidate"]["source_id"] == "paper"


def test_missing_review_retains_one_candidate_home_reason_and_no_new_work(environment, monkeypatch):
    settings, _reviews = environment
    _seed(settings, "paper", "Agent harness benchmark")
    start = Mock(side_effect=AssertionError("cached projection must not acquire"))
    from zotero_summarizer.services.library import deep_review
    monkeypatch.setattr(deep_review, "start", start)

    payload, result = run(settings)

    assert payload["manual_full_text_required"] == []
    assert payload["unqualified_reviews"][0]["reason"] == "review_missing"
    candidate = payload["unqualified_reviews"][0]["candidate"]
    assert candidate["source_id"] == "paper" and candidate["abstract"] == "agent harness benchmark"
    history = payload["metadata"]["source_history"]["paper"]
    assert history["candidate_ref"] == "/unqualified_reviews/0/candidate" and "candidate" not in history
    assert result["full_text_available"] == result["cards_generated"] == 0
    assert "Review qualification pending" in Path(result["markdown_path"]).read_text()
    start.assert_not_called()


def test_cached_legacy_card_cannot_replay_even_with_valid_current_review_and_matching_checksum(environment):
    settings, reviews = environment
    _seed(settings, "paper", "Agent harness benchmark")
    reviews["paper"] = _review("paper")
    payload, result = run(settings)
    path = Path(result["json_path"])
    payload["cards"][0]["provenance"].pop("projection_contract", None)
    payload["cards"][0]["card"]["project_uses"] = ["integrate_into_harness"]
    path.write_text(json.dumps(payload))
    old_bytes = path.read_bytes()

    with stage_calls() as observed:
        rebuilt, repeated = run(settings)

    assert observed["card"] == 1
    assert "candidate_cursor" not in rebuilt["metadata"]
    assert "candidate_cursor" not in json.loads(state_path(settings).read_text())
    assert repeated["json_path"] != result["json_path"]
    assert rebuilt["cards"][0]["card"]["project_uses"] == []
    assert path.read_bytes() == old_bytes


def test_current_review_loses_binding_and_cannot_resurrect_a_previous_card(environment):
    settings, reviews = environment
    _seed(settings, "paper", "Agent harness benchmark")
    reviews["paper"] = _review("paper")
    run(settings)
    reviews["paper"]["review_identity"] = {"fixture": "paper"}

    with stage_calls() as observed:
        payload, result = run(settings)

    assert payload["cards"] == payload["manual_full_text_required"] == []
    assert payload["unqualified_reviews"][0]["reason"] == "review_source_unbound"
    assert result["full_text_available"] == 0 and observed["card"] == 0
    assert "candidate_cursor" not in payload["metadata"]
    state = json.loads(state_path(settings).read_text())
    assert "candidate_cursor" not in state
