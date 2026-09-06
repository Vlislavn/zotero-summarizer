"""A review decision and its training metadata have one commit boundary."""
import csv
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pydantic import TypeAdapter

import pytest

from test_review_commit import _seed
from test_review_workflow import patched_settings  # noqa: F401
from zotero_summarizer.services.golden import goldenset, hybrid_gt
from zotero_summarizer.services.library import review, review_summary
from zotero_summarizer.services.model.classifier_inputs import load_training_inputs
from zotero_summarizer.storage import feeds as fs, repositories
from zotero_summarizer.storage.migrations import TRIAGE_MIGRATIONS, run_migrations


@pytest.mark.parametrize("phase", ["before_commit", "after_commit"])
def test_process_exit_keeps_decision_label_and_sample_together(patched_settings, phase):
    path = patched_settings
    row_id, before = _seed(path)
    csv_before = (path / "zotero-summarizer-golden.csv").read_bytes()
    script = '''
import os, sys
from pathlib import Path
from types import SimpleNamespace
from zotero_summarizer.services.library import review, review_summary
from zotero_summarizer.services.golden import label_verdicts
from zotero_summarizer.storage import repositories
root = Path(sys.argv[1])
settings = SimpleNamespace(triage_db_path=root / 'triage.db', golden_csv_path=root / 'zotero-summarizer-golden.csv')
review.get_settings = review_summary.get_settings = lambda: settings
review_summary._fetch_feed_metadata = lambda **kw: {'abstract': 'Durable full abstract'}
label_verdicts.log_committed_transition = lambda **kw: None
if sys.argv[3] == 'before_commit':
    original = repositories.upsert_label_verdict
    def crash(*args, **kwargs):
        original(*args, **kwargs)
        os._exit(23)
    repositories.upsert_label_verdict = crash
else:
    review._log_review = lambda *args: os._exit(23)
review.approve(int(sys.argv[2]))
'''

    result = subprocess.run([sys.executable, "-c", script, str(path), str(row_id), phase],
                            capture_output=True, text=True, timeout=45)

    assert result.returncode == 23, result.stderr
    assert (path / "zotero-summarizer-golden.csv").read_bytes() == csv_before
    samples = hybrid_gt.apply_hybrid([], path / "triage.db")
    with fs.open_triage_conn(path / "triage.db") as conn:
        row = dict(conn.execute("SELECT * FROM processed_feed_items WHERE id = ?", (row_id,)).fetchone())
        count = conn.execute("SELECT COUNT(*) FROM label_verdicts").fetchone()[0]
    if phase == "before_commit":
        assert row == before
        assert count == 0
        assert samples == []
    else:
        assert row["decision"] == "user_approved"
        assert count == len(samples) == 1
        assert samples[0]["gold_priority_final"] == "should_read"
        assert samples[0]["abstract"] == "Durable full abstract"


def test_missing_csv_does_not_lose_review_training_metadata(patched_settings):
    row_id, _ = _seed(patched_settings)
    csv_path = patched_settings / "zotero-summarizer-golden.csv"
    csv_path.rename(patched_settings / "saved.csv")

    assert review.approve(row_id) == {"processed_id": row_id, "state": "user_approved"}

    assert not csv_path.exists()
    assert len(hybrid_gt.apply_hybrid([], patched_settings / "triage.db")) == 1


def test_export_retraction_and_relabel_use_durable_sample(patched_settings, monkeypatch):
    path = patched_settings
    db = path / "triage.db"
    run_migrations(db, "triage", TRIAGE_MIGRATIONS)
    row_id, _ = _seed(path)
    monkeypatch.setattr(review_summary, "_fetch_feed_metadata", lambda **kw: {"abstract": "Full abstract"})
    review.approve(row_id)
    sample = hybrid_gt.apply_hybrid([], db)[0]
    key = sample["item_key"]
    repositories.insert_or_update_label_verdict(db, item_key=key, original_derived_priority="should_read",
                                               user_priority="must_read", comment="Refined verdict")
    assert hybrid_gt.apply_hybrid([], db)[0]["abstract"] == "Full abstract"
    assert hybrid_gt.apply_hybrid([], db)[0]["gold_priority_final"] == "must_read"
    (path / "zotero.sqlite").touch()
    monkeypatch.setattr(goldenset, "_pull_samples", lambda *args, **kwargs: [])
    monkeypatch.setattr(goldenset.user_labels, "reconcile_label_verdicts",
                        lambda *args: goldenset.user_labels.ReconcileCounts(0, 0, 0))
    csv_path = path / "zotero-summarizer-golden.csv"
    jsonl_path = path / "golden.jsonl"

    for _ in range(2):
        result = goldenset.export_golden_dataset(path, csv_path, jsonl_path, triage_db_path=db)
        assert result["total"] == 1
        exported = json.loads(jsonl_path.read_text())
        assert exported["abstract"] == "Full abstract"
        assert exported["gold_priority_final"] == "must_read"
        with csv_path.open() as source:
            rows = list(csv.DictReader(source))
        assert len(rows) == len(hybrid_gt.apply_hybrid(rows, db)) == 1
    repositories.delete_label_verdict(db, key)
    assert hybrid_gt.apply_hybrid(rows, db) == []
    assert hybrid_gt.load_hybrid_labels(csv_path, db) == {}


def test_competing_reviews_cannot_mix_winner_label_and_metadata(patched_settings, monkeypatch):
    path = patched_settings
    row_id, _ = _seed(path)
    barrier = Barrier(2)
    original = review.prepare_training_sample

    def prepare(*args, **kwargs):
        sample = original(*args, **kwargs)
        barrier.wait(timeout=10)
        return sample

    monkeypatch.setattr(review, "prepare_training_sample", prepare)

    def act(priority):
        try:
            review.relabel(row_id, priority)
            return priority
        except ValueError as exc:
            assert "expected" in str(exc)
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(act, ["must_read", "dont_read"]))

    assert results.count(None) == 1
    winner = next(value for value in results if value is not None)
    sample = hybrid_gt.apply_hybrid([], path / "triage.db")[0]
    assert sample["gold_priority_final"] == sample["gold_priority_inferred"] == winner
    assert sample["gold_notes"] == f"review_relabel: {winner}; from awaiting_review"


def test_durable_review_changes_training_identity_without_changing_csv(patched_settings):
    path = patched_settings
    row_id, _ = _seed(path)
    args = dict(classifier_name="logreg", corpus_db_path=path / "corpus.db", goals_config=None,
                n_folds=2, pca_dim=2, triage_db_path=path / "triage.db")
    csv_path = path / "zotero-summarizer-golden.csv"
    before = load_training_inputs(csv_path, **args)

    review.approve(row_id)
    after = load_training_inputs(csv_path, **args)

    assert after.csv_sha256 == before.csv_sha256
    assert after.sha256 != before.sha256
    assert len(after.rows) == 1
    assert load_training_inputs(csv_path, **args) == after


def test_alias_csv_row_does_not_duplicate_durable_review_sample(patched_settings):
    path = patched_settings
    row_id, _ = _seed(path)
    review.approve(row_id)
    sample = hybrid_gt.apply_hybrid([], path / "triage.db")[0]
    with fs.open_triage_conn(path / "triage.db") as conn, conn:
        conn.execute("INSERT OR REPLACE INTO feed_key_aliases(old_key, stable_feed_key) VALUES (?, ?)",
                     ("feed:42", sample["item_key"]))
    csv_row = {**sample, "item_key": "feed:42", "abstract": "Existing CSV metadata"}

    rows = hybrid_gt.apply_hybrid([csv_row], path / "triage.db")

    assert len(rows) == 1
    assert rows[0]["item_key"] == "feed:42"
    assert rows[0]["abstract"] == "Existing CSV metadata"
    assert rows[0]["_hybrid_source"] == "user"
    csv_path = path / "zotero-summarizer-golden.csv"
    goldenset._write_csv([TypeAdapter(goldenset.GoldenSample).validate_python(csv_row)], csv_path)
    exported = goldenset._write_csv([], csv_path, triage_db_path=path / "triage.db")
    assert [row.item_key for row in exported] == ["feed:42"]


def test_v7_migration_preserves_v6_verdict_and_is_repeatable(tmp_path):
    db = tmp_path / "triage.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE label_verdicts (id INTEGER PRIMARY KEY, item_key TEXT, user_priority TEXT)")
        conn.execute("INSERT INTO label_verdicts VALUES (1, 'P1', 'must_read')")
        conn.execute("CREATE TABLE schema_migrations (namespace TEXT PRIMARY KEY, version INTEGER, applied_at TEXT)")
        conn.execute("INSERT INTO schema_migrations VALUES ('triage', 6, 'before')")

    for _ in range(2):
        assert run_migrations(db, "triage", TRIAGE_MIGRATIONS) == 7
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT * FROM label_verdicts").fetchall() == [(1, "P1", "must_read", None)]
