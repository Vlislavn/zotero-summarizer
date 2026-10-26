"""Explicit processing must use a model that becomes ready in the same invocation."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from tests.test_research_feed_telegram import (
    MODEL, SCORES, ExternalGates, _posts, _project, _report, _run, _stored, _write_inputs,
)
from tests.test_reranker_readiness import _loader


class StartupGates(ExternalGates):
    def __init__(self, posts, ready):
        super().__init__(posts)
        self.ready = ready

    def is_ready(self):
        return self.ready

    def ensure_loaded_async(self):
        self.trace.append(("load",))
        self.ready = True

    def wait_until_ready(self, *, timeout):
        self.trace.append(("wait", timeout))
        self.ensure_loaded_async()
        return self.is_ready()


@pytest.fixture(autouse=True)
def _loaded_sources(record_property):
    yield
    root = Path(__file__).resolve().parents[1]
    for name in ("zotero_summarizer.services.research_feed.telegram",
                 "zotero_summarizer.services.model.reranker",
                 "zotero_summarizer.services.llm.factory"):
        module = sys.modules.get(name)
        if module is not None:
            path = Path(module.__file__).resolve()
            assert path.is_relative_to(root)
            record_property(name, f"{path}:{hashlib.sha256(path.read_bytes()).hexdigest()}")


@pytest.mark.parametrize("initially_ready", [False, True])
def test_each_explicit_processing_invocation_scores_after_successful_initialization(
    tmp_path, monkeypatch, capsys, initially_ready, record_property,
):
    from zotero_summarizer.services.llm import factory
    from zotero_summarizer.services.model import reranker

    posts = _posts()
    observed = []
    for number in range(2):
        settings = _project(tmp_path / str(number))
        path = settings.project_root / "inputs.jsonl"
        _write_inputs(path, posts)
        gates = StartupGates(posts, initially_ready)
        monkeypatch.setattr(reranker, "get_reranker", gates.get_reranker)
        monkeypatch.setattr(factory, "build_client_for_provider", gates.build_client)

        receipt = _run(settings.project_root, capsys, "--input-jsonl", path)
        payload, _markdown = _report(receipt)
        counts = payload["metadata"]["counts"]
        observed.append({"ready_after_command": gates.is_ready(),
                         "loads": gates.trace.count(("load",)),
                         "ml_scored": counts["ml_scored"],
                         "llm_attempts": counts["llm_attempts"]})
    (tmp_path / "startup-observations.json").write_text(json.dumps(observed, indent=2) + "\n")
    record_property("startup_observations", json.dumps(observed, sort_keys=True))

    assert observed == [{"ready_after_command": True,
                         "loads": 0 if initially_ready else 1,
                         "ml_scored": 5, "llm_attempts": 4}] * 2


class NewsEncoder:
    def __init__(self, gates):
        self.gates = gates

    def predict(self, inputs):
        query = inputs[0][0]
        keys = {post["text"]: key for key, post in self.gates.posts.items()}
        assert all(interest == query for interest, _text in inputs)
        pairs = [(keys[text], text) for _query, text in inputs]
        scores = dict(self.gates.rerank(query, pairs, top_n=len(pairs)))
        return [scores[key] for key, _text in pairs]


@pytest.mark.parametrize("outcome,failure", [
    ("delayed", None), ("delayed", RuntimeError("synthetic load failure")),
    ("delayed", TimeoutError("synthetic loader timeout")), ("timeout", None),
], ids=["delayed-ready", "load-failure", "loader-timeout-error", "wait-expired"])
def test_processing_uses_owned_future_and_keeps_nonready_states_explicit(
    tmp_path, monkeypatch, capsys, outcome, failure, record_property,
):
    from zotero_summarizer.services.llm import factory
    from zotero_summarizer.services.model import reranker

    monkeypatch.setenv("SUMMARY_TIMEOUT_SECONDS", "7")
    posts = _posts()
    settings = _project(tmp_path)
    path = settings.project_root / "inputs.jsonl"
    _write_inputs(path, posts)
    gates = ExternalGates(posts)
    load = _loader(monkeypatch, NewsEncoder(gates), outcome=outcome,
                   failure=failure, model_name=MODEL)
    assert not load.ranker.is_ready()
    monkeypatch.setattr(reranker, "get_reranker", lambda name: load.ranker)
    monkeypatch.setattr(factory, "build_client_for_provider", gates.build_client)
    receipt = _run(settings.project_root, capsys, "--input-jsonl", path)
    payload, _markdown = _report(receipt)
    counts = payload["metadata"]["counts"]
    success = outcome == "delayed" and failure is None
    assert counts["ml_scored"] == (5 if success else 0)
    assert counts["llm_attempts"] == (4 if success else 0)
    assert counts["ml_rejected"] == 0 and counts["skipped"] == 1
    assert load.future.waits == [settings.summary_timeout_seconds]
    load.pool.submit.assert_called_once()
    load.pool.shutdown.assert_called_once_with(wait=False)
    stored = _stored(settings)
    for row in payload["posts"]:
        if row["state"] == "skipped":
            continue
        evaluation = stored[row["source_id"]]["evaluation"]
        assert row["ml"] == evaluation["ml"]
        if success:
            assert row["ml"]["score"] == max(SCORES[row["source_id"]])
        elif failure is not None:
            assert row["state"] == "error" and row["ml"]["status"] == "error"
            assert evaluation["error"] == {"stage": "ml", "type": type(failure).__name__}
        else:
            assert row["state"] == "pending" and row["reason"] == "ml_loading"
            assert row["ml"]["score"] is None
            assert row["ml"]["load_wait_budget_seconds"] == settings.summary_timeout_seconds
    if not success:
        assert not any(row[0] in {"provider", "llm", "ml"} for row in gates.trace)
    if outcome == "timeout":
        assert load.future.running() and not load.future.cancelled()
        load.constructor.assert_not_called()
    else:
        load.constructor.assert_called_once_with(MODEL, max_length=512)
    record_property("load_wait_observation", json.dumps({
        "wait_budgets": load.future.waits, "future_done": load.future.done(),
        "future_cancelled": load.future.cancelled(), "ml_scored": counts["ml_scored"],
        "llm_attempts": counts["llm_attempts"],
    }, sort_keys=True))
