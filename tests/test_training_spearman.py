"""Undefined ranking diagnostics stay unavailable through training and publication."""
import asyncio
import csv
import json
from types import SimpleNamespace
from zipfile import ZipFile

import numpy as np
import pytest
from starlette.responses import JSONResponse

from zotero_summarizer.services import lifecycle
from zotero_summarizer.services.model import classifier, classifier_embed, classifier_store, model_card
from zotero_summarizer.services.model import classifier_training as training
from zotero_summarizer.services.model.golden_metrics import spearman_correlation
from zotero_summarizer.services.triage.feeds import _gate
from zotero_summarizer.settings import Settings
from test_classifier_persistence import _fake_embedding, _fake_embeddings_batch, _write_golden_csv
from test_model_archive import _model


@pytest.mark.parametrize("size", [0, 1, 2])
def test_small_sample_is_unavailable(size):
    assert spearman_correlation(np.arange(size), np.arange(size)) is None


@pytest.mark.parametrize("gold,pred", [
    ([1, 2, 3], [1, 2]), ([[1, 2, 3]], [[1, 2, 3]]),
    ([1, float("nan"), 3], [1, 2, 3]), ([1, float("inf"), 3], [1, 2, 3]),
])
def test_invalid_vectors_are_errors(gold, pred):
    with pytest.raises(ValueError):
        spearman_correlation(np.asarray(gold), np.asarray(pred))


@pytest.mark.parametrize("constant", ["labels", "predictions"])
def test_oof_and_dated_constant_vectors_are_unavailable(monkeypatch, constant):
    y = np.ones(6) if constant == "labels" else np.arange(6.0)
    X = np.zeros((6, classifier.FEATURE_DIM))
    X[:, 0] = np.arange(6.0)
    matrix = training._TrainMatrix(X, y, np.ones(6))
    monkeypatch.setattr(classifier, "_fit_predict", lambda name, X, y, test, **kw: (
        None, test[:, 0] if constant == "labels" else np.ones(len(test)),
    ))
    preds, rho = training._oof_predictions(
        "logreg", matrix, [{"item_key": key} for key in "abcdef"], n_folds=3, pca_dim=1,
    )
    assert rho is None
    assert training._dated_oof_spearman([{"days_since_added": "1"}] * 6, y, preds) == (None, 6)


@pytest.mark.parametrize("predictions,expected", [([1, 2, 3, 4], 1.0), ([2, 4, 1, 3], 0.0)])
def test_dated_spearman_preserves_defined_values(predictions, expected):
    rho, count = training._dated_oof_spearman(
        [{"days_since_added": "1"}] * 4, np.arange(4.0), np.asarray(predictions),
    )
    assert rho == expected
    assert count == 4


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_predictions_are_errors(monkeypatch, bad):
    matrix = training._TrainMatrix(np.zeros((6, classifier.FEATURE_DIM)), np.arange(6.0), np.ones(6))
    monkeypatch.setattr(classifier, "_fit_predict", lambda name, X, y, test, **kw: (
        None, np.full(len(test), bad),
    ))
    with pytest.raises(ValueError, match="finite"):
        training._oof_predictions(
            "logreg", matrix, [{"item_key": key} for key in "abcdef"], n_folds=3, pca_dim=1,
        )


def test_constant_predictions_survive_train_archive_startup_and_reports(tmp_path, monkeypatch, caplog):
    settings = Settings.load(project_root=tmp_path)
    settings.golden_csv_path.parent.mkdir(parents=True, exist_ok=True)
    _write_golden_csv(settings.golden_csv_path, n_pos=90, n_neg=90)
    with settings.golden_csv_path.open() as source:
        rows = list(csv.DictReader(source))
    for i, row in enumerate(rows):
        row["days_since_added"] = str(i + 1)
    with settings.golden_csv_path.open("w", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    monkeypatch.setattr(classifier_embed, "compute_embeddings_batch", _fake_embeddings_batch)
    monkeypatch.setattr(classifier_embed, "compute_embedding", _fake_embedding)
    monkeypatch.setattr(classifier, "_fit_predict", lambda name, X, y, test, **kw: (
        None, np.full(len(test), 3.0),
    ))
    monkeypatch.setattr(lifecycle.LOGGER, "propagate", True)
    caplog.set_level("INFO", logger="zotero_summarizer")
    trained = training.train_and_save(
        settings.golden_csv_path, classifier_name="logreg", corpus_db_path=settings.corpus_db_path,
        goals_config=None, output_dir=settings.model_dir, n_folds=3,
    )
    for key in ("oof_spearman", "oof_spearman_verified", "temporal_spearman"):
        assert trained.training_metadata[key] is None
    path = settings.model_dir / "logreg.zip"
    loaded = classifier_store.load_trained(path)
    with ZipFile(path) as archive:
        metadata = json.loads(archive.read("metadata.json"))
    json.dumps(metadata, allow_nan=False)
    assert loaded.training_metadata == trained.training_metadata
    runtime = SimpleNamespace()
    config = SimpleNamespace(classifier_gate=SimpleNamespace(enabled=True, model_name="logreg", drop_priorities=[]))
    lifecycle._init_classifier_gate(runtime, config, settings, background=False)
    monkeypatch.setattr(_gate, "get_state", lambda: runtime)
    _gate.install_gate(loaded, reason="test", rescore=False)
    monkeypatch.setattr(model_card, "state", lambda: runtime)
    response = JSONResponse(asyncio.run(model_card.model_card()))
    assert json.loads(response.body)["model"]["oof_spearman_verified"] is None
    predictions, diagnostics = classifier.predict_new_items(
        rows, [], corpus_db_path=settings.corpus_db_path, classifier_name="logreg", n_folds=3,
    )
    assert diagnostics["oof_spearman"] is None
    assert "n/a" in classifier.format_feed_predictions_markdown(predictions, diagnostics)
    assert "ρ=n/a" in caplog.text
    assert "quality=n/a" in caplog.text


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_metadata_cannot_replace_live_archive(tmp_path, bad):
    path = tmp_path / "logreg.zip"
    classifier_store.write_archive(_model("old"), path)
    before = path.read_bytes()
    invalid = _model("new")
    invalid.training_metadata["oof_spearman"] = bad
    with pytest.raises(ValueError):
        classifier_store.write_archive(invalid, path)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_zero_quality_is_not_missing():
    assert _gate._gate_quality_label({"oof_spearman": 0.0}) == "Spearman=0.000"
    assert "0.000" in classifier.format_feed_predictions_markdown([], {"oof_spearman": 0.0, "n_train": 4})
