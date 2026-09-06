"""The trained artifact, not its CSV, identifies cached scores and feedback."""
import hashlib
import os
from types import SimpleNamespace

import joblib
import numpy as np
import pytest

from zotero_summarizer.services import interaction_log, run_log
from zotero_summarizer.services.library import reading_queue
from zotero_summarizer.services.model import classifier_store as store
from zotero_summarizer.services.model.classifier_artifact import TrainedClassifier
from zotero_summarizer.services.model.classifier_backup import restore_snapshot, snapshot_current
from zotero_summarizer.services.model.classifier_training import save_trained


def _model():
    return TrainedClassifier(
        classifier_name="logreg", golden_csv_sha256="same-csv", feature_dim=1,
        pca_dim=0, X_train=np.array([[1.0], [2.0]]), y_train=np.array([1.0, 2.0]),
        fitted_model={"weights": [1.0]}, training_metadata={"config": "original"},
    )


@pytest.mark.parametrize("change", ["classifier", "weights", "config", "pca"])
def test_different_artifacts_invalidate_scores_and_separate_feedback(tmp_path, monkeypatch, change):
    first, second = _model(), _model()
    if change == "classifier":
        second.classifier_name = "lightgbm"
    elif change == "weights":
        second.fitted_model = {"weights": [2.0]}
    elif change == "config":
        second.training_metadata["config"] = "changed"
    else:
        second.pca_dim = 2
    save_trained(first, tmp_path)
    save_trained(second, tmp_path)
    runtime = SimpleNamespace(classifier_gate=first)
    log = tmp_path / "events.jsonl"
    monkeypatch.setattr(interaction_log, "state", lambda: runtime)
    monkeypatch.setattr(interaction_log, "settings", lambda: SimpleNamespace(interaction_log_path=log))
    monkeypatch.setattr(reading_queue, "get_state", lambda: runtime)
    monkeypatch.setattr(reading_queue, "_cache_path", lambda: tmp_path / "scores.json")
    reading_queue._write_cache(reading_queue._gate_sha(), {"PAPER": {"relevance_score": 3.0}})
    assert reading_queue.read_score_cache_with_staleness()[1] is False
    for gate in (first, second):
        runtime.classifier_gate = gate
        interaction_log.log_human_feedback(item_key="PAPER", item_key_kind="zotero", surface="test",
                                            model={}, human={"kind": "priority", "value": "must_read"})
    assert reading_queue.read_score_cache_with_staleness()[1] is True
    events = run_log.load_runs(log)
    assert events[0]["gate_sha"] != events[1]["gate_sha"]
    assert all(len(event["gate_sha"]) == 64 for event in events)
    assert first.golden_csv_sha256 == second.golden_csv_sha256


@pytest.mark.parametrize("legacy", [False, True])
def test_load_identity_matches_exact_bytes_and_survives_reload(tmp_path, legacy):
    model = _model()
    if legacy:
        path = tmp_path / "logreg.joblib"
        joblib.dump(model, path)
    else:
        path = save_trained(model, tmp_path)
        assert model.model_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    assert store.load_trained(path).model_sha256 == expected
    assert store.load_trained(path).model_sha256 == expected


def test_replacing_path_during_load_cannot_misidentify_loaded_weights(tmp_path, monkeypatch):
    old = _model()
    path = save_trained(old, tmp_path)
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    new = _model()
    new.fitted_model = {"weights": [9.0]}
    original_load = joblib.load

    def load_and_replace(source):
        store.write_archive(new, path)
        return original_load(source)

    monkeypatch.setattr(joblib, "load", load_and_replace)
    loaded = store.load_trained(path)
    assert loaded.fitted_model == old.fitted_model
    assert loaded.model_sha256 == expected
    assert loaded.model_sha256 != hashlib.sha256(path.read_bytes()).hexdigest()


def test_failed_publication_retains_previous_in_memory_identity(tmp_path, monkeypatch):
    model = _model()
    path = save_trained(model, tmp_path)
    before = model.model_sha256

    def fail(*args):
        raise OSError("publication failed")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="publication failed"):
        store.write_archive(model, path)
    assert model.model_sha256 == before
    assert store.load_trained(path).model_sha256 == before


def test_restore_recovers_snapshot_identity(tmp_path):
    first = _model()
    save_trained(first, tmp_path)
    snapshot = snapshot_current(tmp_path, "logreg")
    second = _model()
    second.fitted_model = {"weights": [2.0]}
    save_trained(second, tmp_path)
    path = restore_snapshot(tmp_path, "logreg", snapshot.name)
    assert store.load_trained(path).model_sha256 == first.model_sha256


def test_missing_gate_is_explicit_but_broken_identity_raises(monkeypatch):
    runtime = SimpleNamespace(classifier_gate=None)
    monkeypatch.setattr(interaction_log, "state", lambda: runtime)
    assert interaction_log._current_gate_sha() == ""
    runtime.classifier_gate = SimpleNamespace(golden_csv_sha256="not-a-model-id")
    with pytest.raises(AttributeError):
        interaction_log._current_gate_sha()
