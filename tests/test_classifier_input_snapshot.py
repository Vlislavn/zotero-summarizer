"""File provenance and model reuse have different identities."""
import hashlib
from unittest.mock import Mock

import pytest

from tests.test_classifier_evaluation_truth import _project, _run_report
from test_classifier_persistence import _fake_embeddings_batch, _write_golden_csv
from zotero_summarizer.cli import main
from zotero_summarizer.services import _adapters
from zotero_summarizer.services.golden import hybrid_gt
from zotero_summarizer.services.golden.csv_store import edit_csv
from zotero_summarizer.services.model import classifier, classifier_embed, classifier_inputs
from zotero_summarizer.services.model import classifier_persistence as cp, llm_classifier


@pytest.mark.parametrize("command", ["classify", "classify-llm"])
@pytest.mark.parametrize("concurrent_edit", [False, True])
def test_cli_hashes_the_input_snapshot_before_predictions(tmp_path, monkeypatch, command, concurrent_edit):
    settings = _project(tmp_path, monkeypatch)
    expected = hashlib.sha256(settings.golden_csv_path.read_bytes()).hexdigest()[:12]
    monkeypatch.setenv("TEST_CLASSIFIER_API_KEY", "test-only")
    overlay = hybrid_gt.apply_hybrid

    def edit_after_read(rows, db):
        if concurrent_edit:
            with edit_csv(settings.golden_csv_path) as (_, current):
                current[0]["title"] = "Edited after input was read"
        return overlay(rows, db)

    monkeypatch.setattr(hybrid_gt, "apply_hybrid", edit_after_read)
    report = classifier.ClassifierReport(
        n_rows=2, n_positive=1, embeddings_computed=0, embeddings_cached=0,
        auc=1., elapsed_seconds=.1, item_keys=["A", "B"],
        cv_probabilities=[.9, .1], cv_predictions=["must_read", "dont_read"],
    )
    monkeypatch.setattr(classifier, "cross_validate", Mock(return_value=report))
    client = Mock()
    client.pydantic_prompt.return_value = llm_classifier._LLMVerdict(priority="dont_read", confidence=.9)
    monkeypatch.setattr(_adapters, "build_llm", lambda *args, **kwargs: client)

    assert main(["goldenset", command, "--project-root", str(tmp_path)]) == 0

    assert _run_report(settings)["input_csv_sha256_prefix"] == expected
    assert hashlib.sha256(settings.golden_csv_path.read_bytes()).hexdigest()[:12] != expected


@pytest.mark.parametrize("writer", ["ml", "llm", "metadata", "formatting"])
def test_derived_outputs_do_not_retrain_a_saved_gate(tmp_path, monkeypatch, writer):
    golden = tmp_path / "golden.csv"
    _write_golden_csv(golden)
    monkeypatch.setattr(classifier_embed, "compute_embeddings_batch", _fake_embeddings_batch)
    options = dict(classifier_name="logreg", corpus_db_path=tmp_path / "corpus.db",
                   goals_config=None, output_dir=tmp_path / "models", n_folds=4)
    first = cp.load_or_train(golden, **options)
    archive = options["output_dir"] / "logreg.zip"
    before = archive.read_bytes()
    if writer == "ml":
        classifier.write_predictions_to_csv(golden, classifier.ClassifierReport(
            n_rows=1, n_positive=1, embeddings_computed=0, embeddings_cached=0,
            auc=1., elapsed_seconds=.1, item_keys=["P0"], cv_probabilities=[.9],
            cv_predictions=["must_read"],
        ), classifier_name="logreg")
    elif writer == "llm":
        llm_classifier.write_predictions_to_csv(golden, [
            llm_classifier.LLMClassification("P0", "must_read", .9, "Test"),
        ], classifier_name="test")
    elif writer == "metadata":
        with edit_csv(golden) as (fields, rows):
            fields.append("audit_note")
            fields.reverse()
            for row in rows:
                row["audit_note"] = "Not a feature"
    else:
        golden.write_bytes(golden.read_bytes() + b"\n")
    assert hashlib.sha256(golden.read_bytes()).hexdigest() != first.golden_csv_sha256
    retrain = Mock(side_effect=AssertionError("unchanged training input must reuse the artifact"))
    monkeypatch.setattr(cp, "train_and_save", retrain)

    reused = cp.load_or_train(golden, **options)

    retrain.assert_not_called()
    assert archive.read_bytes() == before
    assert reused.model_sha256 == first.model_sha256


@pytest.mark.parametrize("field", [
    "item_key", "title", "abstract", "doi", "year", "venue", "authors",
    "gold_priority_final", "gold_inferred_relevance", "gold_signal_tier",
    "annotation_count", "note_count", "in_trash", "days_since_added",
])
def test_training_columns_still_invalidate_reuse(tmp_path, field):
    golden = tmp_path / "golden.csv"
    _write_golden_csv(golden)
    options = dict(classifier_name="logreg", corpus_db_path=tmp_path / "corpus.db",
                   goals_config=None, n_folds=4, pca_dim=100)
    before = classifier_inputs.load_training_inputs(golden, **options)
    with edit_csv(golden) as (fields, rows):
        if field not in fields:
            fields.append(field)
            for row in rows:
                row[field] = ""
        rows[0][field] = "changed"
    after = classifier_inputs.load_training_inputs(golden, **options)
    assert after.sha256 != before.sha256
