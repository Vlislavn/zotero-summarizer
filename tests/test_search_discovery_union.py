"""Both optional Search capabilities survive the same existing session store."""
import json
from html import unescape
from types import SimpleNamespace

import pytest

from tests import test_search_question_policy as policy_cases
from zotero_summarizer.services.search import discovery, pipeline, session
from zotero_summarizer.services.search._models import Candidate, QueryPlan, ResearchSession, SearchIntent

journey = policy_cases.journey


@pytest.mark.parametrize("evidence,discovery", [(False, False), (False, True), (True, False), (True, True)])
def test_independent_capability_wire_and_store_roundtrip(tmp_path, monkeypatch, evidence, discovery):
    monkeypatch.setattr(session, "settings", lambda: SimpleNamespace(search_dir=tmp_path / "search"))
    fields = {"id": "joint-capabilities", "created_at": "2026-10-16", "raw_query": "Synthetic topic",
              "intent": SearchIntent(raw_query="Synthetic topic"), "plan": QueryPlan(), "questions": []}
    evidence_status = {"state": "pending", "promoted_candidate_ids": []}
    discovery_status = {"seed_key": "SEED0001", "fingerprint": "synthetic", "membership_unknown": 1}
    if evidence:
        fields.update(evidence_policy="question_evidence", evidence_status=evidence_status)
    if discovery:
        fields["discovery"] = discovery_status
    current = ResearchSession(**fields)
    wire = current.to_dict()
    assert ("evidence_policy" in wire) is evidence
    assert ("evidence_status" in wire) is evidence
    assert ("discovery" in wire) is discovery
    assert ResearchSession.from_dict(wire).to_dict() == wire
    session.save(current)
    loaded = session.load(current.id)
    assert loaded.to_dict() == wire
    if evidence:
        assert loaded.evidence_policy == "question_evidence" and loaded.evidence_status == evidence_status
    if discovery:
        assert loaded.discovery == discovery_status


def _screen_setup(tmp_path, monkeypatch, observe):
    monkeypatch.setattr(session, "settings", lambda: SimpleNamespace(search_dir=tmp_path / "search"))
    monkeypatch.setattr(pipeline, "require_online", lambda: None)
    monkeypatch.setattr(pipeline, "parse_intent", lambda raw, questions, **kwargs:
                        SearchIntent(raw_query=raw, questions=questions))
    calls = []

    def federate(plan, **kwargs):
        calls.append(kwargs)
        if kwargs.get("observe_outcomes"):
            plan.retrieval_accounting = {"arxiv": {"variants": [{"outcome": {
                "provider": "arxiv", "status": "success_nonempty", "count": 1, "error_code": None}}]}}
        return [Candidate(title="Synthetic candidate", doi="10.1/new", query_score=0.6)]

    monkeypatch.setattr(pipeline, "federate", federate)
    monkeypatch.setattr(pipeline, "score_query_relevance", lambda *args, **kwargs: None)
    deps = SimpleNamespace(llm=object(), openalex_client=None, openreview_client=None, library_finder=None,
                           quota=15, crossref_mailto=None, reranker_model="synthetic", reader=None,
                           observe_source_outcomes=observe)
    return deps, calls


@pytest.mark.parametrize("policy", ["baseline", "question_evidence"])
@pytest.mark.parametrize("observe", [False, True])
def test_screen_policy_and_observation_are_independent(tmp_path, monkeypatch, policy, observe):
    deps, calls = _screen_setup(tmp_path, monkeypatch, observe)
    result = pipeline.run_screen("Synthetic topic", ["Synthetic question?"], deps=deps, evidence_policy=policy)
    assert len(calls) == 1
    assert ("observe_outcomes" in calls[0]) is observe
    if observe:
        assert calls[0]["observe_outcomes"] is True
    assert result.evidence_policy == policy
    assert result.discovery == {}
    assert session.load(result.id).to_dict() == result.to_dict()


def test_actual_discovery_uses_merged_baseline_screen_without_review(tmp_path, monkeypatch):
    from zotero_summarizer.api.routes import search as search_api
    from zotero_summarizer.services.library.related import public_query

    deps, calls = _screen_setup(tmp_path, monkeypatch, False)
    seed = {"title": "Public title", "abstract": "Public abstract", "doi": "10.1/seed",
            "user_note": "PRIVATE synthetic fixture"}
    context = {"seed_key": "SEED0001", "seed": seed, "query": public_query(seed), "fingerprint": "synthetic",
               "items": {"SEED0001": seed}, "aliases": {"SEED0001"}}
    observed = []

    def parse(raw, questions, **kwargs):
        observed.append((raw, questions))
        return SearchIntent(raw_query=raw, questions=questions)

    def forbidden(*args, **kwargs):
        pytest.fail("Discovery must not start review, question evidence, or materialization")

    monkeypatch.setattr(pipeline, "parse_intent", parse)
    monkeypatch.setattr(pipeline, "run_question_evidence", forbidden)
    monkeypatch.setattr(pipeline, "run_review", forbidden)
    monkeypatch.setattr(search_api, "_kickoff_review", forbidden)
    monkeypatch.setattr(session, "materialize_once", forbidden)
    monkeypatch.setattr(discovery, "require_online", lambda: None)
    monkeypatch.setattr(discovery, "discovery_context", lambda key: context)
    monkeypatch.setattr(discovery, "default_deps", lambda: deps)
    result = discovery.find_new("SEED0001")
    loaded = session.load(result["id"])
    assert observed == [("Public title\nPublic abstract", [])]
    assert len(calls) == 1 and calls[0]["observe_outcomes"] is True
    assert loaded.evidence_policy == "baseline" and loaded.evidence_status == {}
    assert "evidence_policy" not in result and "evidence_status" not in result
    assert loaded.discovery == result["discovery"]
    assert loaded.discovery["provider_availability"] == "observed"
    assert loaded.discovery["comments_used"] is False
    assert loaded.discovery["source_outcomes"] == [{"provider": "arxiv", "status": "success_nonempty",
                                                   "count": 1, "error_code": None, "variant": 0}]
    assert all(row["review"] == {} and row["materialized_zotero_key"] == "" for row in result["candidates"])


def test_review_save_boundaries_keep_discovery_and_concurrent_add(journey, monkeypatch):
    metadata = {"seed_key": "SEED0001", "mode": "discovery_only", "source_outcomes": []}
    saved = []
    original_merge = session.save_merge
    added = False

    def add_once():
        nonlocal added
        if not added:
            session.update("joint-review", lambda current:
                           setattr(current.candidates[0], "materialized_zotero_key", "ADDED001"))
            added = True

    def merge(current):
        result = original_merge(current)
        saved.append(session.load(current.id).discovery)
        return result

    journey.generator.on_call = add_once
    monkeypatch.setattr(session, "save_merge", merge)
    result = policy_cases.run(journey, session_id="joint-review",
                                  before_review=lambda current: setattr(current, "discovery", dict(metadata)))
    loaded = session.load(result.id)
    assert added and len(saved) >= 3 and all(value == metadata for value in saved)
    assert loaded.discovery == metadata and loaded.evidence_policy == "question_evidence"
    assert next(c for c in loaded.candidates if c.candidate_id == "P01").materialized_zotero_key == "ADDED001"
    assert loaded.to_dict() == result.to_dict()


def test_two_questions_share_default_attempt_budget_candidate_first(journey):
    result = policy_cases.run(journey, questions=["First synthetic question?", "Second synthetic question?"],
                                  session_id="joint-two-questions")
    assert len(journey.generator.calls) + len(journey.verifier.calls) == 24
    requests = [json.loads(unescape(p.split("<untrusted_input>")[-1].split("</untrusted_input>")[0]))
                for p in journey.generator.calls]
    assert [p["candidate_id"] for p in requests] == [f"P{n:02}" for n in range(1, 7) for _ in range(2)]
    tail = next(c for c in result.candidates if c.candidate_id == "P07")
    assert len(tail.question_evidence) == 2
    assert all(row["state"] == "unknown" and row["reason"] == "call_budget_exhausted"
               for row in tail.question_evidence)
    assert result.evidence_status["promoted_candidate_ids"] == []


def test_adapter_projects_only_existing_model_payload_fields_and_retains_receipt_index(journey):
    result = policy_cases.run(journey, session_id="joint-model-payload")
    generator_payloads = [json.loads(unescape(p.split("<untrusted_input>")[-1].split("</untrusted_input>")[0]))
                          for p in journey.generator.calls]
    verifier_payloads = [json.loads(unescape(p.split("<untrusted_input>")[-1].split("</untrusted_input>")[0]))
                         for p in journey.verifier.calls]
    assert generator_payloads and verifier_payloads
    assert all(set(p) == {"question", "candidate_id", "passages"} for p in generator_payloads)
    assert all(set(p) == {"question", "passages", "proposal"} for p in verifier_payloads)
    assert all(row["question_index"] == 0 for c in result.candidates for row in c.question_evidence)
