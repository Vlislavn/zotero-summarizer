"""Review-fleet prewarm: knob resolution (config + env + fail-loud) and the enable
gate. Mirrors ``deep_review_prewarm``. Split out of ``test_review_fleet.py`` to keep
each test module focused (and under the 500-LOC cap)."""
from __future__ import annotations

import types

import pytest

from zotero_summarizer.services.library.review_fleet import prewarm


def _config(*, prewarm_k=5, enabled=True):
    return types.SimpleNamespace(
        quality_review=types.SimpleNamespace(prewarm_on_startup_k=prewarm_k, enabled=enabled),
    )


def _app_state(*, reader=object()):
    return types.SimpleNamespace(zotero_reader=reader)


@pytest.fixture(autouse=True)
def _clear_prewarm_env(monkeypatch):
    monkeypatch.delenv(prewarm._ENV_PREWARM_K, raising=False)
    yield


def test_resolve_uses_config_when_env_unset():
    assert prewarm.resolve_prewarm_k(_config(prewarm_k=3)) == 3


def test_resolve_env_supersedes_config(monkeypatch):
    monkeypatch.setenv(prewarm._ENV_PREWARM_K, "7")
    assert prewarm.resolve_prewarm_k(_config(prewarm_k=3)) == 7


def test_resolve_rejects_non_integer_env_loudly(monkeypatch):
    monkeypatch.setenv(prewarm._ENV_PREWARM_K, "lots")
    with pytest.raises(ValueError, match=prewarm._ENV_PREWARM_K):
        prewarm.resolve_prewarm_k(_config())


def test_resolve_rejects_negative_env_loudly(monkeypatch):
    monkeypatch.setenv(prewarm._ENV_PREWARM_K, "-1")
    with pytest.raises(ValueError, match=">= 0"):
        prewarm.resolve_prewarm_k(_config())


def test_schedule_runs_worker_with_resolved_k(monkeypatch):
    seen = {}
    monkeypatch.setattr(prewarm, "_prewarm_worker", lambda k: seen.update(k=k))
    monkeypatch.setattr(prewarm._flight, "run_in_background", lambda target: target())
    assert prewarm.schedule_on_startup(_config(prewarm_k=3), _app_state()) is True
    assert seen["k"] == 3


def test_schedule_skips_when_k_zero(monkeypatch):
    monkeypatch.setattr(prewarm._flight, "run_in_background", _never_spawn)
    assert prewarm.schedule_on_startup(_config(prewarm_k=0), _app_state()) is False


def test_schedule_skips_when_deep_review_disabled(monkeypatch):
    monkeypatch.setattr(prewarm._flight, "run_in_background", _never_spawn)
    assert prewarm.schedule_on_startup(_config(enabled=False), _app_state()) is False


def test_schedule_skips_without_zotero_reader(monkeypatch):
    monkeypatch.setattr(prewarm._flight, "run_in_background", _never_spawn)
    assert prewarm.schedule_on_startup(_config(), _app_state(reader=None)) is False


def test_worker_starts_fleet_with_k(monkeypatch):
    captured = {}
    monkeypatch.setattr(prewarm.fleet, "start", lambda *, top_k: captured.update(top_k=top_k))
    prewarm._prewarm_worker(4)
    assert captured == {"top_k": 4}


def test_worker_swallows_failures(monkeypatch):
    def _boom(*, top_k):
        raise RuntimeError("fleet kickoff blew up")

    monkeypatch.setattr(prewarm.fleet, "start", _boom)
    prewarm._prewarm_worker(2)  # logged + swallowed, no raise


def _never_spawn(_target):
    raise AssertionError("run_in_background must not be called when prewarm is disabled")
