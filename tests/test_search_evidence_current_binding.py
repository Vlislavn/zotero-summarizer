"""Current-vs-historical GET/store controls; no semantic-model measurements."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_search_question_evidence import journey, stored_session
from zotero_summarizer.api.routes import search
from zotero_summarizer.models.providers import resolve_stage
from zotero_summarizer.services import _common
from zotero_summarizer.services.search import _question_evidence as evidence
from zotero_summarizer.services.search import _question_policy as policy
from zotero_summarizer.services.search import pipeline, session
from zotero_summarizer.services.search._models import Candidate
from zotero_summarizer.services.search.dedup import to_version_families


def tied(journey):
    research = stored_session(name="current-binding")
    research.candidates = [research.candidates[1], research.candidates[10], research.candidates[0]]
    research.candidates[1].doi = "10.1000/current-binding"
    for candidate in research.candidates:
        candidate.query_score = 0.5
    baseline = [c.candidate_id for c in research.candidates]  # Observed order, deliberately not ID-sorted.
    session.save(research)
    assessed = pipeline.run_review(research.id, deps=journey.deps)
    assert [c.candidate_id for c in assessed.candidates] == [baseline[1], baseline[0], baseline[2]]
    return assessed, baseline


def get_without_work(monkeypatch, session_id):
    forbidden = Mock(side_effect=AssertionError("saved GET cannot build clients, write or run evidence"))
    app = FastAPI()
    app.include_router(search.router)
    with monkeypatch.context() as guard:
        guard.setattr(search, "default_deps", forbidden)
        guard.setattr(_common.state(), "resolve_stage_client", forbidden)
        guard.setattr(evidence, "acquire_full_text", forbidden)
        guard.setattr(evidence, "retrieve_original_passages", forbidden)
        guard.setattr(pipeline, "run_review", forbidden)
        guard.setattr(session, "save", forbidden)
        with TestClient(app) as client:
            response = client.get(f"/api/search/{session_id}")
        assert response.status_code == 200
        forbidden.assert_not_called()
    return response.json()


def test_unchanged_binding_retains_active_answer_ties_and_exact_history(journey, monkeypatch, tmp_path):
    assessed, baseline = tied(journey)
    path = tmp_path / "sessions" / f"{assessed.id}.json"
    before = path.read_bytes()
    receipt = assessed.candidates[0].question_evidence[0]

    wire = get_without_work(monkeypatch, assessed.id)

    assert wire["evidence_baseline_order"] == baseline
    assert wire["candidates"][0]["question_evidence"][0] == receipt
    assert wire["evidence_status"]["promoted_candidate_ids"] == [baseline[1]]
    assert path.read_bytes() == before
    loaded = session.load(assessed.id)
    policy.apply_question_policy(loaded)  # Repeated promotion cannot replace the observed tie input.
    session.save_merge(loaded)
    assert session.load(assessed.id).evidence_baseline_order == baseline
    assert session.load(assessed.id).candidates[0].question_evidence[0] == receipt


@pytest.mark.parametrize("changed", ["question", "source", "generator", "verifier", "endpoint", "settings", "schema", "protocol"])
def test_get_restores_nonlexical_ties_and_retains_stale_receipts(journey, monkeypatch, tmp_path, changed):
    assessed, baseline = tied(journey)
    path = tmp_path / "sessions" / f"{assessed.id}.json"
    raw = json.loads(path.read_text())
    target = next(c for c in raw["candidates"] if c["candidate_id"] == baseline[1])
    historical = target["question_evidence"][0]
    if changed == "question":
        raw["questions"] = ["Changed question"]
    elif changed == "source":
        target["url"] = "https://example.org/changed-original.pdf"
    elif changed == "generator":
        journey.deps.config.llm_routing.feed.model = "another-generator"
    elif changed == "verifier":
        journey.deps.config.llm_routing.deep_review.model = "another-verifier"
    elif changed == "endpoint":
        resolve_stage(journey.deps.config.llm_routing, "deep_review").provider.base_url = "http://127.0.0.1:9998/v1"
    elif changed == "settings":
        resolve_stage(journey.deps.config.llm_routing, "feed").provider.temperature = 0.25
    elif changed == "schema":
        class RevisedVerdict(evidence.EvidenceVerdict):
            protocol_revision: int = 2
        monkeypatch.setattr(evidence, "EvidenceVerdict", RevisedVerdict)
    else:
        monkeypatch.setattr(evidence, "_EVIDENCE_PROTOCOL", evidence._EVIDENCE_PROTOCOL + "-revision")
    if changed in {"question", "source"}:
        path.write_text(json.dumps(raw))
    before = path.read_bytes()

    wire = get_without_work(monkeypatch, assessed.id)

    assert [c["candidate_id"] for c in wire["candidates"]] == baseline
    current = next(c for c in wire["candidates"] if c["candidate_id"] == baseline[1])["question_evidence"][0]
    assert current["state"] == "unknown" and "answer" not in current
    assert wire["evidence_status"]["promoted_candidate_ids"] == []
    assert path.read_bytes() == before
    loaded = session.load(assessed.id)
    policy.apply_question_policy(loaded)
    session.save_merge(loaded)
    retained = json.loads(path.read_text())
    assert [c["candidate_id"] for c in retained["candidates"]] == baseline
    assert next(c for c in retained["candidates"] if c["candidate_id"] == baseline[1])["question_evidence"][0] == historical


@pytest.mark.parametrize("absence", ["holder", "config", "routing", "disabled", "same_route"])
def test_absent_current_binding_is_unknown_without_work_or_lost_history(journey, monkeypatch, tmp_path, absence):
    assessed, baseline = tied(journey)
    path = tmp_path / "sessions" / f"{assessed.id}.json"
    historical = assessed.candidates[0].question_evidence[0]
    before = path.read_bytes()
    if absence == "holder":
        monkeypatch.setattr(_common.state(), "app_state", None)
    elif absence == "config":
        monkeypatch.setattr(_common.state(), "app_state", SimpleNamespace(config=None))
    elif absence == "routing":
        monkeypatch.setattr(journey.deps.config, "llm_routing", None)
    elif absence == "disabled":
        journey.deps.config.llm_enabled = False
    else:
        journey.deps.config.llm_routing.feed.model = journey.deps.config.llm_routing.deep_review.model

    wire = get_without_work(monkeypatch, assessed.id)

    assert [c["candidate_id"] for c in wire["candidates"]] == baseline
    assert wire["evidence_status"]["state"] == "unavailable"
    assert all(row["state"] == "unknown" for c in wire["candidates"] for row in c["question_evidence"])
    assert path.read_bytes() == before
    session.save_merge(session.load(assessed.id))
    retained = json.loads(path.read_text())
    assert next(c for c in retained["candidates"] if c["candidate_id"] == baseline[1])["question_evidence"][0] == historical


def test_baseline_sessions_do_not_consult_evaluator_metadata(journey, monkeypatch):
    forbidden = Mock(side_effect=AssertionError("baseline must not resolve an evidence binding"))
    monkeypatch.setattr(policy, "current_binding", forbidden)
    research = stored_session(policy="baseline")
    session.save(research)
    wire = get_without_work(monkeypatch, research.id)
    assert wire["evidence_policy"] == "baseline" and wire["evidence_baseline_order"] == []
    forbidden.assert_not_called()


def test_missing_runtime_binding_blocks_new_evidence_invocations(journey, monkeypatch):
    monkeypatch.setattr(_common.state(), "app_state", None)
    research = stored_session()
    session.save(research)
    assessed = pipeline.run_review(research.id, deps=journey.deps)
    assert assessed.evidence_status["state"] == "unavailable"
    assert not journey.generator.calls and not journey.verifier.calls
    assert [c.candidate_id for c in assessed.candidates] == [c.candidate_id for c in research.candidates]


def test_legacy_receipts_without_baseline_seed_do_not_invent_tie_order(journey, monkeypatch, tmp_path):
    assessed, _baseline = tied(journey)
    path = tmp_path / "sessions" / f"{assessed.id}.json"
    raw = json.loads(path.read_text())
    raw.pop("evidence_baseline_order")
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    observed = [c["candidate_id"] for c in raw["candidates"]]
    wire = get_without_work(monkeypatch, assessed.id)
    assert [c["candidate_id"] for c in wire["candidates"]] == observed
    assert wire["evidence_baseline_order"] == []  # The lost historical order is not recoverable.
    assert wire["evidence_status"]["promoted_candidate_ids"] == []
    assert all(r["state"] == "unknown" for c in wire["candidates"] for r in c["question_evidence"])
    assert path.read_bytes() == before


def test_previous_policy_protocol_cannot_retain_active_promotions(journey, monkeypatch, tmp_path):
    with monkeypatch.context() as previous:
        previous.setattr(evidence, "_EVIDENCE_PROTOCOL", "question-evidence-v2")
        assessed, baseline = tied(journey)
    path = tmp_path / "sessions" / f"{assessed.id}.json"
    before = path.read_bytes()
    historical = assessed.candidates[0].question_evidence[0]

    wire = get_without_work(monkeypatch, assessed.id)

    assert [c["candidate_id"] for c in wire["candidates"]] == baseline
    assert wire["evidence_status"]["promoted_candidate_ids"] == []
    assert all(row["state"] == "unknown" for c in wire["candidates"] for row in c["question_evidence"])
    assert path.read_bytes() == before
    session.save_merge(session.load(assessed.id))
    retained = json.loads(path.read_text())
    assert next(c for c in retained["candidates"] if c["candidate_id"] == baseline[1])["question_evidence"][0] == historical


def test_restore_keeps_current_quality_additions_family_and_concurrent_add_key(journey, monkeypatch, tmp_path):
    assessed, baseline = tied(journey)
    target = assessed.candidates[0]
    richer = Candidate(title="Enriched family", doi=target.doi, abstract="Richer metadata")
    assessed.candidates = to_version_families(assessed.candidates + [richer])
    newcomer = Candidate(title="New quality evidence", candidate_id="unrelated-new", query_score=0.5,
                         quality={"quality_band": "highlight", "grade": "A"})
    assessed.candidates.append(newcomer)
    session.save_merge(assessed)
    session.update(assessed.id, lambda s: setattr(next(c for c in s.candidates if c.candidate_id == baseline[1]),
                                                "materialized_zotero_key", "EXPLICIT-ADD"))
    journey.deps.config.llm_routing.deep_review.model = "changed-verifier"
    session.save_merge(assessed)  # Stale worker snapshot must retain the concurrent Add.
    path = tmp_path / "sessions" / f"{assessed.id}.json"
    before = path.read_bytes()

    wire = get_without_work(monkeypatch, assessed.id)

    assert [c["candidate_id"] for c in wire["candidates"]] == [newcomer.candidate_id, *baseline]
    assert len(wire["candidates"]) == 4
    enriched = next(c for c in wire["candidates"] if c["candidate_id"] == baseline[1])
    assert enriched["abstract"] == "Richer metadata" and enriched["materialized_zotero_key"] == "EXPLICIT-ADD"
    assert enriched["question_evidence"][0]["state"] == "unknown"
    assert path.read_bytes() == before
