"""Synthetic machine-contract regressions, not measured model answerability."""
import hashlib
import json
from html import unescape
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.api.routes import search
from tests._search_evidence_fixtures import QUESTION, QUOTE, journey, run, stored_session  # noqa: F401
from zotero_summarizer.services.search import _question_evidence as evidence
from zotero_summarizer.services.search import pipeline, session
from zotero_summarizer.services.search._models import Candidate, ScreenRequest, SearchIntent
from zotero_summarizer.services.search._question_policy import apply_question_policy

def test_opt_in_promotes_eleventh_before_both_cutoffs_and_persists(journey):
    result = run(journey)

    assert result.candidates[0].candidate_id == "P11"
    assert "P11" in journey.light and "P11" in journey.deep
    assert len(journey.generator.calls) == len(journey.verifier.calls) == 12
    assert journey.acquired.count("P11") == 1
    row = result.candidates[0].question_evidence[0]
    text = "P11: original text\n" + QUOTE
    assert row["state"] == "sufficient" and row["complete"] is False
    assert row["source_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert row["question_id"] == hashlib.sha256(QUESTION.encode()).hexdigest()
    span, = row["evidence"]
    assert text[span["start"]:span["end"]] == span["quote"] == QUOTE
    assert row["capabilities"] == {"dense": True, "bm25": True, "reranker": False,
                                    "embedder_model": "synthetic", "reranker_model": "synthetic"}
    assert result.evidence_status["promoted_candidate_ids"] == ["P11"]
    assert result.evidence_status["calls_used"] == 24
    assert session.load(result.id).to_dict() == result.to_dict()
    assert next(c for c in result.candidates if c.candidate_id == "P13").question_evidence[0]["state"] == "unknown"


@pytest.mark.parametrize("mode", ["baseline", "no_questions", "same_route", "disabled_budget", "invalid_budget"])
def test_disabled_or_unavailable_preserves_baseline_and_extra_work_is_zero(journey, monkeypatch, mode):
    if mode == "same_route":
        journey.deps.config.llm_routing.feed.model = journey.deps.config.llm_routing.deep_review.model
    if mode in {"disabled_budget", "invalid_budget"}:
        monkeypatch.setenv("ZS_SEARCH_EVIDENCE_CALLS", "0" if mode == "disabled_budget" else "-1")
    result = run(journey, policy="baseline" if mode == "baseline" else "question_evidence",
                 questions=[] if mode == "no_questions" else None)

    assert [c.candidate_id for c in result.candidates] == [f"P{n:02}" for n in range(1, 14)]
    assert not journey.generator.calls and not journey.verifier.calls
    assert len(journey.acquired) == 10
    if mode != "baseline":
        assert result.evidence_status["state"] == "unavailable"


@pytest.mark.parametrize("mode", ["missing", "oversized", "retrieval_error", "generator_error", "verifier_error"])
def test_operational_failures_are_unknown_not_negative_and_keep_baseline(journey, monkeypatch, mode):
    if mode in {"missing", "oversized"}:
        monkeypatch.setattr(evidence, "acquire_full_text", lambda *a, **kw: "" if mode == "missing" else "x" * 60001)
    if mode == "retrieval_error":
        monkeypatch.setattr(evidence, "retrieve_original_passages", Mock(side_effect=RuntimeError("synthetic")))
    journey.generator.fail = mode == "generator_error"
    journey.verifier.fail = mode == "verifier_error"
    result = run(journey)

    assert all(c.question_evidence[0]["state"] == "unknown" for c in result.candidates)
    assert result.evidence_status["promoted_candidate_ids"] == []
    assert [c.candidate_id for c in result.candidates] == [f"P{n:02}" for n in range(1, 14)]
    assert "private synthetic" not in json.dumps(result.to_dict())


@pytest.mark.parametrize("state", ["partial", "not_found_in_checked_text", "unknown"])
def test_only_sufficient_promotes_and_missing_is_scoped_to_checked_text(journey, state):
    journey.verifier.state = state
    result = run(journey)
    row = result.candidates[0].question_evidence[0]
    assert row["state"] == state and row["complete"] is False
    assert row["checked_passages"]
    assert result.evidence_status["promoted_candidate_ids"] == []
    assert bool(row["evidence"]) == (state == "partial")
    assert bool(row["uncovered"]) == (state == "partial")


@pytest.mark.parametrize("output", [{"answer": None, "quote": None},
                                    {"answer": "Invented conclusion", "quote": "fabricated quote"}])
def test_abstention_and_ungrounded_generation_do_not_reach_verifier(journey, output):
    journey.generator.output = output
    result = run(journey)
    assert not journey.verifier.calls
    assert all(c.question_evidence[0]["state"] == "unknown" for c in result.candidates)


def test_call_budget_counts_attempts_and_reserves_verification(journey, monkeypatch):
    monkeypatch.setenv("ZS_SEARCH_EVIDENCE_CALLS", "3")
    result = run(journey)
    assert len(journey.generator.calls) == len(journey.verifier.calls) == 1
    assert result.evidence_status["calls_used"] == 2
    assert result.candidates[1].question_evidence[0]["reason"] == "call_budget_exhausted"
    assert result.evidence_status["promoted_candidate_ids"] == []


def test_delete_between_generation_and_verification_does_not_recreate_session(journey):
    journey.generator.on_call = lambda: session.delete("cancelled-evidence")
    run(journey, name="cancelled-evidence")
    assert len(journey.generator.calls) == 1 and not journey.verifier.calls
    with pytest.raises(FileNotFoundError):
        session.load("cancelled-evidence")


@pytest.mark.parametrize("mutation", ["question", "query", "source", "evidence", "retracted", "below_floor"])
def test_stale_or_ineligible_evidence_cannot_promote_or_escape_projection(journey, mutation):
    result = run(journey)
    candidate = result.candidates[0]
    if mutation == "question":
        result.questions = ["Different question"]
    elif mutation == "query":
        result.raw_query = "Different topic"
    elif mutation == "source":
        candidate.url = "https://example.org/another.pdf"
    elif mutation == "evidence":
        candidate.question_evidence[0]["evidence"][0]["start"] = -1
    elif mutation == "retracted":
        candidate.is_retracted = True
    else:
        candidate.query_score = 0.01
    pipeline.rank_candidates(result.candidates)
    apply_question_policy(result)
    assert result.candidates[0].candidate_id == "P01"
    assert result.evidence_status["promoted_candidate_ids"] == []
    if mutation in {"question", "query", "source", "evidence"}:
        projected = result.to_dict(for_display=True)
        target = next(c for c in projected["candidates"] if c["candidate_id"] == "P11")
        assert target["question_evidence"][0]["state"] == "unknown"
        assert "answer" not in target["question_evidence"][0]
        assert projected["evidence_status"]["state"] == "unavailable"


def test_merge_keeps_concurrent_explicit_add_and_family_enrichment_invalidates_evidence(journey):
    from zotero_summarizer.services.search.dedup import to_version_families

    result = run(journey)
    session.update(result.id, lambda s: setattr(s.candidates[0], "materialized_zotero_key", "SAVED"))
    session.save_merge(result)
    assert session.load(result.id).candidates[0].materialized_zotero_key == "SAVED"
    old = result.candidates[0]
    old.doi = "10.1000/example"
    richer = Candidate(title="Richer metadata", doi=old.doi, abstract="New abstract", url="https://example.org/new.pdf")
    merged, = to_version_families([old, richer])
    result.candidates = [merged]
    wire = result.to_dict(for_display=True)
    assert merged.candidate_id == "P11" and merged.materialized_zotero_key == "SAVED"
    assert wire["candidates"][0]["question_evidence"][0]["state"] == "unknown"


def test_family_metadata_enrichment_keeps_current_evidence_and_materialization(journey):
    from zotero_summarizer.services.search.dedup import to_version_families

    research = stored_session(name="family-evidence")
    research.candidates[10].doi = "10.1000/example"
    session.save(research)
    result = pipeline.run_review(research.id, deps=journey.deps)
    target = result.candidates[0]
    target.materialized_zotero_key = "SAVED"
    richer = Candidate(title="New metadata", doi=target.doi, abstract="Richer metadata")
    merged, = to_version_families([target, richer])
    assert merged.question_evidence == target.question_evidence
    assert merged.candidate_id == "P11" and merged.materialized_zotero_key == "SAVED"
    result.candidates = [merged] + [c for c in result.candidates if c.candidate_id != "P11"]
    apply_question_policy(result)
    assert result.evidence_status["promoted_candidate_ids"] == ["P11"]
    assert result.to_dict(for_display=True)["candidates"][0]["question_evidence"][0]["state"] == "sufficient"


def test_unique_original_quote_locations_preserve_second_occurrence_and_reject_ambiguity():
    text = QUOTE + "\nseparator\n" + QUOTE
    start = len(QUOTE) + len("\nseparator\n")
    located, = evidence._located_quote(QUOTE, [{"start": start, "text": text[start:]}])
    assert located["start"] == start and text[located["start"]:located["end"]] == QUOTE
    assert evidence._located_quote(QUOTE, [{"start": 0, "text": text}]) == []


def test_http_policy_survives_actual_screen_and_saved_read(journey, monkeypatch):
    monkeypatch.setattr(search, "default_deps", lambda: journey.deps)
    monkeypatch.setattr(search, "_kickoff_review", lambda _id: False)
    monkeypatch.setattr(pipeline, "parse_intent", lambda raw, qs, **kw: SearchIntent(raw_query=raw, questions=qs))
    monkeypatch.setattr(pipeline, "federate", lambda *a, **kw: [Candidate(title="Paper", query_score=0.5)])
    monkeypatch.setattr(pipeline, "score_query_relevance", lambda *a, **kw: None)
    app = FastAPI()
    app.include_router(search.router)
    with TestClient(app) as client:
        response = client.post("/api/search/screen", json={"query": "topic", "questions": [QUESTION],
                                                         "evidence_policy": "question_evidence"})
        assert response.status_code == 200
        result = response.json()
        assert result["evidence_policy"] == "question_evidence"
        assert client.get(f"/api/search/{result['id']}").json() == result
        assert client.post("/api/search/screen", json={"query": "topic", "evidence_policy": "automatic"}).status_code == 422
    assert session.load(result["id"]).evidence_policy == "question_evidence"
    assert ScreenRequest(query="topic").evidence_policy == "baseline"


def test_read_invalidates_old_binding_without_mutating_saved_bytes(journey, tmp_path):
    result = run(journey)
    path = tmp_path / "sessions" / f"{result.id}.json"
    raw = json.loads(path.read_text())
    raw["questions"] = ["New question"]
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    read = session.load(result.id)
    assert read.candidates[0].candidate_id == "P01"
    assert read.evidence_status["state"] == "unavailable"
    projected = read.to_dict(for_display=True)
    assert all(row["state"] == "unknown" for c in projected["candidates"] for row in c["question_evidence"])
    assert path.read_bytes() == before
    session.save_merge(read)
    retained = json.loads(path.read_text())
    old_receipt = next(c for c in retained["candidates"] if c["candidate_id"] == "P11")["question_evidence"][0]
    assert old_receipt["answer"] and old_receipt["question"] == QUESTION


@pytest.mark.parametrize("field", ["query", "questions"])
def test_invalid_utf8_input_is_rejected_before_any_provider(field):
    values = {"query": "topic", "questions": [QUESTION], "evidence_policy": "question_evidence"}
    values[field] = "\ud800" if field == "query" else ["\ud800"]
    with pytest.raises(ValueError):
        ScreenRequest(**values)


@pytest.mark.parametrize("duplicate", [True, False])
def test_promotion_counts_and_checks_distinct_questions_once(journey, monkeypatch, duplicate):
    questions = [QUESTION, QUESTION if duplicate else "Another question?", "Other question?"]
    research = stored_session(questions=questions, name="distinct-questions")
    research.candidates = [research.candidates[0], research.candidates[10]]

    def verify(*, prompt, pydantic_model):
        payload = json.loads(unescape(prompt.rsplit("<untrusted_input>", 1)[1].split("</untrusted_input>")[0]))
        paper = payload["original_passages"][0]["text"].split(":", 1)[0]
        supported = (paper == "P11" and payload["question"] != "Other question?"
                     or paper == "P01" and payload["question"] == "Other question?")
        return pydantic_model(state="sufficient" if supported else "unknown",
                              evidence_ids=[payload["evidence"][0]["passage_id"]] if supported else [], uncovered=[])

    monkeypatch.setattr(journey.verifier, "pydantic_prompt", verify)
    session.save(research)
    assessed = pipeline.run_review(research.id, deps=journey.deps)
    expected = ["P01", "P11"] if duplicate else ["P11", "P01"]
    assert [c.candidate_id for c in assessed.candidates] == expected
    assert len(journey.generator.calls) == (4 if duplicate else 6)
    assert assessed.evidence_status["calls_used"] == (8 if duplicate else 12)
    for candidate in assessed.candidates:
        assert [row["question_index"] for row in candidate.question_evidence] == [0, 1, 2]
        if duplicate:
            assert candidate.question_evidence[0]["state"] == candidate.question_evidence[1]["state"]


def test_promotion_preserves_ineligible_and_retracted_baseline_slots(journey, monkeypatch):
    monkeypatch.setenv("ZS_SEARCH_EVIDENCE_TOPIC_FLOOR", "0.37")
    research = stored_session(name="eligible-slots")
    research.candidates = [research.candidates[0], research.candidates[1], research.candidates[10],
                           research.candidates[12]]
    research.candidates[0].query_score = 0.9
    research.candidates[1].query_score = 0.369
    research.candidates[1].quality = {"quality_band": "highlight", "grade": "A"}
    research.candidates[2].query_score = 0.371
    research.candidates[3].is_retracted = True
    session.save(research)
    assessed = pipeline.run_review(research.id, deps=journey.deps)
    assert [c.candidate_id for c in assessed.candidates] == ["P11", "P02", "P01", "P13"]
    assert assessed.evidence_status["promoted_candidate_ids"] == ["P11"]


def test_over_budget_original_is_reused_by_existing_review(journey, monkeypatch):
    research = stored_session(name="over-budget-reuse")
    research.candidates = research.candidates[:1]
    journey.deps.max_chars = 10
    reacquire = Mock(side_effect=RuntimeError("second acquisition failed"))
    retrieve = Mock(side_effect=AssertionError("over-budget evidence cannot retrieve passages"))
    reviewed_text = []
    monkeypatch.setattr(pipeline, "acquire_full_text", reacquire)
    monkeypatch.setattr(evidence, "retrieve_original_passages", retrieve)
    monkeypatch.setattr(pipeline, "light_review", lambda c, **kw: reviewed_text.append(kw["full_text"]))
    session.save(research)
    assessed = pipeline.run_review(research.id, deps=journey.deps)
    reacquire.assert_not_called()
    retrieve.assert_not_called()
    assert len(reviewed_text) == 1 and len(reviewed_text[0]) > journey.deps.max_chars
    row = assessed.candidates[0].question_evidence[0]
    assert row["state"] == "unknown" and row["reason"] == "source_character_budget_exceeded"
    assert not journey.generator.calls and not journey.verifier.calls
    assert journey.deep == ["P01"]


@pytest.mark.parametrize('kind', ['surrogate', 'unicode', 'fabricated_quote'])
def test_answer_encoding_preserves_ordinary_review_and_valid_unicode(journey, kind):
    answer = journey.generator.output['answer']
    if kind == 'fabricated_quote':
        journey.generator.output['quote'] = 'This quote is absent from the source.'
    else:
        answer += '\ud800' if kind == 'surrogate' else ' 😀\u0301'
        journey.generator.output['answer'] = answer
    result = run(journey)
    if kind == 'unicode':
        assert result.candidates[0].candidate_id == 'P11'
        assert result.candidates[0].question_evidence[0]['answer'] == answer
        assert len(journey.generator.calls) == len(journey.verifier.calls) == 12
    else:
        assert [c.candidate_id for c in result.candidates] == [f'P{n:02}' for n in range(1, 14)]
        assert len(journey.generator.calls) == 12 and journey.verifier.calls == []
        assert all(c.question_evidence[0]['state'] == 'unknown' for c in result.candidates)
        assert all(c.question_evidence[0].get('answer') is None for c in result.candidates)
        assert result.evidence_status['promoted_candidate_ids'] == []
    assert len(journey.light) == pipeline.LIGHT_REVIEW_N and len(journey.deep) == pipeline.DEEP_SET_K
    assert session.load(result.id).to_dict() == result.to_dict()


def test_rejected_source_is_not_reused_even_when_over_budget(journey, monkeypatch):
    text = "<Error><Code>Denied</Code><Message>Not a paper</Message><RequestId>x</RequestId></Error>"
    journey.deps.max_chars = 10
    monkeypatch.setattr(evidence, "acquire_full_text", lambda *a, **kw: text)
    research = stored_session(name="rejected-source")
    fulltext = {}
    with pytest.raises(APIError):
        evidence._snapshot(research.candidates[0], research, journey.deps, fulltext)
    assert fulltext == {}
