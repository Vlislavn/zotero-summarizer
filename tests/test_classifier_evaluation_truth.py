"""Published classification metrics use the effective labels and this run's predictions."""
import csv
import json

import pytest

from zotero_summarizer.cli import main
from zotero_summarizer.models.config import GoalsConfig
from zotero_summarizer.services import _adapters, _common
from zotero_summarizer.services.model import classifier
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import repositories
from zotero_summarizer.storage.migrations import TRIAGE_MIGRATIONS, run_migrations


def _project(tmp_path, monkeypatch):
    settings = Settings.load(project_root=tmp_path)
    settings.data_dir.mkdir()
    run_migrations(settings.triage_db_path, "triage", TRIAGE_MIGRATIONS)
    rows = [
        {"item_key": key, "title": key, "abstract": "Abstract", "gold_priority_final": priority,
         "gold_signal_strength": strength, "cls_logreg_priority": "must_read",
         "cls_logreg_split": "cv", "cls_test_priority": "must_read"}
        for key, priority, strength in [
            ("A", "dont_read", "high"), ("B", "must_read", "high"),
            ("C", "must_read", "high"), ("D", "could_read", "low"),
        ]
    ]
    with settings.golden_csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for key, priority in [("A", "must_read"), ("B", "dont_read")]:
        _verdict(settings, key, priority)
    config = GoalsConfig(relevance_scale={i: str(i) for i in range(1, 6)}, llm={
        "draft_model": "test", "refine_model": "test",
        "api_base": "http://localhost", "api_key_env": "TEST_CLASSIFIER_API_KEY",
    })
    monkeypatch.setattr(_common, "read_config", lambda *args: config)
    return settings


def _verdict(settings, key, priority):
    repositories.insert_or_update_label_verdict(
        settings.triage_db_path, item_key=key, original_derived_priority="could_read",
        user_priority=priority, comment="",
    )


def _run_report(settings):
    return json.loads((settings.data_dir / "classifier-runs.jsonl").read_text().splitlines()[-1])


@pytest.mark.parametrize("strength", [None, "high"])
@pytest.mark.parametrize("edit_during_fit", [False, True])
def test_classify_scores_the_training_snapshot_not_raw_or_later_truth(
    tmp_path, monkeypatch, strength, edit_during_fit,
):
    settings = _project(tmp_path, monkeypatch)

    def cross_validate(rows, **kwargs):
        assert [r["gold_priority_final"] for r in rows] == [
            "must_read", "dont_read", "must_read", "could_read",
        ]
        if edit_during_fit:
            _verdict(settings, "A", "could_read")
        return classifier.ClassifierReport(
            n_rows=2, n_positive=1, embeddings_computed=0, embeddings_cached=0,
            auc=1.0, elapsed_seconds=0.1, item_keys=["D", "A"],
            cv_probabilities=[0.9, 0.9], cv_predictions=["must_read", "must_read"],
            holdout_n_rows=1, holdout_n_positive=0, holdout_item_keys=["B"],
            holdout_probabilities=[0.1], holdout_predictions=["dont_read"],
        )

    monkeypatch.setattr(classifier, "cross_validate", cross_validate)
    args = ["goldenset", "classify", "--project-root", str(tmp_path)]
    if strength:
        args += ["--strength", strength]
    assert main(args) == 0

    report = _run_report(settings)
    cv = report["cv"]["metrics_vs_gold"]
    assert cv["total"] == (1 if strength else 2), "stale C prediction must not enter this run"
    assert cv["accuracy"] == (1.0 if strength else 0.5)
    assert cv["per_class"]["must_read"]["true_positive"] == 1
    holdout = report["holdout"]["metrics_vs_gold"]
    assert holdout["total"] == 1
    assert holdout["accuracy"] == 1.0
    assert holdout["confusion"][3][3] == 1
    persisted = _common.load_golden_rows(settings.golden_csv_path)
    assert [r["gold_priority_final"] for r in persisted] == [
        "dont_read", "must_read", "must_read", "could_read",
    ], "publishing predictions must not overwrite source labels"
    assert persisted[0]["cls_logreg_priority"] == "must_read"
    assert persisted[1]["cls_logreg_split"] == "holdout"


def test_llm_metrics_use_effective_snapshot_and_only_current_limited_results(tmp_path, monkeypatch):
    settings = _project(tmp_path, monkeypatch)
    monkeypatch.setenv("TEST_CLASSIFIER_API_KEY", "test-only")

    class Client:
        def pydantic_prompt(self, *, prompt, pydantic_model):
            _verdict(settings, "A", "could_read")
            return pydantic_model(
                priority="must_read" if "Title: A\n" in prompt else "dont_read",
                confidence=0.9, rationale="test",
            )

    monkeypatch.setattr(_adapters, "build_llm", lambda *args, **kwargs: Client())
    assert main([
        "goldenset", "classify-llm", "--project-root", str(tmp_path),
        "--classifier-name", "test", "--api-key-env", "TEST_CLASSIFIER_API_KEY",
        "--limit", "2", "--workers", "1",
    ]) == 0

    report = _run_report(settings)
    metrics = report["cv"]["metrics_vs_gold"]
    assert report["rows_processed"] == 2
    assert metrics["total"] == 2, "old C/D predictions are not results of this limited run"
    assert metrics["accuracy"] == 1.0
    assert metrics["binary"]["f1"] == 1.0
    persisted = _common.load_golden_rows(settings.golden_csv_path)
    assert persisted[0]["gold_priority_final"] == "dont_read"
    assert persisted[1]["cls_test_priority"] == "dont_read"
