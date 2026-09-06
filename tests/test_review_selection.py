"""Explicit review scopes stay explicit through HTTP, scheduling and workers."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zotero_summarizer.api.errors import APIError, install_error_handlers
from zotero_summarizer.api.routes import library
from zotero_summarizer.services.library import deep_review
from zotero_summarizer.services.library.review_fleet import fleet, verdict_store


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(fleet, "_LATCH", fleet._flight.FlightLatch())
    monkeypatch.setattr(fleet, "_STATE", dict(fleet._STATE))
    monkeypatch.setattr(deep_review, "_JOBS", {})
    fleet.try_start()
    fleet.finish()
    app = FastAPI()
    app.include_router(library.router)
    install_error_handlers(app)
    with TestClient(app) as http:
        yield http


@pytest.mark.parametrize("running", [False, True])
def test_empty_http_scope_does_not_claim_or_schedule_work(client, monkeypatch, running):
    if running:
        fleet.try_start()
    before = fleet.status()
    scheduled = []
    monkeypatch.setattr(fleet._flight, "run_in_background", scheduled.append)

    response = client.post("/api/library/review-fleet/run", json={"item_keys": [], "top_k": 20})

    assert response.status_code == 200
    assert response.json() == {**before, "accepted": False}
    assert scheduled == []
    assert fleet.status() == before


def test_empty_deep_review_scope_never_builds_context(monkeypatch):
    before = deep_review.status()
    calls = []
    monkeypatch.setattr(deep_review, "_build_ctx", lambda **kw: calls.append(kw) or {})
    monkeypatch.setattr(deep_review, "_resolve_items", lambda *args: calls.append(args) or [])

    assert deep_review.start(item_keys=[]) == {**before, "accepted": False}
    assert calls == []
    assert deep_review.status() == before


@pytest.mark.parametrize("keys, expected", [(None, ["AUTO"]), ([], []), (["B", "A", "B"], ["B", "A"])])
def test_resolver_preserves_scope_and_first_seen_order(monkeypatch, keys, expected):
    scans = []
    monkeypatch.setattr(deep_review.reading_queue, "build_reading_queue",
                        lambda **kw: scans.append(kw) or {"items": [{"item_key": "AUTO"}]})
    monkeypatch.setattr(deep_review.reading_queue, "get_cached_scoring", lambda key: None)

    items = deep_review._resolve_items(1, keys, {"B": "/cache/B.pdf"})

    assert [item["item_key"] for item in items] == expected
    assert bool(scans) is (keys is None)
    if keys:
        assert items[0]["pdf_path"] == "/cache/B.pdf"


@pytest.mark.parametrize("payload, expected", [
    ({"top_k": 1}, ["AUTO"]),
    ({"item_keys": None, "top_k": 1}, ["AUTO"]),
    ({"item_keys": ["B", "A", "B", "A"], "top_k": 1}, ["B", "A"]),
])
def test_http_scope_reaches_real_workers_once(client, monkeypatch, payload, expected):
    reviewed, scans = [], []
    monkeypatch.setattr(fleet._flight, "run_in_background", lambda fn: fn())
    monkeypatch.setattr(deep_review, "_build_ctx", lambda **kw: {})
    monkeypatch.setattr(deep_review, "_ensure_pool", lambda provider: SimpleNamespace(
        submit=lambda fn, *args: fn(*args),
    ))
    monkeypatch.setattr(deep_review.reading_queue, "build_reading_queue",
                        lambda **kw: scans.append(kw) or {"items": [{"item_key": "AUTO"}]})
    monkeypatch.setattr(deep_review.reading_queue, "get_cached_scoring", lambda key: None)

    def review(item, **kwargs):
        reviewed.append(item["item_key"])
        return {"review_contract_version": deep_review.REVIEW_CONTRACT_VERSION,
                "digest": {"read_decision": "skim", "grade": "C"}, "needs_pdf": False}

    monkeypatch.setattr(deep_review, "_review_one", review)
    response = client.post("/api/library/review-fleet/run", json=payload)

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ready"
    assert result["total"] == result["completed"] == result["proposed"] == len(expected)
    assert reviewed == expected
    assert set(deep_review._read_all()) == set(verdict_store.read_all()) == set(expected)
    assert bool(scans) is (payload.get("item_keys") is None)


@pytest.mark.parametrize("key", ["", " ", ".", "..", "a/b", "a\\b", "a\0b", "feed:123", "feed:d:" + "a" * 64, "note:A:1", None, 7])
def test_http_rejects_invalid_scope_before_scheduling(client, monkeypatch, key):
    before = fleet.status()
    scheduled = []
    monkeypatch.setattr(fleet._flight, "run_in_background", scheduled.append)

    response = client.post("/api/library/review-fleet/run", json={"item_keys": ["VALID", key]})

    assert response.status_code == 422
    assert scheduled == []
    assert fleet.status() == before
    assert deep_review._read_all() == verdict_store.read_all() == {}


@pytest.mark.parametrize("key", ["..", "feed:123", "note:A:1"])
def test_direct_fleet_rejects_invalid_scope_before_claim(client, monkeypatch, key):
    before = fleet.status()
    scheduled = []
    monkeypatch.setattr(fleet._flight, "run_in_background", scheduled.append)

    with pytest.raises(APIError) as exc:
        fleet.start(item_keys=["VALID", key])

    assert exc.value.status_code == 422
    assert scheduled == []
    assert fleet.status() == before


def test_fleet_snapshots_selection_before_background_execution(client, monkeypatch):
    scheduled, received = [], []
    monkeypatch.setattr(fleet._flight, "run_in_background", scheduled.append)
    monkeypatch.setattr(fleet, "_run_job", lambda top_k, item_keys: received.append(item_keys))
    keys = ["B", "A", "B"]

    assert fleet.start(item_keys=keys)["accepted"] is True
    keys[:] = ["UNRELATED"]
    scheduled[0]()

    assert received == [["B", "A"]]
