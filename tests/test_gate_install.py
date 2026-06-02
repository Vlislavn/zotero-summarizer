"""Auto-rescore on gate install — the "Today always reflects the live model".

Covers the single shared install path (``feeds.install_gate``) that both retrain
flows converge on:
  * the swap is atomic and the slate is re-scored right after, so already-triaged
    Today rows pick up the new model without a manual ``rescore-slate`` call;
  * a post-swap rescore failure is swallowed (the gate is already live) and never
    bubbles up as a fake install/retrain failure;
  * the daemon's background retrain worker installs through the same path and
    always clears the in-progress flag.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

import zotero_summarizer.services.triage.feeds._gate as gate_mod
from zotero_summarizer.services.triage import rescore_slate as rescore_mod


class _FakeGate:
    classifier_name = "logreg"
    golden_csv_sha256 = "abc123def4567890"

    def __init__(self, sha: str = "abc123def4567890"):
        self.golden_csv_sha256 = sha
        self.training_metadata = {"n_train": 10, "oof_spearman": 0.5}


def _stub_rescore(monkeypatch, *, result=None, raises=False):
    """Patch the (lazily-imported) rescore_slate target; return its call log."""
    calls: list[int] = []

    def fake():
        calls.append(1)
        if raises:
            raise RuntimeError("boom")
        return result if result is not None else {"rescored": 3, "skipped": 0}

    monkeypatch.setattr(rescore_mod, "rescore_slate", fake)
    return calls


# --- install_gate: swap + rescore ----------------------------------------


def test_install_gate_swaps_and_rescores(monkeypatch):
    app_state = SimpleNamespace(classifier_gate=None, classifier_gate_lock=threading.Lock())
    monkeypatch.setattr(gate_mod, "get_state", lambda: app_state)
    calls = _stub_rescore(monkeypatch, result={"rescored": 3, "skipped": 1})

    gate = _FakeGate()
    result = gate_mod.install_gate(gate, reason="test")

    assert app_state.classifier_gate is gate      # atomically swapped in
    assert result == {"rescored": 3, "skipped": 1}
    assert len(calls) == 1                          # rescore fired exactly once


def test_install_gate_works_without_a_lock(monkeypatch):
    # A disabled-then-enabled edge: lifecycle never set the lock; swap direct.
    app_state = SimpleNamespace(classifier_gate=None, classifier_gate_lock=None)
    monkeypatch.setattr(gate_mod, "get_state", lambda: app_state)
    _stub_rescore(monkeypatch)
    gate = _FakeGate()
    gate_mod.install_gate(gate, reason="test")
    assert app_state.classifier_gate is gate


def test_install_gate_rescore_false_skips_rescore(monkeypatch):
    app_state = SimpleNamespace(classifier_gate=None, classifier_gate_lock=threading.Lock())
    monkeypatch.setattr(gate_mod, "get_state", lambda: app_state)
    calls = _stub_rescore(monkeypatch)

    result = gate_mod.install_gate(_FakeGate(), reason="test", rescore=False)

    assert result is None
    assert calls == []                              # rescore intentionally skipped


def test_install_gate_swallows_rescore_failure(monkeypatch):
    # The gate is already live; a rescore blow-up must NOT look like an install
    # failure — install_gate returns None and the swap still stuck.
    app_state = SimpleNamespace(classifier_gate=None, classifier_gate_lock=threading.Lock())
    monkeypatch.setattr(gate_mod, "get_state", lambda: app_state)
    calls = _stub_rescore(monkeypatch, raises=True)

    gate = _FakeGate()
    result = gate_mod.install_gate(gate, reason="test")   # must not raise

    assert result is None
    assert app_state.classifier_gate is gate
    assert len(calls) == 1


# --- daemon background retrain → install_gate -----------------------------


def test_daemon_retrain_worker_installs_and_rescores(monkeypatch, tmp_path):
    from zotero_summarizer.services.model import classifier_persistence

    new_gate = _FakeGate(sha="freshsha000000")
    monkeypatch.setattr(
        classifier_persistence, "load_or_train",
        lambda *a, **k: new_gate,
    )

    app_state = SimpleNamespace(
        classifier_gate=_FakeGate(sha="oldsha"),
        classifier_gate_lock=threading.Lock(),
        classifier_gate_training=True,
        app_state=SimpleNamespace(config=SimpleNamespace(
            classifier_gate=SimpleNamespace(n_folds=5, pca_dim=100),
        )),
    )
    monkeypatch.setattr(gate_mod, "get_state", lambda: app_state)
    monkeypatch.setattr(gate_mod, "get_settings", lambda: SimpleNamespace(
        corpus_db_path=tmp_path / "corpus.db"))
    calls = _stub_rescore(monkeypatch)

    gate_mod._gate_retrain_worker(tmp_path / "golden.csv", "logreg")

    assert app_state.classifier_gate is new_gate        # swapped to the retrained gate
    assert len(calls) == 1                               # slate rescored after swap
    assert app_state.classifier_gate_training is False   # in-progress flag cleared


def test_daemon_retrain_worker_clears_flag_and_reraises_on_train_failure(monkeypatch, tmp_path):
    from zotero_summarizer.services.model import classifier_persistence

    def boom(*a, **k):
        raise RuntimeError("train exploded")

    monkeypatch.setattr(classifier_persistence, "load_or_train", boom)
    app_state = SimpleNamespace(
        classifier_gate=_FakeGate(sha="oldsha"),
        classifier_gate_lock=threading.Lock(),
        classifier_gate_training=True,
        app_state=SimpleNamespace(config=SimpleNamespace(
            classifier_gate=SimpleNamespace(n_folds=5, pca_dim=100),
        )),
    )
    monkeypatch.setattr(gate_mod, "get_state", lambda: app_state)
    monkeypatch.setattr(gate_mod, "get_settings", lambda: SimpleNamespace(
        corpus_db_path=tmp_path / "corpus.db"))

    with pytest.raises(RuntimeError, match="train exploded"):
        gate_mod._gate_retrain_worker(tmp_path / "golden.csv", "logreg")

    # Old gate kept; flag cleared so the next tick can retry.
    assert app_state.classifier_gate.golden_csv_sha256 == "oldsha"
    assert app_state.classifier_gate_training is False
