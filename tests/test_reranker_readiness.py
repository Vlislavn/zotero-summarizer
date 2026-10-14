"""Owned Future completion without weights, real executors, polling or sleeps."""
from __future__ import annotations

from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from zotero_summarizer.services.model import reranker as model


class LoadFuture(Future):
    """Delayed work completes only at result(), not an is_ready() recheck."""

    def __init__(self, outcome):
        super().__init__()
        self.outcome = outcome
        self.work = None
        self.waits = []

    def complete(self):
        try:
            self.work()
        except Exception as exc:
            self.set_exception(exc)
        else:
            self.set_result(None)

    def result(self, timeout=None):
        if not self.done():
            self.waits.append(timeout)
            assert timeout is not None, "load completion must use an explicit bounded wait"
            if self.outcome == "timeout":
                raise FutureTimeoutError("synthetic unfinished load")
            self.complete()
        return super().result(timeout=timeout)


def _loader(monkeypatch, encoder, *, outcome="delayed", failure=None, model_name="stub"):
    future = LoadFuture(outcome)
    constructor = Mock(side_effect=failure) if failure is not None else Mock(return_value=encoder)
    pool = Mock()

    def submit(work):
        future.work = work
        assert future.set_running_or_notify_cancel()
        if outcome == "instant":
            future.complete()
        return future

    pool.submit.side_effect = submit
    pool_factory = Mock(return_value=pool)
    monkeypatch.setattr(model, "CrossEncoder", constructor)
    monkeypatch.setattr(model, "ThreadPoolExecutor", pool_factory)
    return SimpleNamespace(ranker=model.Reranker(model_name), future=future,
                           constructor=constructor, pool=pool, pool_factory=pool_factory)


@pytest.mark.parametrize("outcome", ["instant", "delayed"])
def test_owned_completion_scores_and_never_submits_a_second_load(monkeypatch, outcome):
    encoder = Mock(predict=Mock(return_value=[0.1, 0.9]))
    load = _loader(monkeypatch, encoder, outcome=outcome)
    load.ranker.ensure_loaded_async()
    if outcome == "delayed":
        assert not load.ranker.is_ready() and load.ranker.is_loading()
        assert load.ranker.rerank("query", [("A", "one")], 1) == []
        assert load.constructor.call_count == 0
    assert load.ranker.wait_until_ready(timeout=0.125)
    assert load.ranker.rerank("query", [("A", "one"), ("B", "two")], 2) == [("B", 0.9), ("A", 0.1)]
    assert load.ranker.wait_until_ready(timeout=0.125)
    assert load.future.waits == ([] if outcome == "instant" else [0.125])
    load.constructor.assert_called_once_with("stub", max_length=512)
    load.pool.submit.assert_called_once()
    load.pool.shutdown.assert_called_once_with(wait=False)
    assert not load.ranker.is_loading()


@pytest.mark.parametrize("failure", [RuntimeError("synthetic load failure"),
                                     TimeoutError("synthetic loader timeout")])
def test_completed_load_failure_retains_original_error_without_retry(monkeypatch, failure):
    load = _loader(monkeypatch, Mock(), failure=failure)
    for _ in range(2):
        with pytest.raises(type(failure)) as caught:
            load.ranker.wait_until_ready(timeout=0.125)
        assert caught.value is failure
    assert load.future.waits == [0.125]
    load.constructor.assert_called_once_with("stub", max_length=512)
    load.pool.submit.assert_called_once()
    assert load.future.done() and not load.future.cancelled()
    with pytest.raises(type(failure)) as caught:
        load.ranker.is_ready()
    assert caught.value is failure


def test_wait_expiry_keeps_same_running_future_without_cancellation_or_reload(monkeypatch):
    load = _loader(monkeypatch, Mock(), outcome="timeout")
    assert not load.ranker.wait_until_ready(timeout=0.03125)
    assert load.ranker.is_loading() and not load.ranker.is_ready()
    assert load.future.running() and not load.future.cancelled()
    load.ranker.ensure_loaded_async()
    assert load.future.waits == [0.03125]
    load.pool.submit.assert_called_once()
    load.constructor.assert_not_called()
    load.future.complete()
    assert load.ranker.is_ready()
    assert load.ranker.wait_until_ready(timeout=0.03125)
    assert load.future.waits == [0.03125]
    load.constructor.assert_called_once_with("stub", max_length=512)


@pytest.mark.parametrize("timeout", [None, 0, -1, True, float("nan"), float("inf")])
def test_invalid_wait_budget_fails_before_load_submission(monkeypatch, timeout):
    load = _loader(monkeypatch, Mock())
    with pytest.raises(ValueError, match="finite and positive"):
        load.ranker.wait_until_ready(timeout=timeout)
    load.pool_factory.assert_not_called()
    load.constructor.assert_not_called()
