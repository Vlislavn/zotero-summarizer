"""Retraction invalidates a verdict-derived CSV label, not independent evidence."""
import asyncio
import csv
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from zotero_summarizer.api.routes import golden
from zotero_summarizer.services.golden import hybrid_gt, label_verdicts
from zotero_summarizer.services.golden.csv_store import edit_csv
from zotero_summarizer.services.library.review_summary import append_verdict_to_golden
from zotero_summarizer.services.sync import service as sync
from zotero_summarizer.storage import repositories as db


@pytest.mark.parametrize("lane", ["http", "reconcile", "offline"])
@pytest.mark.parametrize("tier", ["feed_user_label", "user_label", "feed_interest"])
def test_retraction_removes_effective_label_and_reassignment_restores_it(tmp_path, monkeypatch, lane, tier):
    path = tmp_path / "triage.db"
    csv_path = tmp_path / "golden.csv"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    monkeypatch.setattr(golden, "_db_path", lambda: path)
    monkeypatch.setattr(golden, "log_retract_event", lambda *args: None)
    monkeypatch.setattr(label_verdicts, "log_committed_transition", lambda **kw: None)
    monkeypatch.setattr(sync.verdict_effects, "apply_verdict_effects", lambda *args: {})
    with edit_csv(csv_path, create_fields=["item_key", "title", "abstract", "gold_signal_tier",
                                           "gold_priority_final", "gold_inferred_relevance"]) as _:
        pass
    # The CSV survives as metadata; its historical verdict is not fresh evidence.
    append_verdict_to_golden("P1", title="Paper", abstract="Complete abstract", priority="must_read",
                             signal_tier=tier, golden_csv_path=csv_path)
    append_verdict_to_golden("P2", title="Independent", abstract="Independent abstract", priority="could_read",
                             signal_tier="strong_positive", golden_csv_path=csv_path)
    for key in ("P1", "P2"):
        label_verdicts.set_label_verdict(path, item_key=key, user_priority="must_read",
                                         original_derived_priority="could_read", surface="test")
    original = csv_path.read_bytes()
    assert hybrid_gt.load_hybrid_labels(csv_path, path)["P1"]["source"] == "user"
    for key in ("P1", "P2"):
        if lane == "http":
            assert asyncio.run(golden.remove_verdict(key)) == {"deleted": True}
        elif lane == "reconcile":
            assert label_verdicts.retract_label_verdict(path, item_key=key, surface="zotero_reconcile")
        else:
            mutation = dict(mutation_id=str(uuid4()), device_id="test", item_key=key, field="verdict",
                            operation="delete", base_revision=db.sync_current_fields(path)[(key, "verdict")]["revision"],
                            created_at=datetime.now(timezone.utc).isoformat())
            assert sync.push(path, [mutation])["results"][0]["status"] == "applied"
    with csv_path.open(newline="") as source:
        rows = list(csv.DictReader(source))
    assert set(hybrid_gt.load_hybrid_labels(csv_path, path)) == {"P2"}
    effective = hybrid_gt.apply_hybrid(rows, path)
    assert [(row["item_key"], row["gold_priority_final"]) for row in effective] == [("P2", "could_read")]
    assert hybrid_gt.hybrid_summary(csv_path, path)["total_rows"] == 1
    assert csv_path.read_bytes() == original
    assert hybrid_gt.load_user_verdicts(path) == {}

    label_verdicts.set_label_verdict(path, item_key="P1", user_priority="dont_read", surface="test")
    assert hybrid_gt.load_hybrid_labels(csv_path, path)["P1"]["effective_priority"] == "dont_read"
    assert hybrid_gt.apply_hybrid(rows, path)[0]["gold_priority_final"] == "dont_read"


def test_retracted_stable_key_suppresses_legacy_csv_alias(tmp_path, monkeypatch):
    path = tmp_path / "triage.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    stable = "feed:g:" + "a" * 64
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO feed_key_aliases(old_key, stable_feed_key) VALUES (?, ?)", ("feed:42", stable))
    db.insert_or_update_label_verdict(path, item_key="feed:42", original_derived_priority="unknown",
                                      user_priority="could_read", comment="before migration")
    assert db.delete_label_verdict(path, "feed:42")
    db.insert_or_update_label_verdict(path, item_key=stable, original_derived_priority="unknown",
                                      user_priority="must_read", comment="")
    rows = [{"item_key": "feed:42", "gold_signal_tier": "feed_user_label|outcome_trashed",
             "gold_priority_final": "must_read"}]
    assert hybrid_gt.apply_hybrid(rows, path)[0]["_hybrid_source"] == "user"
    assert db.delete_label_verdict(path, stable)
    assert hybrid_gt.apply_hybrid(rows, path) == []


def test_standalone_csv_label_without_retraction_is_preserved(tmp_path, monkeypatch):
    path = tmp_path / "triage.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    row = {"item_key": "P1", "gold_signal_tier": "feed_user_label", "gold_priority_final": "must_read"}
    assert hybrid_gt.apply_hybrid([row], path)[0]["gold_priority_final"] == "must_read"
