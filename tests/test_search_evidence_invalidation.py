"""Read-time receipt identity and stable-tie contracts with scripted evidence only."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_search_question_evidence import journey, run, stored_session
from zotero_summarizer.api.routes import search
from zotero_summarizer.models.providers import resolve_stage
from zotero_summarizer.services import _common
from zotero_summarizer.services.search import _question_evidence as evidence
from zotero_summarizer.services.search import pipeline, session


def test_stale_receipts_restore_original_baseline_ties_without_writing(journey, tmp_path, monkeypatch):
    monkeypatch.setattr(_common.state(), "app_state", SimpleNamespace(config=journey.deps.config))
    research = stored_session(name="tied-evidence")
    research.candidates = [research.candidates[0], research.candidates[10]]
    for candidate in research.candidates:
        candidate.query_score = 0.5
    baseline = [c.candidate_id for c in research.candidates]
    session.save(research)
    assessed = pipeline.run_review(research.id, deps=journey.deps)
    assert [c.candidate_id for c in assessed.candidates] == list(reversed(baseline))
    path = tmp_path / "sessions" / f"{research.id}.json"
    stored = json.loads(path.read_text())
    stored["questions"] = ["Changed question"]
    path.write_text(json.dumps(stored))
    before = path.read_bytes()

    loaded = session.load(research.id)

    assert [c.candidate_id for c in loaded.candidates] == baseline
    assert loaded.evidence_status["promoted_candidate_ids"] == []
    assert path.read_bytes() == before
    wire = loaded.to_dict(for_display=True)
    assert all(r["state"] == "unknown" for c in wire["candidates"] for r in c["question_evidence"])
    session.save_merge(loaded)
    retained = json.loads(path.read_text())
    old = next(c for c in retained["candidates"] if c["candidate_id"] == "P11")
    assert old["question_evidence"][0]["answer"]


@pytest.mark.parametrize("changed", ["model", "settings", "protocol"])
def test_changed_current_evaluator_suppresses_active_answer_without_client_or_write(
    journey, monkeypatch, tmp_path, changed
):
    monkeypatch.setattr(_common.state(), "app_state", SimpleNamespace(config=journey.deps.config))
    assessed = run(journey)
    assert assessed.candidates[0].question_evidence[0]["state"] == "sufficient"
    path = tmp_path / "sessions" / f"{assessed.id}.json"
    before = path.read_bytes()
    if changed == "model":
        journey.deps.config.llm_routing.deep_review.model = "different-verifier"
    elif changed == "settings":
        provider = resolve_stage(journey.deps.config.llm_routing, "deep_review").provider
        provider.max_tokens += 1
    else:
        monkeypatch.setattr(evidence, "_VERIFY", evidence._VERIFY + "\nProtocol revision two.")
    no_clients = Mock(side_effect=AssertionError("saved reads must not construct clients"))
    monkeypatch.setattr(search, "default_deps", no_clients)
    monkeypatch.setattr(_common.state(), "resolve_stage_client", no_clients)
    app = FastAPI()
    app.include_router(search.router)

    with TestClient(app) as client:
        response = client.get(f"/api/search/{assessed.id}")

    assert response.status_code == 200
    projected = response.json()
    target = next(c for c in projected["candidates"] if c["candidate_id"] == "P11")
    assert target["question_evidence"][0]["state"] == "unknown"
    assert "answer" not in target["question_evidence"][0]
    assert projected["candidates"][0]["candidate_id"] == "P01"
    assert projected["evidence_status"]["promoted_candidate_ids"] == []
    assert path.read_bytes() == before
    no_clients.assert_not_called()
    session.save_merge(session.load(assessed.id))
    retained = json.loads(path.read_text())
    historical = next(c for c in retained["candidates"] if c["candidate_id"] == "P11")
    assert historical["question_evidence"][0]["answer"]
