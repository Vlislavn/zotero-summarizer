"""Launch-time deep-review prewarm: knob resolution, uncached-top selection, the
startup gate, and the background worker. All wiring is stubbed (no real LLM/queue).
"""
from __future__ import annotations

import types

import pytest

from zotero_summarizer.services.library import deep_review_prewarm as prewarm


def _config(*, prewarm_k=5, enabled=True, gate_enabled=True):
    return types.SimpleNamespace(
        quality_review=types.SimpleNamespace(prewarm_on_startup_k=prewarm_k, enabled=enabled),
        classifier_gate=types.SimpleNamespace(enabled=gate_enabled),
    )


def _app_state(*, gate=object(), reader=object()):
    return types.SimpleNamespace(classifier_gate=gate, zotero_reader=reader)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(prewarm._ENV_PREWARM_K, raising=False)
    yield


# --- resolve_prewarm_k: config default + env override, fail-loud on garbage --------


def test_resolve_uses_config_when_env_unset():
    assert prewarm.resolve_prewarm_k(_config(prewarm_k=3)) == 3


def test_resolve_env_supersedes_config(monkeypatch):
    monkeypatch.setenv(prewarm._ENV_PREWARM_K, "7")
    assert prewarm.resolve_prewarm_k(_config(prewarm_k=3)) == 7


def test_resolve_env_zero_disables(monkeypatch):
    monkeypatch.setenv(prewarm._ENV_PREWARM_K, "0")
    assert prewarm.resolve_prewarm_k(_config(prewarm_k=5)) == 0


def test_resolve_blank_env_falls_back_to_config(monkeypatch):
    monkeypatch.setenv(prewarm._ENV_PREWARM_K, "   ")
    assert prewarm.resolve_prewarm_k(_config(prewarm_k=4)) == 4


def test_resolve_rejects_non_integer_env_loudly(monkeypatch):
    monkeypatch.setenv(prewarm._ENV_PREWARM_K, "lots")
    with pytest.raises(ValueError, match=prewarm._ENV_PREWARM_K):
        prewarm.resolve_prewarm_k(_config())


def test_resolve_rejects_negative_env_loudly(monkeypatch):
    monkeypatch.setenv(prewarm._ENV_PREWARM_K, "-1")
    with pytest.raises(ValueError, match=">= 0"):
        prewarm.resolve_prewarm_k(_config())


# --- _select_uncached_top: top-k by queue order, MINUS already-cached -------------


def test_select_drops_cached_and_keeps_rank_order(monkeypatch):
    monkeypatch.setattr(
        prewarm.reading_queue, "build_reading_queue",
        lambda **_k: {"items": [{"item_key": "A"}, {"item_key": "B"}, {"item_key": "C"}]},
    )
    monkeypatch.setattr(
        prewarm.deep_review, "cached_review_keys",
        lambda: {"B"},  # B already done (one cache read, not one-per-row)
    )
    assert prewarm._select_uncached_top(3) == ["A", "C"]


def test_select_respects_top_k_slice(monkeypatch):
    monkeypatch.setattr(
        prewarm.reading_queue, "build_reading_queue",
        lambda **_k: {"items": [{"item_key": k} for k in ("A", "B", "C", "D", "E")]},
    )
    monkeypatch.setattr(prewarm.deep_review, "cached_review_keys", set)
    assert prewarm._select_uncached_top(2) == ["A", "B"]  # only the top-2 considered


def test_select_empty_queue_yields_nothing(monkeypatch):
    monkeypatch.setattr(prewarm.reading_queue, "build_reading_queue", lambda **_k: {"items": []})
    monkeypatch.setattr(prewarm.deep_review, "cached_review_keys", set)
    assert prewarm._select_uncached_top(5) == []


# --- schedule_on_startup: the enable gate -----------------------------------------


def test_schedule_runs_worker_with_resolved_k(monkeypatch):
    seen = {}
    monkeypatch.setattr(prewarm, "_prewarm_worker", lambda k, c, a: seen.update(k=k))
    monkeypatch.setattr(prewarm, "run_in_background", lambda target: target())  # inline
    assert prewarm.schedule_on_startup(_config(prewarm_k=3), _app_state()) is True
    assert seen["k"] == 3


def test_schedule_skips_when_k_zero(monkeypatch):
    monkeypatch.setattr(prewarm, "run_in_background", _never_spawn)
    assert prewarm.schedule_on_startup(_config(prewarm_k=0), _app_state()) is False


def test_schedule_skips_when_deep_review_disabled(monkeypatch):
    monkeypatch.setattr(prewarm, "run_in_background", _never_spawn)
    assert prewarm.schedule_on_startup(_config(enabled=False), _app_state()) is False


def test_schedule_skips_without_zotero_reader(monkeypatch):
    monkeypatch.setattr(prewarm, "run_in_background", _never_spawn)
    assert prewarm.schedule_on_startup(_config(), _app_state(reader=None)) is False


def _never_spawn(_target):
    raise AssertionError("run_in_background must not be called when prewarm is disabled")


# --- _prewarm_worker: select -> deep_review.start, best-effort boundary ------------


def test_worker_starts_review_for_missing_keys(monkeypatch):
    monkeypatch.setattr(prewarm, "_wait_for_gate_ready", lambda c, a: None)
    monkeypatch.setattr(prewarm, "_select_uncached_top", lambda k: ["A", "C"])
    captured = {}
    monkeypatch.setattr(prewarm.deep_review, "start", lambda **kw: captured.update(kw))
    prewarm._prewarm_worker(5, _config(), _app_state())
    assert captured == {"item_keys": ["A", "C"]}


def test_worker_noop_when_all_cached(monkeypatch):
    monkeypatch.setattr(prewarm, "_wait_for_gate_ready", lambda c, a: None)
    monkeypatch.setattr(prewarm, "_select_uncached_top", lambda k: [])

    def _boom(**_kw):
        raise AssertionError("deep_review.start must not run with nothing to warm")

    monkeypatch.setattr(prewarm.deep_review, "start", _boom)
    prewarm._prewarm_worker(5, _config(), _app_state())  # must not raise


def test_worker_swallows_failures(monkeypatch):
    """The background boundary keeps a queue/build failure from crashing the app."""
    monkeypatch.setattr(prewarm, "_wait_for_gate_ready", lambda c, a: None)

    def _explode(_k):
        raise RuntimeError("queue build blew up")

    monkeypatch.setattr(prewarm, "_select_uncached_top", _explode)
    prewarm._prewarm_worker(5, _config(), _app_state())  # logged + swallowed, no raise


# --- _wait_for_gate_ready: bounded poll, no-op when the gate is off ----------------


def test_wait_returns_immediately_when_gate_disabled(monkeypatch):
    def _no_sleep(_s):
        raise AssertionError("must not sleep when the gate is disabled")

    monkeypatch.setattr(prewarm.time, "sleep", _no_sleep)
    prewarm._wait_for_gate_ready(_config(gate_enabled=False), _app_state(gate=None))


def test_wait_polls_until_gate_loads(monkeypatch):
    app = _app_state(gate=None)
    calls = {"n": 0}

    def _sleep(_s):
        calls["n"] += 1
        app.classifier_gate = object()  # gate finishes its background retrain

    monkeypatch.setattr(prewarm.time, "sleep", _sleep)
    prewarm._wait_for_gate_ready(_config(gate_enabled=True), app)
    assert calls["n"] == 1  # polled once, then saw the gate
