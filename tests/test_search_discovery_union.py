"""Current Search and real Discovery storage/work boundaries, not semantic accuracy."""
import json
from copy import deepcopy
from html import unescape
from types import SimpleNamespace

import httpx
import pytest

from tests._search_evidence_fixtures import QUOTE, journey, run  # noqa: F401
from tests.test_discovery_server import PRIVATE, begin, journey as discovery_journey  # noqa: F401
from zotero_summarizer.integrations import openalex
from zotero_summarizer.services.library._prompt_security import UNTRUSTED_INPUT_RULE
from zotero_summarizer.services.search import discovery, federate as fed, pipeline, session
from zotero_summarizer.services.search._models import Candidate, QueryPlan, ResearchSession, SearchIntent


@pytest.mark.parametrize("evidence,has_discovery", [(False, False), (False, True), (True, False), (True, True)])
def test_independent_capability_wire_and_store_roundtrip(discovery_journey, evidence, has_discovery):
    if has_discovery:
        response = begin(discovery_journey)
        assert response.status_code == 200
        current = session.load(response.json()["id"])
    else:
        current = session.new_session(raw_query="Synthetic topic", intent=SearchIntent(raw_query="Synthetic topic"),
                                      plan=QueryPlan(), questions=[])
    current.evidence_policy = "question_evidence" if evidence else "baseline"
    session.save(current)
    durable = current.to_dict(for_storage=True)
    assert durable["evidence_policy"] == current.evidence_policy and durable["evidence_status"] == {}
    assert bool(durable["discovery"]) is has_discovery
    assert ResearchSession.from_dict(durable).to_dict(for_storage=True) == durable
    assert session.load(current.id).to_dict(for_storage=True) == durable
    wire = current.to_dict(for_display=True)
    if has_discovery:
        assert wire["discovery"]["freshness"] == "unverified" and not wire["discovery"]["new_actions_allowed"]
        assert "snapshot" not in wire["discovery"]
        with pytest.raises(ValueError, match="projections cannot restore"):
            ResearchSession.from_dict(wire)
        assert discovery.discovery_view(current)["discovery"]["freshness"] == "current"
    else:
        assert "discovery" not in wire and wire["related"] == {}


@pytest.mark.parametrize("policy", ["baseline", "question_evidence"])
@pytest.mark.parametrize("observe", [False, True])
def test_screen_policy_and_observation_are_independent(journey, monkeypatch, policy, observe):
    calls = []
    monkeypatch.setattr(pipeline, "parse_intent", lambda raw, questions, **kw:
                        SearchIntent(raw_query=raw, questions=questions))

    def public_pool(plan, **kwargs):
        calls.append(kwargs)
        return [Candidate(title="Synthetic candidate", doi="10.1/new", query_score=0.6)]

    monkeypatch.setattr(pipeline, "federate", public_pool)
    monkeypatch.setattr(pipeline, "score_query_relevance", lambda *a, **kw: None)
    result = pipeline.run_screen("Synthetic topic", ["Synthetic question?"], deps=journey.deps, evidence_policy=policy)
    assert len(calls) == 1 and "observe_outcomes" not in calls[0]
    assert result.evidence_policy == policy and result.discovery == {}
    assert session.load(result.id).to_dict() == result.to_dict()

    monkeypatch.setattr(openalex, "_RATE_LIMITER", SimpleNamespace(acquire=lambda: None))
    plan = QueryPlan(openalex_lexical="public topic")
    requests = []

    def provider(request):
        requests.append(request)
        return httpx.Response(200, json={"results": []})

    with httpx.Client(transport=httpx.MockTransport(provider), trust_env=False) as http:
        client = openalex.OpenAlexClient(object(), http_client=http)
        assert fed.federate(plan, openalex_client=client, quota=1, observe_outcomes=observe) == []
    assert len(requests) == 1
    row, = plan.retrieval_accounting["openalex"]["variants"]
    if observe:
        assert row["outcome"] == {"status": "success_empty", "count": 0, "error_code": None}
    else:
        assert "outcome" not in row
    assert journey.generator.calls == journey.verifier.calls == []


def test_actual_discovery_uses_merged_baseline_screen_without_review(discovery_journey, monkeypatch):
    from zotero_summarizer.api.routes import search

    def forbidden(*a, **kw):
        pytest.fail("Find must not start Review, question evidence or materialization")

    monkeypatch.setattr(search, "_kickoff_review", forbidden)
    monkeypatch.setattr(pipeline, "run_question_evidence", forbidden)
    monkeypatch.setattr(pipeline, "run_review", forbidden)
    monkeypatch.setattr(session, "materialize_once", forbidden)
    metadata = discovery_journey.client.get("/api/library/related/SEED0001/discover").json()
    assert metadata["context_fingerprint"] and discovery_journey.requests == discovery_journey.payloads == []
    response = begin(discovery_journey)
    assert response.status_code == 200
    result = response.json()
    loaded = session.load(result["id"])
    assert loaded.evidence_policy == "baseline" and loaded.questions == [] and loaded.evidence_status == {}
    assert result["evidence_policy"] == "baseline" and result["evidence_status"] == {}
    assert len(discovery_journey.requests) == 1 and discovery_journey.payloads == []
    plan, = discovery_journey.requests
    assert not plan["arxiv"] and not plan["europepmc"] and plan["openalex_semantic"]
    assert PRIVATE not in json.dumps(plan, ensure_ascii=False)
    assert result["discovery"]["freshness"] == "current"
    assert result["discovery"]["assessment"]["status"] == "not_requested"
    assert "snapshot" not in result["discovery"]
    assert all(c["review"] == {} and not c["materialized_zotero_key"] for c in result["candidates"])


def test_review_save_boundaries_keep_discovery_and_concurrent_add(discovery_journey, monkeypatch):
    from zotero_summarizer.api.routes import search

    response = begin(discovery_journey)
    assert response.status_code == 200
    initial = session.load(response.json()["id"])
    retained = deepcopy(initial.discovery)
    saved, queued, reviewed = [], [], []
    original_merge = session.save_merge

    class QueuedWorker:
        def __init__(self, *, target, args, daemon):
            queued.append((target, args, daemon))

        def start(self):
            return None

    def light(candidate, **kwargs):
        if not reviewed:
            session.update(initial.id, lambda s: setattr(s.candidates[0], "materialized_zotero_key", "ADDED001"))
        reviewed.append(candidate.candidate_id)

    def merge(current):
        result = original_merge(current)
        saved.append(session.load(current.id).discovery)
        return result

    deps = pipeline.SearchDeps(config=discovery_journey.config, llm=object(), llm_light=object(),
                               extractor=object(), reranker_model="synthetic", max_chars=60000)
    monkeypatch.setattr(search, "threading", SimpleNamespace(Thread=QueuedWorker))
    monkeypatch.setattr(search, "default_deps", lambda: deps)
    monkeypatch.setattr(pipeline, "acquire_full_text", lambda *a, **kw: QUOTE)
    monkeypatch.setattr(pipeline, "light_review", light)
    monkeypatch.setattr(pipeline, "targeted_review", lambda *a, **kw: None)
    monkeypatch.setattr(session, "save_merge", merge)
    assert search._kickoff_review(initial.id, explicit=True) is True
    assert len(queued) == 1 and session.load(initial.id).status == "reviewing"
    worker, args, daemon = queued[0]
    assert daemon is True
    worker(*args)
    loaded = session.load(initial.id)
    assert loaded.status == "reviewed" and reviewed
    assert len(saved) >= 3 and all(value == retained for value in saved)
    assert loaded.discovery == retained and loaded.evidence_policy == "baseline"
    assert next(c for c in loaded.candidates if c.candidate_id == initial.candidates[0].candidate_id
                ).materialized_zotero_key == "ADDED001"


def _generator_fields(prompt):
    assert prompt.startswith(UNTRUSTED_INPUT_RULE + "\n\n")
    body = prompt.removeprefix(UNTRUSTED_INPUT_RULE)
    return [unescape(block.split("</untrusted_input>", 1)[0]).strip()
            for block in body.split("<untrusted_input>")[1:]]


def test_two_questions_share_default_attempt_budget_candidate_first(journey):
    questions = ["First synthetic question?", "Second synthetic question?"]
    result = run(journey, questions=questions, name="joint-two-questions")
    assert len(journey.generator.calls) == len(journey.verifier.calls) == 12
    fields = [_generator_fields(prompt) for prompt in journey.generator.calls]
    assert fields == [[f"P{n:02}: original text\n" + QUOTE, question]
                      for n in range(1, 7) for question in questions]
    tail = next(c for c in result.candidates if c.candidate_id == "P07")
    assert len(tail.question_evidence) == 2
    assert all(row["state"] == "unknown" and row["reason"] == "call_budget_exhausted"
               for row in tail.question_evidence)
    assert result.evidence_status["calls_used"] == 24 and result.evidence_status["promoted_candidate_ids"] == []


def test_adapter_projects_only_existing_model_payload_fields_and_retains_receipt_index(journey):
    result = run(journey, name="joint-model-payload")
    fields = [_generator_fields(prompt) for prompt in journey.generator.calls]
    assert fields and all(len(row) == 2 and row[1] == result.questions[0] for row in fields)
    assert journey.verifier.calls
    assert all(set(payload) == {"question", "answer", "evidence", "original_passages"}
               for payload in journey.verifier.calls)
    assert all(row["question_index"] == 0 for c in result.candidates for row in c.question_evidence)
    assert PRIVATE not in json.dumps(journey.verifier.calls, ensure_ascii=False)
