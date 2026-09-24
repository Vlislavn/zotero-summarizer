"""Rescore recovers a broken cache without publishing a partial new model epoch."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests._reading_queue_support import FakeGate, FakeReader, isolate, item, patch_state, seed
from zotero_summarizer.api.routes import library
from zotero_summarizer.services.library import reading_queue as rq


@pytest.fixture
def setup(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    patch_state(monkeypatch, FakeReader([item("A"), item("B", tags=["🧠"])]),
                FakeGate("new", {"A": 4.0, "B": 3.0}))
    monkeypatch.setattr(rq, "_goal_affinity", lambda keys: {k: 0.7 for k in keys})
    monkeypatch.setattr(rq, "_SCORE_BATCH", 1)
    yield rq._cache_path()
    rq.finish()


@pytest.mark.parametrize("broken", [b'{"scores":', b'[]', b'\xff'])
def test_http_rescore_recovers_corruption_but_normal_read_stays_fail_fast(setup, monkeypatch, broken):
    setup.write_bytes(broken)
    jobs = []
    monkeypatch.setattr(rq, "run_in_background", jobs.append)
    app = FastAPI()
    app.include_router(library.router)

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/api/library/reading-queue").status_code == 500
        assert jobs == []
        for _ in range(2):
            response = client.get("/api/library/reading-queue", params={"refresh": True})
            assert response.status_code == 200
            assert response.json()["status"] == "computing"
        assert len(jobs) == 1
        assert setup.read_bytes() == broken
        assert client.get("/api/library/reading-queue/status").json() == {"running": True, "error": None}
        jobs[0]()
        assert client.get("/api/library/reading-queue/status").json() == {"running": False, "error": None}
        result = client.get("/api/library/reading-queue", params={"include_read": True}).json()

    assert result["status"] == "ready" and result["scores_stale"] is False
    assert {r["item_key"]: r["relevance_score"] for r in result["items"]} == {"A": 4.0, "B": 3.0}
    saved = json.loads(setup.read_bytes())
    assert saved["gate_sha"] == "new" and saved["computed_at"]
    assert {v["goal_sim"] for v in saved["scores"].values()} == {0.7}


@pytest.mark.parametrize("failure", ["score", "goal", "publish"])
def test_late_rebuild_failure_keeps_previous_snapshot_and_retry_replaces_it(setup, monkeypatch, failure):
    seed("old", A=1.0, B=2.0, GONE=5.0)
    before = setup.read_bytes()
    target = {"score": "_score_items", "goal": "_goal_affinity", "publish": "_write_cache"}[failure]
    original = getattr(rq, target)
    calls = []

    def fail_late(*args, **kwargs):
        calls.append(args)
        assert setup.read_bytes() == before
        if failure == "publish" or len(calls) == 2:
            raise RuntimeError("injected late failure")
        return original(*args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(rq, target, fail_late)
        rq.try_start()
        with pytest.raises(RuntimeError, match="injected late failure"):
            rq._compute_scores_into_cache()

    assert setup.read_bytes() == before
    assert not rq.is_running() and "injected late failure" in rq.last_error()
    assert rq.build_reading_queue()["scores_stale"] is True
    rq.try_start()
    rq._compute_scores_into_cache()
    assert not rq.is_running() and rq.last_error() is None
    saved = json.loads(setup.read_bytes())
    assert saved["gate_sha"] == "new"
    assert {k: v["relevance_score"] for k, v in saved["scores"].items()} == {"A": 4.0, "B": 3.0}


def test_empty_library_rebuild_replaces_broken_cache(setup, monkeypatch):
    setup.write_bytes(b'{')
    patch_state(monkeypatch, FakeReader([]), FakeGate("new"))

    rq._compute_scores_into_cache()

    assert json.loads(setup.read_bytes())["scores"] == {}
