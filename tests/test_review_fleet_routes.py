"""Route tests for the review-fleet API surface (``/api/library/review-fleet/*``).

The fleet itself is stubbed — these assert the HTTP wiring only: the two routes are
registered, ``GET /status`` returns ``fleet.status()`` verbatim, and ``POST /run``
forwards the validated ``top_k`` to ``fleet.start`` and returns its payload. No job,
no LLM, no model dir is touched.
"""
from __future__ import annotations

import asyncio

from zotero_summarizer.api.app import create_app
from zotero_summarizer.api.routes import library as library_routes


def test_review_fleet_routes_registered():
    paths = {getattr(route, "path", "") for route in create_app().routes}
    assert "/api/library/review-fleet/run" in paths
    assert "/api/library/review-fleet/status" in paths


def test_status_route_returns_fleet_status_verbatim(monkeypatch):
    payload = {
        "status": "ready",
        "total": 3,
        "completed": 3,
        "error": None,
        "started_at": "2026-06-16T00:00:00Z",
        "progress": {},
    }
    monkeypatch.setattr(library_routes.review_fleet, "status", lambda: payload)
    out = asyncio.run(library_routes.get_review_fleet_status())
    assert out == payload


def test_run_route_forwards_top_k_to_fleet_start(monkeypatch):
    seen = {}

    def _start(top_k):
        seen["top_k"] = top_k
        return {"status": "running", "total": 0, "completed": 0, "error": None}

    monkeypatch.setattr(library_routes.review_fleet, "start", _start)
    req = library_routes.ReviewFleetRunRequest(top_k=4)
    out = asyncio.run(library_routes.run_review_fleet(req))
    assert seen["top_k"] == 4
    assert out["status"] == "running"


def test_run_route_default_top_k(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        library_routes.review_fleet, "start", lambda top_k: seen.update(top_k=top_k) or {}
    )
    asyncio.run(library_routes.run_review_fleet(library_routes.ReviewFleetRunRequest()))
    assert seen["top_k"] == 5  # the model default


def test_run_request_rejects_out_of_range_top_k():
    """The request model clamps top_k to [1,20] — a 0 or 21 is a 422 at the boundary."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        library_routes.ReviewFleetRunRequest(top_k=0)
    with pytest.raises(ValidationError):
        library_routes.ReviewFleetRunRequest(top_k=21)
