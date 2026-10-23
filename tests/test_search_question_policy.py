"""Opt-in Search journey with acquired synthetic text; no real providers/network."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from zotero_summarizer.models import GoalsConfig
from zotero_summarizer.models.config import LLMConfig
from zotero_summarizer.services.search import pipeline, session
from zotero_summarizer.services.search._models import Candidate, QueryPlan, ResearchSession, ScreenRequest, SearchIntent
from zotero_summarizer.services.search._source_context import raw_digest

QUESTION = "Did the treatment improve the outcome?"
TEXT = "The registered study found no improvement from the treatment. " * 25


class ScriptedClient:
    def __init__(self, role):
        self.role, self.calls = role, []
        self.fail = False
        self.on_call = None

    def pydantic_prompt(self, *, prompt, pydantic_model, **kwargs):
        import json
        from html import unescape
        self.calls.append(prompt)
        if self.on_call:
            self.on_call()
        if self.fail:
            raise RuntimeError("synthetic provider failure")
        payload = json.loads(unescape(prompt.split("<untrusted_input>")[-1].split("</untrusted_input>")[0]))
        if self.role == "generator":
            ids = [payload["passages"][0]["passage_id"]] if payload["candidate_id"] == "P11" else []
            return pydantic_model(answer="No improvement was observed.", evidence_ids=ids)
        supported = bool(payload["proposal"]["evidence"])
        return pydantic_model(state="sufficient" if supported else "not_found_in_checked_text",
                              supported=supported,
                              evidence_ids=[r["passage_id"] for r in payload["proposal"]["evidence"]], uncovered=[])


@pytest.fixture
def journey(monkeypatch, tmp_path):
    from zotero_summarizer.services.search import _question_adapter
    monkeypatch.setattr(session, "settings", lambda: SimpleNamespace(search_dir=tmp_path / "sessions"))
    for name in ("ZS_SEARCH_EVIDENCE_CANDIDATES", "ZS_SEARCH_EVIDENCE_CALLS", "ZS_SEARCH_EVIDENCE_TOPIC_FLOOR"):
        monkeypatch.delenv(name, raising=False)
    config = GoalsConfig(relevance_scale={n: f"Synthetic level {n}" for n in range(1, 6)},
                         llm=LLMConfig(draft_model="synthetic-generator",
                         refine_model="synthetic-verifier", api_base="http://127.0.0.1:9999/v1", api_key_env="TEST_KEY"))
    config.llm_routing.feed.model = "synthetic-generator"
    config.llm_routing.deep_review.model = "synthetic-verifier"
    generator, verifier = ScriptedClient("generator"), ScriptedClient("verifier")
    deps = SimpleNamespace(config=config, llm=verifier, llm_light=generator, extractor=object(),
                           unpaywall_client=None, max_chars=60000, evidence_pass=None)
    acquired, light, deep = [], [], []

    def acquire(candidate, **kwargs):
        acquired.append(candidate.candidate_id)
        return TEXT

    monkeypatch.setattr(pipeline, "acquire_full_text", acquire)
    monkeypatch.setattr(_question_adapter, "acquire_full_text", acquire)
    monkeypatch.setattr(pipeline, "light_review", lambda c, **kw: light.append(c.candidate_id))
    monkeypatch.setattr(pipeline, "targeted_review", lambda c, **kw: deep.append(c.candidate_id))
    return SimpleNamespace(deps=deps, acquired=acquired, light=light, deep=deep, generator=generator, verifier=verifier)


def run(journey, policy="question_evidence", questions=None, session_id="evidence-journey", before_review=None):
    stored = ResearchSession(id=session_id, created_at="2026-10-08", raw_query="treatment studies",
                             intent=SearchIntent(raw_query="treatment studies", canonical_question="treatment studies"),
                             plan=QueryPlan(), questions=[QUESTION] if questions is None else questions,
                             candidates=[Candidate(title=f"Paper {n}", candidate_id=f"P{n:02}",
                                                   query_score=0.98 - n * 0.04) for n in range(1, 13)],
                             status="reviewing", evidence_policy=policy)
    if before_review:
        before_review(stored)
    session.save(stored)
    result = pipeline.run_review(stored.id, deps=journey.deps)
    return result


def test_real_opt_in_path_promotes_eleventh_before_both_cutoffs_and_persists(journey):
    result = run(journey)

    assert result.candidates[0].candidate_id == "P11"
    assert "P11" in journey.light and "P11" in journey.deep
    assert len(journey.generator.calls) == len(journey.verifier.calls) == 12
    assert journey.acquired.count("P11") == 1  # reused for ordinary review
    receipt = result.candidates[0].question_evidence[0]
    assert receipt["state"] == "sufficient" and receipt["complete"] is False
    assert receipt["source_sha256"] == raw_digest(TEXT)
    assert receipt["evidence"][0]["quote"] == TEXT[receipt["evidence"][0]["start"]:receipt["evidence"][0]["end"]]
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

    assert [c.candidate_id for c in result.candidates] == [f"P{n:02}" for n in range(1, 13)]
    assert not journey.generator.calls and not journey.verifier.calls
    assert len(journey.acquired) == 10


def test_missing_source_and_provider_failure_are_unchecked_not_negative(journey, monkeypatch):
    from zotero_summarizer.services.search import _question_adapter
    monkeypatch.setattr(_question_adapter, "acquire_full_text", lambda *a, **kw: "")
    result = run(journey)
    assert all(c.question_evidence[0]["state"] == "unknown" for c in result.candidates)
    assert not journey.generator.calls

    monkeypatch.setattr(_question_adapter, "acquire_full_text", lambda *a, **kw: TEXT)
    journey.verifier.fail = True
    result = run(journey, session_id="provider-failure")
    assert all(c.question_evidence[0]["state"] == "unknown" for c in result.candidates)
    assert result.evidence_status["promoted_candidate_ids"] == []


def test_acquired_scope_never_turns_retrieval_miss_into_document_absence(journey):
    result = run(journey)
    checked = next(c for c in result.candidates if c.candidate_id == "P01").question_evidence[0]
    assert checked["state"] == "unknown"
    assert checked["reason"] == "unchecked_remainder"
    assert checked["complete"] is False
    assert checked["checked_passages"]


def test_deletion_stops_between_generator_and_verifier_without_recreating_session(journey):
    journey.generator.on_call = lambda: session.delete("deleted-policy")
    run(journey, session_id="deleted-policy")
    assert not journey.verifier.calls
    with pytest.raises(FileNotFoundError):
        session.load("deleted-policy")


def test_question_fingerprint_invalidation_refuses_promotion(journey):
    from zotero_summarizer.services.search._question_policy import apply_question_policy
    result = run(journey)
    result.questions = ["A changed question"]
    pipeline.rank_candidates(result.candidates)
    apply_question_policy(result)
    assert result.candidates[0].candidate_id == "P01"
    assert result.evidence_status["promoted_candidate_ids"] == []


def test_screen_policy_is_explicit_and_validated():
    assert ScreenRequest(query="topic").evidence_policy == "baseline"
    with pytest.raises(ValueError):
        ScreenRequest(query="topic", evidence_policy="automatic")


@pytest.mark.parametrize("restriction", ["retracted", "below_floor", "changed_source"])
def test_topic_integrity_and_source_identity_limit_promotion(journey, restriction):
    from zotero_summarizer.services.search._question_policy import apply_question_policy

    def restrict(stored):
        target = stored.candidates[10]
        if restriction == "retracted":
            target.is_retracted = True
        elif restriction == "below_floor":
            target.query_score = 0.1

    result = run(journey, before_review=restrict)
    if restriction == "changed_source":
        result.candidates[0].url = "https://example.org/a-different-source.pdf"
        pipeline.rank_candidates(result.candidates)
        apply_question_policy(result)
    assert result.evidence_status["promoted_candidate_ids"] == []
    assert result.candidates[0].candidate_id == "P01"


def test_configured_call_limit_is_attempt_bound_and_unknown_tail_is_preserved(journey, monkeypatch):
    monkeypatch.setenv("ZS_SEARCH_EVIDENCE_CALLS", "3")
    result = run(journey)
    assert len(journey.generator.calls) + len(journey.verifier.calls) == 3
    assert result.evidence_status["promoted_candidate_ids"] == []
    assert result.candidates[-1].question_evidence[0]["reason"] == "call_budget_exhausted"


def test_shared_index_exposes_ranked_occurrences_without_changing_legacy_positive_gate():
    from zotero_summarizer.services.faithbench._corpus import PaperChunkIndex
    index = PaperChunkIndex("same words same words same words", chunk_chars=12, overlap=0)
    index._bm25 = SimpleNamespace(get_scores=lambda tokens: [-1] * len(index.chunks))
    assert index.top_chunks("same", 6) == []
    assert index.top_spans("same", 6) == index.spans
    assert index.top_spans("same", 0) == []


def test_http_screen_persists_explicit_policy_through_actual_service(journey, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from zotero_summarizer.api.routes import search
    monkeypatch.setattr(search, "default_deps", lambda: journey.deps)
    monkeypatch.setattr(search, "_kickoff_review", lambda session_id: False)
    journey.deps.reranker_model = "synthetic"
    journey.deps.openalex_client = journey.deps.openreview_client = journey.deps.library_finder = None
    journey.deps.crossref_mailto = journey.deps.reader = None
    journey.deps.quota = 15
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
    assert result["evidence_status"]["state"] == "pending"
    assert session.load(result["id"]).evidence_policy == "question_evidence"
