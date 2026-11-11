"""Current opt-in Search owners with scripted sources, not model-quality claims."""
import hashlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests._search_evidence_fixtures import QUESTION, QUOTE, journey, run, stored_session  # noqa: F401
from zotero_summarizer.api.routes import search
from zotero_summarizer.services.search import _question_evidence as evidence
from zotero_summarizer.services.search import pipeline, session
from zotero_summarizer.services.search._models import Candidate, ScreenRequest, SearchIntent
from zotero_summarizer.services.search._question_policy import apply_question_policy


def test_real_opt_in_path_promotes_eleventh_before_both_cutoffs_and_persists(journey, monkeypatch):
    events = []
    verify = journey.verifier.pydantic_prompt
    light, select = pipeline.light_review, pipeline.select_deep_set

    def verified(**kwargs):
        reply = verify(**kwargs)
        events.append(("verify", journey.verifier.calls[-1]["original_passages"][0]["text"]))
        return reply

    def reviewed(candidate, **kwargs):
        events.append(("light", candidate.candidate_id))
        return light(candidate, **kwargs)

    def selected(candidates, **kwargs):
        events.append(("select", None))
        return select(candidates, **kwargs)

    monkeypatch.setattr(journey.verifier, "pydantic_prompt", verified)
    monkeypatch.setattr(pipeline, "light_review", reviewed)
    monkeypatch.setattr(pipeline, "select_deep_set", selected)
    result = run(journey)

    assert result.candidates[0].candidate_id == "P11"
    assert "P11" in journey.light and "P11" in journey.deep
    assert len(journey.generator.calls) == len(journey.verifier.calls) == 12
    assert journey.acquired.count("P11") == 1
    assert max(i for i, (kind, _) in enumerate(events) if kind == "verify") < next(
        i for i, (kind, _) in enumerate(events) if kind in {"light", "select"})
    receipt = result.candidates[0].question_evidence[0]
    text = "P11: original text\n" + QUOTE
    assert receipt["state"] == "sufficient" and receipt["complete"] is False
    assert receipt["source_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
    span, = receipt["evidence"]
    assert text[span["start"]:span["end"]] == span["quote"] == QUOTE
    assert result.evidence_status["promoted_candidate_ids"] == ["P11"]
    assert session.load(result.id).to_dict() == result.to_dict()


@pytest.mark.parametrize("mode", ["baseline", "no_questions", "same_route", "disabled_budget"])
def test_disabled_or_globally_unavailable_keeps_baseline_order_and_no_extra_calls(journey, monkeypatch, mode):
    if mode == "same_route":
        journey.deps.config.llm_routing.feed.model = journey.deps.config.llm_routing.deep_review.model
    if mode == "disabled_budget":
        monkeypatch.setenv("ZS_SEARCH_EVIDENCE_CALLS", "0")
    result = run(journey, policy="baseline" if mode == "baseline" else "question_evidence",
                 questions=[] if mode == "no_questions" else None)

    assert [c.candidate_id for c in result.candidates] == [f"P{n:02}" for n in range(1, 14)]
    assert not journey.generator.calls and not journey.verifier.calls
    assert len(journey.acquired) == 10 and len(journey.light) == 10 and len(journey.deep) == 4


def test_missing_source_and_provider_failure_are_unchecked_not_negative(journey, monkeypatch):
    acquire = evidence.acquire_full_text
    monkeypatch.setattr(evidence, "acquire_full_text", lambda *a, **kw: "")
    result = run(journey)
    assert all(c.question_evidence[0]["state"] == "unknown" for c in result.candidates)
    assert not journey.generator.calls
    assert result.evidence_status["promoted_candidate_ids"] == []

    monkeypatch.setattr(evidence, "acquire_full_text", acquire)
    journey.verifier.fail = True
    result = run(journey, name="provider-failure")
    assert len(journey.generator.calls) == len(journey.verifier.calls) == 12
    assert all(c.question_evidence[0]["state"] == "unknown" for c in result.candidates)
    assert result.evidence_status["promoted_candidate_ids"] == []
    assert "private synthetic verifier details" not in str(result.to_dict())


def test_acquired_scope_never_turns_retrieval_miss_into_document_absence(journey, monkeypatch):
    retrieve = evidence.retrieve_original_passages
    monkeypatch.setattr(evidence, "retrieve_original_passages", lambda text, questions, **kw:
                        ([[] for _ in questions], {"dense": False, "bm25": False, "reranker": False}))
    result = run(journey)
    row = result.candidates[0].question_evidence[0]
    assert (row["state"], row["reason"], row["complete"]) == ("unknown", "no_passages_retrieved", False)
    assert not journey.generator.calls and not journey.verifier.calls

    monkeypatch.setattr(evidence, "retrieve_original_passages", retrieve)
    journey.verifier.state = "not_found_in_checked_text"
    result = run(journey, name="checked-only")
    row = result.candidates[0].question_evidence[0]
    assert (row["state"], row["reason"], row["complete"]) == (
        "not_found_in_checked_text", "unchecked_remainder", False)
    assert row["checked_passages"] and result.evidence_status["promoted_candidate_ids"] == []


def test_deletion_stops_between_generator_and_verifier_without_recreating_session(journey):
    journey.generator.on_call = lambda: session.delete("deleted-policy")
    run(journey, name="deleted-policy")
    assert len(journey.generator.calls) == 1 and not journey.verifier.calls
    assert journey.light == journey.deep == []
    with pytest.raises(FileNotFoundError):
        session.load("deleted-policy")


def test_question_fingerprint_invalidation_refuses_promotion(journey, tmp_path):
    research = stored_session()
    research.candidates = [research.candidates[i] for i in (2, 0, 10, 1)]
    for candidate in research.candidates:
        candidate.query_score = 0.9
    session.save(research)
    result = pipeline.run_review(research.id, deps=journey.deps)
    assert result.candidates[0].candidate_id == "P11"
    path = tmp_path / "sessions" / f"{research.id}.json"
    before = path.read_bytes()
    result.questions = ["A changed question"]
    pipeline.rank_candidates(result.candidates)
    apply_question_policy(result)
    assert [c.candidate_id for c in result.candidates] == ["P03", "P01", "P11", "P02"]
    assert result.evidence_status["promoted_candidate_ids"] == []
    wire = result.to_dict(for_display=True)
    assert all(row["state"] == "unknown" and "answer" not in row
               for c in wire["candidates"] for row in c["question_evidence"])
    assert path.read_bytes() == before


def test_screen_policy_is_explicit_and_validated():
    assert ScreenRequest(query="topic").evidence_policy == "baseline"
    with pytest.raises(ValueError):
        ScreenRequest(query="topic", evidence_policy="automatic")


@pytest.mark.parametrize("restriction", ["retracted", "below_floor", "changed_source"])
def test_topic_integrity_and_source_identity_limit_promotion(journey, restriction):
    research = stored_session()
    if restriction == "retracted":
        research.candidates[10].is_retracted = True
    elif restriction == "below_floor":
        research.candidates[10].query_score = 0.1
    session.save(research)
    result = pipeline.run_review(research.id, deps=journey.deps)
    if restriction == "changed_source":
        assert result.candidates[0].candidate_id == "P11"
        result.candidates[0].url = "https://example.org/a-different-source.pdf"
        pipeline.rank_candidates(result.candidates)
        apply_question_policy(result)
    assert result.evidence_status["promoted_candidate_ids"] == []
    assert result.candidates[0].candidate_id == "P01"


def test_configured_call_limit_is_attempt_bound_and_unknown_tail_is_preserved(journey, monkeypatch):
    monkeypatch.setenv("ZS_SEARCH_EVIDENCE_CALLS", "3")
    result = run(journey)
    assert len(journey.generator.calls) == len(journey.verifier.calls) == 1
    assert result.evidence_status["calls_used"] == 2
    assert result.evidence_status["promoted_candidate_ids"] == []
    assert result.candidates[1].question_evidence[0]["reason"] == "call_budget_exhausted"


def test_shared_index_exposes_ranked_occurrences_without_changing_legacy_positive_gate():
    from zotero_summarizer.services.faithbench._corpus import PaperChunkIndex
    text = "same same same same"
    index = PaperChunkIndex(text, chunk_chars=5, overlap=0)
    index._bm25 = SimpleNamespace(get_scores=lambda tokens: [-1] * len(index.chunks))
    assert index.top_chunks("same", 6) == []
    assert index.spans == [(0, 4), (5, 9), (10, 14), (15, 19)]
    assert [text[start:end] for start, end in index.spans] == ["same"] * 4


def test_http_screen_persists_explicit_policy_through_actual_service(journey, monkeypatch):
    monkeypatch.setattr(search, "default_deps", lambda: journey.deps)
    monkeypatch.setattr(search, "_kickoff_review", lambda session_id: False)
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
        assert result["evidence_policy"] == "question_evidence" and result["evidence_status"] == {}
        assert client.get(f"/api/search/{result['id']}").json() == result
    assert session.load(result["id"]).evidence_policy == "question_evidence"
