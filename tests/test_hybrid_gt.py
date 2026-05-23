"""Tests for services.hybrid_gt — the loader that merges derived CSV
labels with user verdicts from label_verdicts.

These tests use a tmp CSV and a tmp SQLite to keep everything hermetic.
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import pytest

from zotero_summarizer.services.golden import hybrid_gt
from zotero_summarizer.storage import repositories


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """Write a minimal golden-CSV stub with just the columns we care about."""
    fieldnames = ["item_key", "gold_priority_final", "title"]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _init_label_verdicts(db_path: Path) -> None:
    """Create just the label_verdicts table directly in ``db_path``.

    The production schema initializer ``repositories.init_db()`` operates
    on the module-level ``DB_PATH`` constant, while the verdict insert
    helper takes an explicit path. For tests we touch the file the
    helper will read.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(repositories._CREATE_LABEL_VERDICTS_TABLE)
        conn.commit()
    finally:
        conn.close()


def _seed_db(db_path: Path, verdicts: list[tuple[str, str, str, str]]) -> None:
    """Seed label_verdicts. Each tuple: (item_key, original_derived, user, comment)."""
    _init_label_verdicts(db_path)
    for item_key, derived, user, comment in verdicts:
        repositories.insert_or_update_label_verdict(
            db_path,
            item_key=item_key,
            original_derived_priority=derived,
            user_priority=user,
            comment=comment,
        )


def test_load_hybrid_labels_empty_db(tmp_path: Path):
    """No verdicts → every row carries source='derived'."""
    csv_path = tmp_path / "g.csv"
    db_path = tmp_path / "t.db"
    _write_csv(csv_path, [
        {"item_key": "K1", "gold_priority_final": "must_read", "title": "T1"},
        {"item_key": "feed:42", "gold_priority_final": "dont_read", "title": "T2"},
    ])
    _init_label_verdicts(db_path)

    out = hybrid_gt.load_hybrid_labels(csv_path, db_path)
    assert len(out) == 2
    assert out["K1"]["source"] == "derived"
    assert out["K1"]["effective_priority"] == "must_read"
    assert out["K1"]["user_priority"] is None
    assert out["feed:42"]["effective_priority"] == "dont_read"


def test_user_verdict_overrides_derived(tmp_path: Path):
    """A user verdict wins over the CSV derivation."""
    csv_path = tmp_path / "g.csv"
    db_path = tmp_path / "t.db"
    _write_csv(csv_path, [
        {"item_key": "K1", "gold_priority_final": "could_read", "title": "T1"},
    ])
    _seed_db(db_path, [("K1", "could_read", "must_read", "rated it up")])

    out = hybrid_gt.load_hybrid_labels(csv_path, db_path)
    assert out["K1"]["source"] == "user"
    assert out["K1"]["derived_priority"] == "could_read"
    assert out["K1"]["user_priority"] == "must_read"
    assert out["K1"]["effective_priority"] == "must_read"
    assert out["K1"]["comment"] == "rated it up"


def test_user_verdict_confirming_derivation(tmp_path: Path):
    """User says the same thing as the derivation — still source='user'."""
    csv_path = tmp_path / "g.csv"
    db_path = tmp_path / "t.db"
    _write_csv(csv_path, [
        {"item_key": "K1", "gold_priority_final": "must_read", "title": "T1"},
    ])
    _seed_db(db_path, [("K1", "must_read", "must_read", "")])

    out = hybrid_gt.load_hybrid_labels(csv_path, db_path)
    assert out["K1"]["source"] == "user"  # user touched it, even though same
    assert out["K1"]["effective_priority"] == "must_read"


def test_user_verdict_on_row_missing_from_csv(tmp_path: Path):
    """A user verdict on a key not yet in the CSV must still appear in the merge."""
    csv_path = tmp_path / "g.csv"
    db_path = tmp_path / "t.db"
    _write_csv(csv_path, [
        {"item_key": "K1", "gold_priority_final": "could_read", "title": "T1"},
    ])
    # In production the route always supplies the server-side derived
    # priority. For this "row not yet in CSV" case we pass an inferred
    # neutral derivation so the helper's invariant holds.
    _seed_db(db_path, [("feed:9001", "could_read", "must_read", "fresh feed row")])

    out = hybrid_gt.load_hybrid_labels(csv_path, db_path)
    assert "feed:9001" in out
    assert out["feed:9001"]["derived_priority"] is None
    assert out["feed:9001"]["effective_priority"] == "must_read"
    assert out["feed:9001"]["source"] == "user"


def test_apply_hybrid_overlays_in_place(tmp_path: Path):
    """apply_hybrid swaps gold_priority_final and tags _hybrid_source."""
    db_path = tmp_path / "t.db"
    _seed_db(db_path, [("K1", "could_read", "must_read", "")])

    rows = [
        {"item_key": "K1", "gold_priority_final": "could_read", "title": "T1"},
        {"item_key": "K2", "gold_priority_final": "dont_read", "title": "T2"},
    ]
    out = hybrid_gt.apply_hybrid(rows, db_path)

    assert out[0]["gold_priority_final"] == "must_read"
    assert out[0]["_hybrid_source"] == "user"
    assert out[1]["gold_priority_final"] == "dont_read"
    assert out[1]["_hybrid_source"] == "derived"
    # original rows untouched (shallow copy contract)
    assert rows[0]["gold_priority_final"] == "could_read"


def test_apply_hybrid_empty_db_no_change(tmp_path: Path):
    """Empty label_verdicts → every row tagged 'derived', no priority change."""
    db_path = tmp_path / "t.db"
    _init_label_verdicts(db_path)

    rows = [{"item_key": "K1", "gold_priority_final": "must_read"}]
    out = hybrid_gt.apply_hybrid(rows, db_path)
    assert out[0]["gold_priority_final"] == "must_read"
    assert out[0]["_hybrid_source"] == "derived"


def test_hybrid_summary_counts(tmp_path: Path):
    csv_path = tmp_path / "g.csv"
    db_path = tmp_path / "t.db"
    _write_csv(csv_path, [
        {"item_key": "K1", "gold_priority_final": "must_read", "title": "T1"},
        {"item_key": "K2", "gold_priority_final": "could_read", "title": "T2"},
        {"item_key": "K3", "gold_priority_final": "dont_read", "title": "T3"},
    ])
    _seed_db(db_path, [
        ("K1", "must_read", "must_read", ""),     # confirming
        ("K2", "could_read", "must_read", "up"),  # overriding
    ])

    s = hybrid_gt.hybrid_summary(csv_path, db_path)
    assert s["total_rows"] == 3
    assert s["user_verdicts"] == 2
    assert s["user_confirmed_derivation"] == 1
    assert s["user_overrode_derivation"] == 1


def test_load_hybrid_labels_missing_csv_raises(tmp_path: Path):
    db_path = tmp_path / "t.db"
    _init_label_verdicts(db_path)
    with pytest.raises(FileNotFoundError):
        hybrid_gt.load_hybrid_labels(tmp_path / "does-not-exist.csv", db_path)
