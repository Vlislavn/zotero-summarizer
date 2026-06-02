"""UI "Retrain" hot-swaps the live gate + re-scores Today (no restart needed).

The ``POST /api/admin/retrain`` worker used to train + save to disk only — the
running server kept the old in-memory gate until a manual restart + rescore.
These tests pin the new behaviour: after a successful train it installs the
fresh gate live (via the shared ``feeds.install_gate``) and surfaces the swap +
rescore counts in the job result, but only when the gate is enabled, and never
lets a swap failure turn a successful retrain into a failed job.
"""
from __future__ import annotations

from types import SimpleNamespace

import zotero_summarizer.api.routes.admin as admin
from zotero_summarizer.services.triage import feeds


class _FakeTrained:
    classifier_name = "logreg"
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
    admin._retrain_worker(job["job_id"], classifier_name="logreg", n_folds=5)

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
    admin._retrain_worker(job["job_id"], classifier_name="logreg", n_folds=5)

    out = admin._JOBS[job["job_id"]]
    assert out["status"] == "succeeded"
    assert out["result"]["hot_swapped"] is False
    assert out["result"]["rescored"] is None
    assert called == []                      # disabled gate → disk-only, no live swap


def test_retrain_swap_failure_does_not_fail_the_job(monkeypatch, tmp_path):
    monkeypatch.setattr(admin, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(admin, "read_config", lambda _p: _config(enabled=True))
    _patch_train(monkeypatch, _FakeTrained())

    def boom(*a, **k):
        raise RuntimeError("swap exploded")

    monkeypatch.setattr(feeds, "install_gate", boom)

    job = admin._new_job("retrain")
    admin._retrain_worker(job["job_id"], classifier_name="logreg", n_folds=5)

    out = admin._JOBS[job["job_id"]]
    assert out["status"] == "succeeded"      # train succeeded; swap failure is non-fatal
    assert out["result"]["hot_swapped"] is False
