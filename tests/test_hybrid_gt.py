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
from zotero_summarizer.storage import feeds as feeds_storage
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
    """Create the label_verdicts + processed_feed_items tables in ``db_path``.

    The production schema initializer ``repositories.init_db()`` operates
    on the module-level ``DB_PATH`` constant, while the verdict insert
    helper takes an explicit path. For tests we touch the file the
    helper will read. The feeds table is part of the production schema
    (``apply_schema`` always creates it) and the hybrid merge reads
    resolved outcomes from it, so the hermetic DB needs it too.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(repositories._CREATE_LABEL_VERDICTS_TABLE)
        feeds_storage.init_feeds_schema(conn)
        conn.commit()
    finally:
        conn.close()


def _seed_outcome(db_path: Path, feed_item_id: int, outcome: str) -> None:
    """Insert one materialized processed_feed_items row with a resolved outcome."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            INSERT INTO processed_feed_items
                (feed_library_id, feed_item_id, guid, title, decision, run_id,
                 materialized_zotero_key, outcome_eligible_at, outcome_detected_at,
                 final_outcome)
            VALUES (1, ?, ?, 'T', 'selected', 'r1', 'ZKEY0001',
                    datetime('now'), datetime('now'), ?)
            """,
            (feed_item_id, f"guid-{feed_item_id}", outcome),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_machine_add(db_path: Path, item_key: str, priority: str = "should_read") -> None:
    """Seed the provisional verdict the Today 'Add to library' path writes."""
    repositories.insert_or_update_label_verdict(
        db_path,
        item_key=item_key,
        original_derived_priority="could_read",
        user_priority=priority,
        comment="added from Today",
        source="machine_add",
    )


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


# ---------------------------------------------------------------------------
# Outcome correction of provisional machine adds (June 2026).
# ---------------------------------------------------------------------------


def _feed_row(feed_item_id: int, tier: str = "feed_interest") -> dict[str, str]:
    return {
        "item_key": f"feed:{feed_item_id}",
        "gold_priority_final": "should_read",
        "gold_inferred_relevance": "4.0",
        "gold_signal_tier": tier,
        "title": "T",
    }


def test_machine_add_kept_inbox_demotes_to_could_read(tmp_path: Path):
    """Added-then-untouched: the resolved outcome corrects the provisional label."""
    db_path = tmp_path / "t.db"
    _init_label_verdicts(db_path)
    _seed_machine_add(db_path, "feed:10")
    _seed_outcome(db_path, 10, "kept_inbox")

    out = hybrid_gt.apply_hybrid([_feed_row(10)], db_path)
    assert out[0]["gold_priority_final"] == "could_read"
    assert float(out[0]["gold_inferred_relevance"]) == pytest.approx(8 / 3)
    assert out[0]["_hybrid_source"] == "outcome"
    assert out[0]["gold_signal_tier"] == "feed_interest|outcome_kept_inbox"


def test_machine_add_trashed_outcome_becomes_dont_read(tmp_path: Path):
    db_path = tmp_path / "t.db"
    _init_label_verdicts(db_path)
    _seed_machine_add(db_path, "feed:11")
    _seed_outcome(db_path, 11, "trashed")

    out = hybrid_gt.apply_hybrid([_feed_row(11)], db_path)
    assert out[0]["gold_priority_final"] == "dont_read"
    assert float(out[0]["gold_inferred_relevance"]) == 1.0
    assert out[0]["_hybrid_source"] == "outcome"


def test_machine_add_engaged_outcome_is_demote_only(tmp_path: Path):
    """Positive outcomes never promote. An unchecked add caps at could_read (3.0),
    so even an `engaged` outcome (mapped 5.0) holds flat at could_read — the real
    promotion comes from the separate full-weight Zotero engagement export."""
    db_path = tmp_path / "t.db"
    _init_label_verdicts(db_path)
    _seed_machine_add(db_path, "feed:12")
    _seed_outcome(db_path, 12, "engaged")

    out = hybrid_gt.apply_hybrid([_feed_row(12)], db_path)
    assert out[0]["gold_priority_final"] == "could_read"
    assert float(out[0]["gold_inferred_relevance"]) == 3.0
    assert out[0]["_hybrid_source"] == "outcome"


def test_machine_add_pending_window_is_weak_could_read(tmp_path: Path):
    """No resolved outcome yet → the UNCHECKED provisional add resolves to a weak
    could_read (3.0), not the should_read the add stamps for display intent."""
    db_path = tmp_path / "t.db"
    _init_label_verdicts(db_path)
    _seed_machine_add(db_path, "feed:13")

    out = hybrid_gt.apply_hybrid([_feed_row(13)], db_path)
    assert out[0]["gold_priority_final"] == "could_read"
    assert float(out[0]["gold_inferred_relevance"]) == 3.0
    assert out[0]["_hybrid_source"] == "machine_add"
    assert out[0]["gold_signal_tier"] == "feed_interest"  # no suffix


def test_machine_add_unknown_outcome_carries_no_evidence(tmp_path: Path):
    """`unknown` is a key-resolution failure, not behaviour → no correction; the
    unchecked add stays at its weak could_read floor."""
    db_path = tmp_path / "t.db"
    _init_label_verdicts(db_path)
    _seed_machine_add(db_path, "feed:14")
    _seed_outcome(db_path, 14, "unknown")

    out = hybrid_gt.apply_hybrid([_feed_row(14)], db_path)
    assert out[0]["gold_priority_final"] == "could_read"
    assert out[0]["_hybrid_source"] == "machine_add"


def test_future_outcome_value_is_ignored_not_crashing(tmp_path: Path):
    """A taxonomy value this code has never seen must not correct or crash."""
    db_path = tmp_path / "t.db"
    _init_label_verdicts(db_path)
    _seed_machine_add(db_path, "feed:15")
    _seed_outcome(db_path, 15, "archived_v2")

    out = hybrid_gt.apply_hybrid([_feed_row(15)], db_path)
    assert out[0]["gold_priority_final"] == "could_read"
    assert out[0]["_hybrid_source"] == "machine_add"
    assert hybrid_gt.outcome_correction("should_read", "archived_v2") is None


def test_explicit_user_verdict_wins_over_outcome(tmp_path: Path):
    """A deliberate relabel (source='user') is never outcome-corrected."""
    db_path = tmp_path / "t.db"
    _init_label_verdicts(db_path)
    _seed_db(db_path, [("feed:16", "could_read", "must_read", "read it, great")])
    _seed_outcome(db_path, 16, "trashed")

    out = hybrid_gt.apply_hybrid([_feed_row(16)], db_path)
    assert out[0]["gold_priority_final"] == "must_read"
    assert out[0]["_hybrid_source"] == "user"


def test_outcome_correction_generalizes_across_priorities(tmp_path: Path):
    """The demote-only min() rule holds for any provisional band, not just
    the should_read adds observed in production."""
    assert hybrid_gt.outcome_correction("must_read", "kept_inbox") == (
        "could_read", pytest.approx(8 / 3),
    )
    assert hybrid_gt.outcome_correction("could_read", "moved_collection") == (
        "could_read", 3.0,  # min(3.0, 3.67) — never promoted
    )
    assert hybrid_gt.outcome_correction("dont_read", "engaged") == (
        "dont_read", 1.0,  # demote-only: even a positive outcome can't promote
    )


def test_trash_then_add_csv_tier_does_not_block_correction(tmp_path: Path):
    """Correction keys on the verdict's source, never the CSV tier: a paper
    trashed earlier (CSV tier=feed_user_label) then re-added keeps working."""
    db_path = tmp_path / "t.db"
    _init_label_verdicts(db_path)
    _seed_machine_add(db_path, "feed:17")
    _seed_outcome(db_path, 17, "kept_inbox")

    out = hybrid_gt.apply_hybrid([_feed_row(17, tier="feed_user_label")], db_path)
    assert out[0]["gold_priority_final"] == "could_read"
    assert out[0]["gold_signal_tier"] == "feed_user_label|outcome_kept_inbox"


def test_hybrid_summary_separates_machine_adds(tmp_path: Path):
    """Provisional adds no longer inflate the user_verdicts count."""
    csv_path = tmp_path / "g.csv"
    db_path = tmp_path / "t.db"
    _write_csv(csv_path, [
        {"item_key": "K1", "gold_priority_final": "must_read", "title": "T1"},
        {"item_key": "feed:20", "gold_priority_final": "should_read", "title": "T2"},
        {"item_key": "feed:21", "gold_priority_final": "should_read", "title": "T3"},
    ])
    _seed_db(db_path, [("K1", "must_read", "must_read", "")])
    _seed_machine_add(db_path, "feed:20")
    _seed_machine_add(db_path, "feed:21")
    _seed_outcome(db_path, 21, "kept_inbox")

    s = hybrid_gt.hybrid_summary(csv_path, db_path)
    assert s["user_verdicts"] == 1
    assert s["machine_provisional"] == 1
    assert s["outcome_corrected"] == 1

    merged = hybrid_gt.load_hybrid_labels(csv_path, db_path)
    assert merged["feed:20"]["source"] == "machine_add"
    # Unchecked add → weak could_read (downgraded from should_read display intent).
    assert merged["feed:20"]["effective_priority"] == "could_read"
    assert merged["feed:21"]["source"] == "outcome"
    assert merged["feed:21"]["effective_priority"] == "could_read"


def test_load_user_verdicts_is_uncapped(tmp_path: Path):
    """Regression: the paged reader's 500-row default cap silently dropped
    the oldest verdicts from training once the table outgrew it."""
    db_path = tmp_path / "t.db"
    _init_label_verdicts(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executemany(
            "INSERT INTO label_verdicts"
            " (item_key, original_derived_priority, user_priority, comment, created_at, source)"
            " VALUES (?, 'could_read', 'should_read', '', ?, 'user')",
            [(f"K{i}", f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}") for i in range(620)],
        )
        conn.commit()
    finally:
        conn.close()

    verdicts = hybrid_gt.load_user_verdicts(db_path)
    assert len(verdicts) == 620
