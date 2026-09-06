"""Every ML CLI path supplies the same effective labels before model work."""
import json
from unittest.mock import Mock

import pytest

from tests.test_classifier_persistence import _fake_embedding, _fake_embeddings_batch, _write_golden_csv
from tests.test_hybrid_gt import _seed_outcome
from zotero_summarizer.cli import main
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.models.config import GoalsConfig
from zotero_summarizer.services import _common
from zotero_summarizer.services.golden.csv_store import edit_csv
from zotero_summarizer.services.model import classifier, classifier_embed, classifier_persistence as cp
from zotero_summarizer.services.model.eval_baseline import _featurize, _runners
from zotero_summarizer.services.setup import calibration
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import repositories as db
from zotero_summarizer.storage.migrations import TRIAGE_MIGRATIONS, run_migrations


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    settings = Settings.load(project_root=tmp_path)
    settings.data_dir.mkdir()
    run_migrations(settings.triage_db_path, "triage", TRIAGE_MIGRATIONS)
    _write_golden_csv(settings.golden_csv_path)
    with edit_csv(settings.golden_csv_path) as (_, rows):
        for key in ("feed:42", "feed:43", "feed:44"):
            rows.append({"item_key": key, "title": key, "abstract": "Complete abstract",
                         "gold_priority_final": "should_read", "gold_inferred_relevance": "4.0",
                         "gold_signal_tier": "feed_interest", "gold_signal_strength": "low"})
    for key, source in [("N0", "user"), ("feed:42", "machine_add"),
                        ("feed:43", "machine_add"), ("feed:44", "user")]:
        db.insert_or_update_label_verdict(
            settings.triage_db_path, item_key=key, original_derived_priority="could_read",
            user_priority="must_read", source=source, comment="",
        )
    _seed_outcome(settings.triage_db_path, 43, "trashed")
    assert db.delete_label_verdict(settings.triage_db_path, "feed:44")
    config = GoalsConfig(relevance_scale={i: str(i) for i in range(1, 6)}, llm={
        "draft_model": "test", "refine_model": "test",
        "api_base": "http://localhost", "api_key_env": "TEST_KEY",
    })
    config.corpus.enabled = False
    config.prestige.enabled = False
    monkeypatch.setattr(_common, "read_config", lambda *args: config)
    monkeypatch.setattr(classifier_embed, "compute_embedding", _fake_embedding)
    monkeypatch.setattr(classifier_embed, "compute_embeddings_batch", _fake_embeddings_batch)
    return settings, config


def _assert_truth(rows):
    by_key = {row["item_key"]: row for row in rows}
    assert len(rows) == 26
    assert "feed:44" not in by_key, "retracted CSV labels must not enter model work"
    for key, priority, relevance, source in [
        ("N0", "must_read", 5.0, "user"),
        ("feed:42", "could_read", 3.0, "machine_add"),
        ("feed:43", "dont_read", 1.0, "outcome"),
        ("P0", "must_read", 5.0, "derived"),
    ]:
        row = by_key[key]
        assert row["gold_priority_final"] == priority
        assert float(row["gold_inferred_relevance"]) == relevance
        assert row["_hybrid_source"] == source
    assert by_key["feed:43"]["gold_signal_tier"] == "feed_interest|outcome_trashed"


@pytest.mark.parametrize("mode", ["new", "force", "cached_raw"])
def test_train_cli_overlays_labels_and_invalidates_a_raw_csv_artifact(dataset, monkeypatch, mode):
    settings, config = dataset
    original = settings.golden_csv_path.read_bytes()
    if mode == "cached_raw":
        cp.train_and_save(
            settings.golden_csv_path, classifier_name="logreg", goals_config=config,
            corpus_db_path=settings.corpus_db_path, output_dir=settings.model_dir, n_folds=2,
        )
    select = Mock(wraps=classifier._filter_train_rows)
    monkeypatch.setattr(classifier, "_filter_train_rows", select)
    args = ["goldenset", "train-classifier", "--project-root", str(settings.project_root),
            "--classifier", "logreg", "--folds", "2"]
    if mode == "force":
        args += ["--force"]

    assert main(args) == 0

    select.assert_called_once()
    _assert_truth(select.call_args.args[0])
    trained = cp.load_trained(settings.model_dir / "logreg.zip")
    assert trained.training_metadata["n_train"] == 26
    assert settings.golden_csv_path.read_bytes() == original
    entry = json.loads((settings.data_dir / "classifier-runs.jsonl").read_text().splitlines()[-1])
    assert entry["training_metadata"]["n_train"] == 26

    select.reset_mock()
    assert main([arg for arg in args if arg != "--force"]) == 0
    select.assert_not_called()


@pytest.mark.parametrize("command", ["baseline", "learning", "tune", "calibrate"])
def test_evaluation_cli_resolves_labels_before_featurization(dataset, monkeypatch, command):
    settings, _ = dataset
    original = settings.golden_csv_path.read_bytes()
    # Stop at the expensive model boundary, after actual CLI/config/CSV/SQLite routing.
    featurize = Mock(side_effect=RuntimeError("test stops before model work"))
    monkeypatch.setattr(_featurize, "featurize_golden", featurize)
    monkeypatch.setattr(_runners, "featurize_golden", featurize)
    if command == "calibrate":
        monkeypatch.setattr(calibration, "run_full_calibration", lambda *args, **kwargs: {})
        args = ["calibrate", "--tier3", "--tier3-classifiers", "logreg", "--tier3-min-labels", "1"]
    elif command == "tune":
        args = ["goldenset", "tune", "--n-trials", "1"]
    else:
        args = ["goldenset", "eval-baseline", "--classifier", "logreg"]
        if command == "learning":
            args += ["--learning-curve"]

    with pytest.raises(RuntimeError, match="test stops before model work"):
        main([*args, "--project-root", str(settings.project_root)])

    featurize.assert_called_once()
    _assert_truth(featurize.call_args.args[0])
    assert featurize.call_args.kwargs["corpus_db_path"] == settings.corpus_db_path
    assert settings.golden_csv_path.read_bytes() == original


def test_predict_feed_cli_reaches_model_with_effective_labels_and_writes_results(dataset, monkeypatch):
    settings, _ = dataset
    original = settings.golden_csv_path.read_bytes()
    monkeypatch.setattr(ZoteroReader, "__init__", lambda self, *args: None)
    monkeypatch.setattr(ZoteroReader, "get_feed_groups", lambda self: [{"library_id": 1}])
    monkeypatch.setattr(ZoteroReader, "get_feed_items", lambda self, **kwargs: [
        {"item_id": 999, "title": "New paper", "abstract": "Unread"},
    ])
    prediction = classifier.FeedPrediction(
        item_key="feed:999", title="New paper", authors="", venue="", doi="", abstract_preview="Unread",
        raw_score=4.0, calibrated_score=4.0, predicted_priority="should_read",
    )
    predict = Mock(return_value=([prediction], {"n_train": 26, "oof_spearman": None}))
    monkeypatch.setattr(classifier, "predict_new_items", predict)
    output = settings.data_dir / "predictions.csv"

    assert main(["goldenset", "predict-feed", "--project-root", str(settings.project_root),
                 "--output", str(output), "--limit", "1"]) == 0

    predict.assert_called_once()
    _assert_truth(predict.call_args.kwargs["training_rows"])
    assert predict.call_args.kwargs["new_items"][0]["item_id"] == 999
    assert _common.load_golden_rows(output)[0]["predicted_priority"] == "should_read"
    assert settings.golden_csv_path.read_bytes() == original
