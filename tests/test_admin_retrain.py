"""UI "Retrain" hot-swaps the live gate + re-scores Today (no restart needed).

The ``POST /api/admin/retrain`` worker used to train + save to disk only — the
running server kept the old in-memory gate until a manual restart + rescore.
These tests pin the new behaviour: after a successful train it installs the
fresh gate live (via the shared ``feeds.install_gate``) and surfaces the swap +
rescore counts in the job result, but only when the gate is enabled. Any failure
is recorded as terminal and re-raised; a saved model alone is not job success.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import zotero_summarizer.api.routes.admin as admin
from zotero_summarizer.services.triage import feeds


@pytest.fixture(autouse=True)
def _clean_retrain_lock(monkeypatch):
    """`retrain()` now claims `_RETRAIN_LOCK` synchronously and the worker releases
    it in its `finally`. Keep the lock clean between tests even if one asserts
    before the worker released it (the lock is non-reentrant)."""
    monkeypatch.setattr(admin, "_JOBS", {})
    yield
    if admin._RETRAIN_LOCK.locked():
        admin._RETRAIN_LOCK.release()


def _run_worker(job_id):
    """Invoke the worker the way the route does: claim the lock first (the worker
    releases it). Calling `_retrain_worker` without this would `release()` an
    unheld lock and `RuntimeError`."""
    admin._RETRAIN_LOCK.acquire()
    admin._retrain_worker(job_id, classifier_name="logreg", n_folds=5)


class _FakeTrained:
    classifier_name = "logreg"
    golden_csv_sha256 = "abc"
    t_keep = 0.5
    t_must = 0.8
    t_could = 0.3
    training_metadata = {"n_train": 12, "n_holdout": 3}


def _settings(tmp_path):
    golden = tmp_path / "golden.csv"
    golden.write_text("item_key,gold_priority_final\n", encoding="utf-8")
    return SimpleNamespace(
        golden_csv_path=golden,
        config_path=tmp_path / "goals.yaml",
        corpus_db_path=tmp_path / "corpus.db",
        triage_db_path=tmp_path / "triage.db",
        data_dir=tmp_path,
    )


def _patch_train(monkeypatch, trained):
    from zotero_summarizer.services.model import classifier_persistence
    monkeypatch.setattr(classifier_persistence, "train_and_save", lambda *a, **k: trained)


def _config(*, enabled: bool):
    return SimpleNamespace(
        classifier_gate=SimpleNamespace(enabled=enabled, model_name="logreg"),
    )


def test_retrain_hot_swaps_and_reports_rescore(monkeypatch, tmp_path):
    monkeypatch.setattr(admin, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(admin, "read_config", lambda _p: _config(enabled=True))
    trained = _FakeTrained()
    _patch_train(monkeypatch, trained)

    installed: list[object] = []

    def fake_install(gate, *, reason, rescore=True):
        installed.append(gate)
        assert reason == "ui-retrain"
        return {"rescored": 5}

    monkeypatch.setattr(feeds, "install_gate", fake_install)

    job = admin._new_job("retrain")
    _run_worker(job["job_id"])

    out = admin._JOBS[job["job_id"]]
    assert out["status"] == "succeeded"
    assert out["result"]["hot_swapped"] is True
    assert out["result"]["rescored"] == 5
    assert installed == [trained]            # the just-trained gate went live


def test_retrain_disabled_gate_does_not_swap(monkeypatch, tmp_path):
    monkeypatch.setattr(admin, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(admin, "read_config", lambda _p: _config(enabled=False))
    _patch_train(monkeypatch, _FakeTrained())

    called: list[int] = []
    monkeypatch.setattr(feeds, "install_gate",
                        lambda *a, **k: called.append(1) or {"rescored": 1})

    job = admin._new_job("retrain")
    _run_worker(job["job_id"])

    out = admin._JOBS[job["job_id"]]
    assert out["status"] == "succeeded"
    assert out["result"]["hot_swapped"] is False
    assert out["result"]["rescored"] is None
    assert called == []                      # disabled gate → disk-only, no live swap


def test_retrain_swap_failure_fails_the_job(monkeypatch, tmp_path):
    monkeypatch.setattr(admin, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(admin, "read_config", lambda _p: _config(enabled=True))
    _patch_train(monkeypatch, _FakeTrained())

    def boom(*a, **k):
        raise RuntimeError("swap exploded")

    monkeypatch.setattr(feeds, "install_gate", boom)

    job = admin._new_job("retrain")
    with pytest.raises(RuntimeError, match="swap exploded"):
        _run_worker(job["job_id"])

    out = admin._JOBS[job["job_id"]]
    assert out["status"] == "failed"
    assert "swap exploded" in out["error"]
    assert out["finished_at"] is not None
    assert not admin._RETRAIN_LOCK.locked()


def test_post_swap_rescore_failure_is_visible_without_fake_rollback(monkeypatch, tmp_path):
    import zotero_summarizer.services.triage.feeds._gate as gate_mod
    from zotero_summarizer.services.triage import rescore_slate

    monkeypatch.setattr(admin, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(admin, "read_config", lambda _p: _config(enabled=True))
    trained = _FakeTrained()
    _patch_train(monkeypatch, trained)
    runtime = SimpleNamespace(classifier_gate=object(), classifier_gate_lock=None)
    monkeypatch.setattr(gate_mod, "get_state", lambda: runtime)

    def fail():
        raise RuntimeError("slate write failed")

    monkeypatch.setattr(rescore_slate, "rescore_slate", fail)
    job = admin._new_job("retrain")
    with pytest.raises(RuntimeError, match="slate write failed"):
        _run_worker(job["job_id"])
    assert runtime.classifier_gate is trained
    assert job["status"] == "failed"
    assert "slate write failed" in job["error"]
    assert job["finished_at"] is not None
    assert not admin._RETRAIN_LOCK.locked()


@pytest.mark.parametrize("boundary", ["settings", "config", "train", "result"])
def test_worker_records_all_boundary_failures(monkeypatch, tmp_path, boundary):
    from zotero_summarizer.services.model import classifier_persistence

    monkeypatch.setattr(admin, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(admin, "read_config", lambda _p: _config(enabled=False))
    trained = _FakeTrained()
    _patch_train(monkeypatch, trained)

    def fail(*args, **kwargs):
        raise RuntimeError(f"{boundary} failed")

    if boundary == "settings":
        monkeypatch.setattr(admin, "get_settings", fail)
    elif boundary == "config":
        monkeypatch.setattr(admin, "read_config", fail)
    elif boundary == "train":
        monkeypatch.setattr(classifier_persistence, "train_and_save", fail)
    else:
        class BrokenMetadata:
            get = fail
        trained.training_metadata = BrokenMetadata()

    job = admin._new_job("retrain")
    with pytest.raises(RuntimeError, match=f"{boundary} failed"):
        _run_worker(job["job_id"])
    assert job["status"] == "failed"
    assert job["finished_at"] is not None
    assert f"{boundary} failed" in job["error"]
    assert not admin._RETRAIN_LOCK.locked()


def test_thread_start_failure_finishes_registered_job(monkeypatch):
    import asyncio

    monkeypatch.setattr(admin, "_JOBS", {})

    def fail_start(self):
        raise RuntimeError("cannot start thread")

    monkeypatch.setattr(admin.threading.Thread, "start", fail_start)
    with pytest.raises(RuntimeError, match="cannot start thread"):
        asyncio.run(admin.retrain(admin.RetrainRequest()))
    job, = admin._JOBS.values()
    assert job["status"] == "failed"
    assert job["finished_at"] is not None
    assert "cannot start thread" in job["error"]
    assert not admin._RETRAIN_LOCK.locked()


def test_background_failure_reaches_polling_and_thread_hook(monkeypatch):
    import asyncio
    import threading

    observed = []
    finished = threading.Event()

    def fail():
        raise RuntimeError("settings unavailable")

    def record_failure(args):
        observed.append(args)
        finished.set()

    monkeypatch.setattr(admin, "get_settings", fail)
    monkeypatch.setattr(threading, "excepthook", record_failure)
    response = asyncio.run(admin.retrain(admin.RetrainRequest()))
    assert finished.wait(timeout=5)
    observed[0].thread.join(timeout=5)
    job = asyncio.run(admin.get_job(response["job_id"]))
    assert job["status"] == "failed"
    assert job["finished_at"] is not None
    assert "settings unavailable" in job["error"]
    assert isinstance(observed[0].exc_value, RuntimeError)
    assert not admin._RETRAIN_LOCK.locked()
