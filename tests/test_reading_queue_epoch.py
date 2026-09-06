"""A queued Rescore pins its actual worker-start model and inference config."""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests._reading_queue_support import FakeGate, FakeReader, isolate, item, patch_state, seed
from zotero_summarizer.api.routes import library
from zotero_summarizer.services.library import reading_queue as rq


@pytest.mark.parametrize("moment,unload", [
    ("before_start", False), ("before_start", True),
    ("after_first_batch", False), ("after_first_batch", True), ("config_only", False),
])
def test_http_rescore_uses_one_worker_epoch(monkeypatch, tmp_path, moment, unload):
    isolate(monkeypatch, tmp_path)
    jobs, calls = [], []
    monkeypatch.setattr(rq, "run_in_background", jobs.append)
    monkeypatch.setattr(rq, "_SCORE_BATCH", 1)
    monkeypatch.setattr(rq, "_goal_affinity", lambda keys: {})

    def swap():
        if moment != "config_only":
            runtime.classifier_gate = replacement
        runtime.app_state = SimpleNamespace(config=new_config)

    class Gate(FakeGate):
        def predict(self, items, **kwargs):
            calls.append((self.model_sha256, kwargs["goals_config"], kwargs["prestige_network"]))
            if moment != "before_start" and len(calls) == 1:
                swap()
            return super().predict(items, **kwargs)

    initial = Gate("first", {"A": 1.0, "B": 2.0})
    replacement = None if unload else Gate("second", {"A": 4.0, "B": 5.0})
    patch_state(monkeypatch, FakeReader([item("A"), item("B")]), initial)
    runtime = rq.get_state()
    monkeypatch.setattr(rq, "get_state", lambda: runtime)
    initial_config, new_config = object(), object()
    runtime.app_state.config = initial_config
    seed("previous", A=3.0)
    before = rq._cache_path().read_bytes()
    app = FastAPI()
    app.include_router(library.router)

    try:
        with TestClient(app) as client:
            assert client.get("/api/library/reading-queue", params={"refresh": True}).json()["status"] == "computing"
            assert len(jobs) == 1 and rq._cache_path().read_bytes() == before
            if moment == "before_start":
                swap()
            if moment == "before_start" and unload:
                with pytest.raises(RuntimeError, match="gate.*unavailable"):
                    jobs[0]()
                assert rq._cache_path().read_bytes() == before
                status = client.get("/api/library/reading-queue/status").json()
                assert status["running"] is False and "unavailable" in status["error"]
                assert calls == []
                return
            jobs[0]()
            assert client.get("/api/library/reading-queue/status").json() == {"running": False, "error": None}

        expected_gate = replacement if moment == "before_start" else initial
        expected_config = new_config if moment == "before_start" else initial_config
        assert calls == [(expected_gate.model_sha256, expected_config, True)] * 2
        saved = json.loads(rq._cache_path().read_bytes())
        assert saved["gate_sha"] == expected_gate.model_sha256
        assert {k: v["relevance_score"] for k, v in saved["scores"].items()} == expected_gate._scores
        if runtime.classifier_gate is not None:
            assert rq.read_score_cache_with_staleness()[1] is (runtime.classifier_gate is not expected_gate)
    finally:
        rq.finish()


def test_live_scoring_still_uses_current_gate_with_cache_only_prestige(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    calls = []

    class Gate(FakeGate):
        def predict(self, items, **kwargs):
            calls.append(kwargs)
            return super().predict(items, **kwargs)

    patch_state(monkeypatch, FakeReader([]), Gate("current", {"A": 4.0}))
    runtime = rq.get_state()
    monkeypatch.setattr(rq, "get_state", lambda: runtime)

    result = rq.live_scoring(item("A"))

    assert result["composite_score"] == 4.0
    assert calls[0]["prestige_network"] is False and calls[0]["return_shap"] is True
    rq.get_state().classifier_gate = None
    assert rq.live_scoring(item("A")) is None
    assert len(calls) == 1
