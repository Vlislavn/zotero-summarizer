"""Reuse depends on training inputs, not just CSV bytes."""
import hashlib
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from zotero_summarizer.models.config import GoalsConfig
from zotero_summarizer.services.model import classifier_embed, classifier_persistence as cp
from zotero_summarizer.services.model import classifier_inputs, classifier_training
from zotero_summarizer.storage import repositories
from test_classifier_persistence import _fake_embeddings_batch, _write_golden_csv
from test_hybrid_gt import _seed_outcome


@pytest.mark.parametrize("change", ["folds", "pca", "goals", "corpus_config", "prestige_config"])
def test_same_csv_changed_training_inputs_retrain(tmp_path, monkeypatch, change):
    golden = tmp_path / "golden.csv"
    _write_golden_csv(golden)
    monkeypatch.setattr(classifier_embed, "compute_embeddings_batch", _fake_embeddings_batch)
    config = GoalsConfig(relevance_scale={i: str(i) for i in range(1, 6)}, llm={
        "draft_model": "test", "refine_model": "test",
        "api_base": "http://localhost", "api_key_env": "TEST_KEY",
    })
    config.corpus.enabled = False
    config.prestige.enabled = False
    args = dict(classifier_name="logreg", corpus_db_path=tmp_path / "corpus.db",
                goals_config=config, output_dir=tmp_path / "models", n_folds=4, pca_dim=100)
    cp.load_or_train(golden, **args)
    if change == "folds":
        args["n_folds"] = 3
    elif change == "pca":
        args["pca_dim"] = 20
    elif change == "goals":
        config.research_goals = ["A new research direction"]
    elif change == "corpus_config":
        config.corpus.embedding_model = "changed-encoder"
    else:
        config.prestige.cache_ttl_days += 1
    retrain = Mock(return_value=object())
    monkeypatch.setattr(cp, "train_and_save", retrain)
    assert cp.load_or_train(golden, **args) is retrain.return_value
    retrain.assert_called_once()


@pytest.mark.parametrize("change", ["verdict", "outcome", "tuning", "implementation", "legacy"])
def test_other_inputs_invalidate_real_saved_model(tmp_path, monkeypatch, change):
    golden, db = tmp_path / "golden.csv", tmp_path / "triage.db"
    _write_golden_csv(golden)
    with sqlite3.connect(db) as conn:
        repositories.apply_schema(conn)
    monkeypatch.setattr(classifier_embed, "compute_embeddings_batch", _fake_embeddings_batch)
    if change == "outcome":
        golden.write_text(golden.read_text().replace("P0,", "feed:10,"))
        with sqlite3.connect(db) as conn:
            repositories.upsert_label_verdict(
                conn, item_key="feed:10", original_derived_priority="must_read",
                user_priority="must_read", comment="", source="machine_add",
            )
    args = dict(classifier_name="lightgbm", corpus_db_path=tmp_path / "corpus.db",
                goals_config=None, output_dir=tmp_path / "models", n_folds=4, triage_db_path=db)
    first = cp.load_or_train(golden, **args)
    if change == "verdict":
        with sqlite3.connect(db) as conn:
            repositories.upsert_label_verdict(
                conn, item_key="P0", original_derived_priority="must_read",
                user_priority="dont_read", comment="",
            )
    elif change == "outcome":
        _seed_outcome(db, 10, "trashed")
    elif change == "tuning":
        monkeypatch.setattr(classifier_inputs, "load_tuned_params", lambda: ({"num_leaves": 3}, 2))
    elif change == "implementation":
        monkeypatch.setattr(classifier_inputs, "_implementation_sha", lambda: "changed-code")
    else:
        del first.training_metadata["training_input_sha256"]
        cp.save_trained(first, args["output_dir"])
    retrain = Mock(return_value=object())
    monkeypatch.setattr(cp, "train_and_save", retrain)
    assert cp.load_or_train(golden, **args) is retrain.return_value
    retrain.assert_called_once()


def test_training_hashes_the_csv_it_read_not_later_edits(tmp_path, monkeypatch):
    golden = tmp_path / "golden.csv"
    _write_golden_csv(golden)
    original_sha = hashlib.sha256(golden.read_bytes()).hexdigest()
    monkeypatch.setattr(classifier_embed, "compute_embeddings_batch", _fake_embeddings_batch)
    featurize = classifier_training._featurize_training_matrix

    def edit_after_read(*args, **kwargs):
        golden.write_text(golden.read_text().replace("P0,", "new-key,"))
        return featurize(*args, **kwargs)

    monkeypatch.setattr(classifier_training, "_featurize_training_matrix", edit_after_read)
    args = dict(classifier_name="logreg", corpus_db_path=tmp_path / "corpus.db",
                goals_config=None, output_dir=tmp_path / "models", n_folds=4)
    first = cp.load_or_train(golden, **args)
    assert first.golden_csv_sha256 == original_sha
    retrain = Mock(return_value=object())
    monkeypatch.setattr(cp, "train_and_save", retrain)
    assert cp.load_or_train(golden, **args) is retrain.return_value


def test_corpus_content_not_lookup_cache_or_wal_checkpoint_drives_identity(tmp_path):
    db = tmp_path / "corpus.db"
    assert classifier_inputs._corpus_rows(db) == {"corpus_embeddings": [], "goal_embeddings": []}
    assert not db.exists()
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE corpus_embeddings (item_id TEXT PRIMARY KEY, embedding_json TEXT)")
        conn.execute("INSERT INTO corpus_embeddings VALUES ('A', '[1, 0]')")
        conn.commit()
        original = classifier_inputs._corpus_rows(db)
        conn.execute("CREATE TABLE specter2_embeddings (item_key TEXT)")
        conn.execute("INSERT INTO specter2_embeddings VALUES ('cached')")
        conn.commit()
        assert classifier_inputs._corpus_rows(db) == original
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        assert classifier_inputs._corpus_rows(db) == original
        conn.execute("UPDATE corpus_embeddings SET embedding_json='[0, 1]'")
        conn.commit()
        assert classifier_inputs._corpus_rows(db) != original


def test_dependency_version_changes_implementation_identity(monkeypatch):
    package = SimpleNamespace(metadata={"Name": "encoder"}, version="1")
    monkeypatch.setattr(classifier_inputs, "distributions", lambda: [package])
    before = classifier_inputs._implementation_sha()
    package.version = "2"
    assert classifier_inputs._implementation_sha() != before


def test_feature_and_encoder_source_changes_invalidate_identity(tmp_path, monkeypatch):
    root = tmp_path / "package" / "services" / "model"
    root.mkdir(parents=True)
    source = root / "classifier_const.py"
    source.write_text("SPECTER2_ADAPTER_NAME = 'first'\n")
    monkeypatch.setattr(classifier_inputs, "__file__", str(root / "classifier_inputs.py"))
    monkeypatch.setattr(classifier_inputs, "distributions", lambda: [])
    before = classifier_inputs._implementation_sha()
    source.write_text("SPECTER2_ADAPTER_NAME = 'second'\n")
    assert classifier_inputs._implementation_sha() != before


def test_corrupt_corpus_is_not_an_empty_identity(tmp_path):
    db = tmp_path / "corpus.db"
    db.write_text("not a SQLite database")
    with pytest.raises(sqlite3.DatabaseError):
        classifier_inputs._corpus_rows(db)


def test_daemon_uses_same_identity_and_retrains_after_verdict_only_change(tmp_path, monkeypatch):
    from zotero_summarizer.services.triage.feeds import _gate
    from zotero_summarizer.settings import Settings

    settings = Settings.load(project_root=tmp_path)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    _write_golden_csv(settings.golden_csv_path)
    with sqlite3.connect(settings.triage_db_path) as conn:
        repositories.apply_schema(conn)
    config = GoalsConfig(relevance_scale={i: str(i) for i in range(1, 6)}, llm={
        "draft_model": "test", "refine_model": "test",
        "api_base": "http://localhost", "api_key_env": "TEST_KEY",
    })
    config.corpus.enabled = False
    config.prestige.enabled = False
    config.classifier_gate.model_name = "logreg"
    gate_cfg = config.classifier_gate
    inputs = classifier_inputs.load_training_inputs(
        settings.golden_csv_path, classifier_name=gate_cfg.model_name,
        corpus_db_path=settings.corpus_db_path, goals_config=config,
        n_folds=gate_cfg.n_folds, pca_dim=gate_cfg.pca_dim, triage_db_path=settings.triage_db_path,
    )
    gate = SimpleNamespace(training_metadata={"training_input_sha256": inputs.sha256})
    state = SimpleNamespace(classifier_gate=gate, classifier_gate_training=False,
                            app_state=SimpleNamespace(config=config))
    monkeypatch.setattr(_gate, "get_state", lambda: state)
    monkeypatch.setattr(_gate, "get_settings", lambda: settings)
    thread = Mock()
    monkeypatch.setattr("threading.Thread", thread)
    assert _gate.schedule_gate_retrain_async("test") is False
    thread.assert_not_called()
    from zotero_summarizer.services.model.llm_classifier import LLMClassification, write_predictions_to_csv

    write_predictions_to_csv(settings.golden_csv_path, [
        LLMClassification("P0", "must_read", .9, "Derived output"),
    ], classifier_name="test")
    assert _gate.schedule_gate_retrain_async("prediction-write") is False
    thread.assert_not_called()
    with sqlite3.connect(settings.triage_db_path) as conn:
        repositories.upsert_label_verdict(
            conn, item_key="P0", original_derived_priority="must_read",
            user_priority="dont_read", comment="",
        )
    assert _gate.schedule_gate_retrain_async("test") is True
    assert thread.call_args.kwargs["args"] == (settings.golden_csv_path, "logreg")
    thread.return_value.start.assert_called_once()
