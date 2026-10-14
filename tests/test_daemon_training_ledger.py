"""Daemon producer regression; synthetic trainer only, no fitting or inference."""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from zotero_summarizer.services import run_log
from zotero_summarizer.services.model import classifier_inputs, classifier_persistence
from zotero_summarizer.services.triage.feeds import _gate
from zotero_summarizer.settings import Settings


@pytest.fixture
def daemon(monkeypatch, tmp_path):
    settings = Settings.load(project_root=tmp_path)
    settings.data_dir.mkdir()
    settings.golden_csv_path.write_text(
        "item_key,title,abstract,gold_priority_final\nA,Synthetic,Fixture,must_read\n",
    )
    config = SimpleNamespace(classifier_gate=SimpleNamespace(n_folds=3, pca_dim=20))
    old = SimpleNamespace(classifier_name="old", golden_csv_sha256="old")
    state = SimpleNamespace(
        classifier_gate=old, classifier_gate_training=True, classifier_gate_error="prior failure",
        classifier_gate_lock=threading.Lock(), app_state=SimpleNamespace(config=config),
    )
    monkeypatch.setattr(_gate, "get_state", lambda: state)
    monkeypatch.setattr(_gate, "get_settings", lambda: settings)
    monkeypatch.setattr(_gate, "_rescore_slate_after_swap", lambda reason: {"rescored": 0})
    monkeypatch.setattr(classifier_inputs, "_implementation_sha", lambda: "synthetic-code")
    captured = []

    def trainer(golden_csv, **options):
        assert options["classifier_name"] == "logreg"
        assert options["triage_db_path"] == settings.triage_db_path
        assert options["output_dir"] == settings.model_dir
        # Same reader used by the real trainer, without ML or a real label store.
        inputs = classifier_inputs.load_training_inputs(
            golden_csv, classifier_name=options["classifier_name"],
            corpus_db_path=options["corpus_db_path"], goals_config=None,
            n_folds=options["n_folds"], pca_dim=options["pca_dim"],
        )
        captured.append(inputs)
        return SimpleNamespace(
            classifier_name="logreg", golden_csv_sha256=inputs.csv_sha256,
            model_sha256=hashlib.sha256(b"synthetic artifact, not a model").hexdigest(),
            training_metadata={
                "training_input_sha256": inputs.sha256, "n_train": len(inputs.rows),
                "oof_spearman": None, "temporal_spearman": None,
                "oof_metrics_vs_gold": {"total": 1, "accuracy": 0.0},
                "feature_reference_year": inputs.feature_reference_year,
                "fit_options": {"lgbm_params": {}, "pca_specter_dim": None},
            },
        )

    monkeypatch.setattr(classifier_persistence, "train_and_save", trainer)
    return SimpleNamespace(settings=settings, state=state, old=old, captured=captured, trainer=trainer)


def read_entry(daemon):
    lines = daemon.settings.classifier_runs_log_path.read_text().splitlines()
    assert len(lines) == 1
    return json.loads(lines[0])


def test_successful_daemon_training_records_bound_identity(daemon):
    _gate._gate_retrain_worker(daemon.settings.golden_csv_path, "logreg")

    entry = read_entry(daemon)
    assert entry["type"] == "train_artifact"
    assert entry["producer"] == "daemon-retrain"
    assert entry["identity_scope"] == "gate_training"
    assert entry["classifier"] == "logreg"
    assert entry["training_metadata"] == daemon.state.classifier_gate.training_metadata
    assert entry["cv"] == {"n_rows": 1, "auc": None, "metrics_vs_gold": {"total": 1, "accuracy": 0.0}}
    for field, expected in (
        ("golden_csv", daemon.captured[0].csv_sha256),
        ("training_input", daemon.captured[0].sha256),
        ("model", daemon.state.classifier_gate.model_sha256),
    ):
        assert entry["identities"][field] == {"value": expected, "scope": "sha256_full", "reason": None}
    for field in ("source", "config", "seed", "historical_inference_model"):
        assert entry["identities"][field]["value"] is None
        assert entry["identities"][field]["reason"]
    assert entry["run_id"] != daemon.captured[0].sha256
    assert daemon.state.classifier_gate_training is False
    assert daemon.state.classifier_gate_error == ""


def test_ledger_does_not_hash_concurrently_edited_live_inputs(daemon, monkeypatch):
    def edit_during_training(*args, **kwargs):
        gate = daemon.trainer(*args, **kwargs)
        daemon.settings.golden_csv_path.write_text("item_key,title\nB,Later input\n")
        daemon.state.app_state.config.classifier_gate.n_folds = 9
        return gate

    monkeypatch.setattr(classifier_persistence, "train_and_save", edit_during_training)
    _gate._gate_retrain_worker(daemon.settings.golden_csv_path, "logreg")
    entry = read_entry(daemon)
    assert entry["identities"]["golden_csv"]["value"] == daemon.captured[0].csv_sha256
    assert entry["identities"]["training_input"]["value"] == daemon.captured[0].sha256
    assert entry["identities"]["golden_csv"]["value"] != hashlib.sha256(
        daemon.settings.golden_csv_path.read_bytes(),
    ).hexdigest()
    assert entry["identities"]["config"]["value"] is None


@pytest.mark.parametrize("stage", ["training", "rescore", "append", "serialization"])
def test_failures_leave_truthful_gate_and_no_false_success(daemon, monkeypatch, stage):
    def fail(*args, **kwargs):
        raise OSError(stage + " failed")

    if stage == "training":
        monkeypatch.setattr(classifier_persistence, "train_and_save", fail)
    elif stage == "rescore":
        monkeypatch.setattr(_gate, "_rescore_slate_after_swap", fail)
    elif stage == "append":
        monkeypatch.setattr(run_log, "append_run", fail)
    else:
        def invalid_report(*args, **kwargs):
            gate = daemon.trainer(*args, **kwargs)
            gate.training_metadata["invalid"] = object()
            return gate
        monkeypatch.setattr(classifier_persistence, "train_and_save", invalid_report)

    with pytest.raises((OSError, TypeError)):
        _gate._gate_retrain_worker(daemon.settings.golden_csv_path, "logreg")
    assert daemon.state.classifier_gate_training is False
    assert daemon.state.classifier_gate_error != ""
    assert daemon.state.classifier_gate_error != "prior failure"
    assert (daemon.state.classifier_gate is daemon.old) is (stage == "training")
    assert not daemon.settings.classifier_runs_log_path.exists() or not daemon.settings.classifier_runs_log_path.read_text()


def test_missing_and_legacy_identity_is_not_zero_or_full_hash(daemon, monkeypatch):
    def legacy(*args, **kwargs):
        gate = daemon.trainer(*args, **kwargs)
        gate.golden_csv_sha256 = "a" * 12
        del gate.model_sha256
        gate.training_metadata = {}
        return gate

    monkeypatch.setattr(classifier_persistence, "train_and_save", legacy)
    _gate._gate_retrain_worker(daemon.settings.golden_csv_path, "logreg")
    entry = read_entry(daemon)
    assert entry["cv"] == {"n_rows": None, "auc": None, "metrics_vs_gold": None}
    assert entry["identities"]["golden_csv"]["scope"] == "sha256_prefix"
    assert entry["identities"]["model"]["value"] is None
    assert entry["identities"]["training_input"]["value"] is None


@pytest.mark.parametrize("stage", ["training", "append"])
def test_cancellation_clears_flag_and_records_error_without_success(daemon, monkeypatch, stage):
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt("synthetic cancellation")

    if stage == "training":
        monkeypatch.setattr(classifier_persistence, "train_and_save", interrupt)
    else:
        monkeypatch.setattr(run_log, "append_run", interrupt)
    with pytest.raises(KeyboardInterrupt):
        _gate._gate_retrain_worker(daemon.settings.golden_csv_path, "logreg")
    assert daemon.state.classifier_gate_training is False
    assert (daemon.state.classifier_gate is daemon.old) is (stage == "training")
    assert daemon.state.classifier_gate_error == "KeyboardInterrupt: synthetic cancellation"
    assert not daemon.settings.classifier_runs_log_path.exists()


def test_failed_later_append_does_not_republish_prior_success(daemon, monkeypatch):
    _gate._gate_retrain_worker(daemon.settings.golden_csv_path, "logreg")
    prior = daemon.settings.classifier_runs_log_path.read_bytes()
    daemon.state.classifier_gate_training = True

    def fail(*args, **kwargs):
        raise OSError("next ledger append failed")

    monkeypatch.setattr(run_log, "append_run", fail)
    with pytest.raises(OSError, match="next ledger append failed"):
        _gate._gate_retrain_worker(daemon.settings.golden_csv_path, "logreg")
    assert daemon.settings.classifier_runs_log_path.read_bytes() == prior
    assert daemon.state.classifier_gate_error == "OSError: next ledger append failed"
    assert daemon.state.classifier_gate_training is False


def test_append_committed_then_failed_is_still_visible(daemon, monkeypatch):
    append = run_log.append_run

    def append_then_fail(*args):
        append(*args)
        raise OSError("append completion uncertain")

    monkeypatch.setattr(run_log, "append_run", append_then_fail)
    with pytest.raises(OSError, match="append completion uncertain"):
        _gate._gate_retrain_worker(daemon.settings.golden_csv_path, "logreg")
    assert read_entry(daemon)["identity_scope"] == "gate_training"
    assert daemon.state.classifier_gate is not daemon.old
    assert daemon.state.classifier_gate_error == "OSError: append completion uncertain"
    assert daemon.state.classifier_gate_training is False


def test_assigned_import_provenance():
    from zotero_summarizer.services.model import training_record

    root = Path(__file__).resolve().parents[1]
    for module, relative in (
        (_gate, "zotero_summarizer/services/triage/feeds/_gate.py"),
        (training_record, "zotero_summarizer/services/model/training_record.py"),
        (classifier_inputs, "zotero_summarizer/services/model/classifier_inputs.py"),
        (classifier_persistence, "zotero_summarizer/services/model/classifier_persistence.py"),
    ):
        assert Path(module.__file__).resolve() == root / relative


def test_nonfinite_metrics_fail_before_ledger_publication(daemon, monkeypatch):
    def nonfinite(*args, **kwargs):
        gate = daemon.trainer(*args, **kwargs)
        gate.training_metadata["oof_spearman"] = float("nan")
        return gate

    monkeypatch.setattr(classifier_persistence, "train_and_save", nonfinite)
    with pytest.raises(ValueError):
        _gate._gate_retrain_worker(daemon.settings.golden_csv_path, "logreg")
    assert daemon.state.classifier_gate is not daemon.old
    assert daemon.state.classifier_gate_training is False
    assert daemon.state.classifier_gate_error.startswith("ValueError:")
    assert not daemon.settings.classifier_runs_log_path.exists()


def test_inflight_training_has_no_ledger_until_owned_thread_drains(daemon, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def paused(*args, **kwargs):
        gate = daemon.trainer(*args, **kwargs)
        started.set()
        assert release.wait(timeout=5)
        return gate

    monkeypatch.setattr(classifier_persistence, "train_and_save", paused)
    errors = []

    def worker():
        try:
            _gate._gate_retrain_worker(daemon.settings.golden_csv_path, "logreg")
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert started.wait(timeout=5)
        assert daemon.state.classifier_gate is daemon.old
        assert daemon.state.classifier_gate_training is True
        assert not daemon.settings.classifier_runs_log_path.exists()
        daemon.settings.golden_csv_path.write_text("item_key,title\nLater,Concurrent replacement\n")
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert errors == []
    assert read_entry(daemon)["identities"]["golden_csv"]["value"] == daemon.captured[0].csv_sha256


def test_actual_training_pipeline_retains_its_pre_fit_snapshot(daemon, monkeypatch):
    import numpy as np
    from zotero_summarizer.services.model import band_calibration, classifier_training as training
    from zotero_summarizer.services.model import classifier_drift, library_features

    monkeypatch.setattr(classifier_persistence, "train_and_save", training.train_and_save)
    monkeypatch.setattr(run_log, "short_git_commit", lambda *args: "")
    load_inputs = classifier_inputs.load_training_inputs

    def capture(golden_csv, **options):
        options["goals_config"] = None
        options["triage_db_path"] = None
        inputs = load_inputs(golden_csv, **options)
        daemon.captured.append(inputs)
        return inputs

    monkeypatch.setattr(training, "load_training_inputs", capture)
    monkeypatch.setattr(training.classifier, "_filter_train_rows", lambda rows, **opts: (
        ["A"], ["Synthetic"], ["Fixture"], [4.5], rows,
    ))
    monkeypatch.setattr(library_features, "load_positive_library_from_rows", lambda *args: SimpleNamespace(n_rows=1))

    def matrix(*args, **options):
        daemon.settings.golden_csv_path.write_text("item_key,title\nLater,Changed while fitting\n")
        return training._TrainMatrix(np.zeros((1, 1)), np.asarray([4.5]), np.asarray([1.]))

    monkeypatch.setattr(training, "_featurize_training_matrix", matrix)
    monkeypatch.setattr(training, "_oof_predictions", lambda *a, **k: (np.asarray([4.5]), None))
    monkeypatch.setattr(training, "_dated_oof_spearman", lambda *a: (None, 0))
    monkeypatch.setattr(band_calibration, "fit_band_calibrator", lambda *a: (None, None))
    monkeypatch.setattr(band_calibration, "apply_band_calibration", lambda c, p: p)
    monkeypatch.setattr(training, "_oof_quality_metrics", lambda *a: {"total": 1, "accuracy": 0.0})
    monkeypatch.setattr(classifier_drift, "log_label_drift", lambda *a, **k: None)
    monkeypatch.setattr(training, "_temporal_holdout_metrics", lambda *a, **k: None)
    monkeypatch.setattr(training, "_fit_final_model", lambda *a, **k: (None, None))

    def save(trained, output_dir):
        trained.model_sha256 = hashlib.sha256(b"synthetic serialized artifact").hexdigest()

    monkeypatch.setattr(training, "save_trained", save)
    _gate._gate_retrain_worker(daemon.settings.golden_csv_path, "logreg")
    entry = read_entry(daemon)
    assert entry["identities"]["golden_csv"]["value"] == daemon.captured[0].csv_sha256
    assert entry["identities"]["training_input"]["value"] == daemon.captured[0].sha256
    assert entry["training_metadata"]["oof_spearman"] is None
    assert entry["training_metadata"]["feature_reference_year"] == daemon.captured[0].feature_reference_year
    # Admin uses this unchanged optional trainer sink, before installing the gate.
    legacy_log = daemon.settings.data_dir / "admin-synthetic.jsonl"
    training.train_and_save(
        daemon.settings.golden_csv_path, classifier_name="logreg",
        corpus_db_path=daemon.settings.corpus_db_path, goals_config=None,
        output_dir=daemon.settings.model_dir, n_folds=3, runs_log_path=legacy_log,
    )
    legacy = json.loads(legacy_log.read_text())
    assert set(legacy) == {
        "run_id", "timestamp", "git_commit", "classifier", "type", "cv", "label_drift",
        "input_csv_sha256_prefix",
    }
    assert legacy["type"] == "train_artifact"
    assert legacy["classifier"] == "logreg"
    assert legacy["cv"] == entry["cv"]
    assert legacy["input_csv_sha256_prefix"] == daemon.captured[-1].csv_sha256[:12]
