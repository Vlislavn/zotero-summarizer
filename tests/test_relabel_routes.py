"""Literal relabel-audit commands must not be swallowed by the paper route."""

import warnings

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zotero_summarizer.api.errors import install_error_handlers
from zotero_summarizer.api.routes import relabel_audit


def test_reset_dispatches_without_a_verdict_body_and_only_removes_the_session(tmp_path, monkeypatch):
    session = tmp_path / "session.json"
    session.write_text("old session", encoding="utf-8")
    unrelated = tmp_path / "keep.json"
    unrelated.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(relabel_audit, "_session_path", lambda: session)
    app = FastAPI()
    app.include_router(relabel_audit.router)
    install_error_handlers(app)

    with TestClient(app) as client:
        response = client.post("/api/relabel-audit/reset")
        assert response.status_code == 200
        assert response.json() == {"deleted": True, "path": str(session)}
        assert not session.exists()
        assert client.post("/api/relabel-audit/reset").status_code == 200
        assert client.get("/api/relabel-audit/next").status_code == 404
        # The dynamic paper route remains registered and validates its body.
        assert client.post("/api/relabel-audit/PAPER001").status_code == 422
        assert client.post("/api/relabel-audit/PAPER001", json={"new_priority": "must_read"}).status_code == 404
    assert unrelated.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("answered", [0, 1, 2])
def test_metrics_checks_pair_count_before_computation(tmp_path, monkeypatch, answered):
    service = relabel_audit.relabel_audit
    session = tmp_path / "session.json"
    candidates = [service.AuditCandidate(
        item_key=f"PAPER00{i}", title="Paper", authors="A", venue="V", abstract="Text",
        days_since_added=100, age_bucket="90-180", original_priority=priority,
        original_inferred_relevance=score,
    ) for i, (priority, score) in enumerate([("must_read", 5.0), ("dont_read", 1.0)])]
    service.write_session(session, candidates, sample_size=2, seed=42)
    for candidate in candidates[:answered]:
        service.record_response(session, candidate.item_key, candidate.original_priority)
    monkeypatch.setattr(relabel_audit, "_session_path", lambda: session)
    app = FastAPI()
    app.include_router(relabel_audit.router)
    install_error_handlers(app)
    before = session.read_bytes()

    with warnings.catch_warnings(record=True) as caught, TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/relabel-audit/metrics")

    assert session.read_bytes() == before
    if answered < 2:
        assert response.status_code == 400
        assert "at least two" in response.json()["message"]
        assert not caught
    else:
        assert response.status_code == 200
        assert response.json()["n_paired"] == 2
        assert response.json()["icc_2_1"] == 1.0
