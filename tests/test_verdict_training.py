"""A user verdict must become a trainable golden row and survive re-export.

Covers the add-from-Today → dont_read gap: the verdict on a materialized library
item (no engagement, so no derived golden row) is appended as a full-weight row
and preserved when 'Refresh labels' regenerates the engagement-derived rows.
"""
from __future__ import annotations

import csv
import dataclasses
from pathlib import Path

import pytest

from zotero_summarizer.services.golden import goldenset
from zotero_summarizer.services.golden.goldenset import GoldenSample
from zotero_summarizer.services.library.review import append_verdict_to_golden


def _empty_golden(tmp_path: Path) -> Path:
    fields = [f.name for f in dataclasses.fields(GoldenSample)]
    p = tmp_path / "golden.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=fields).writeheader()
    return p


def _rows(path: Path) -> dict[str, dict]:
    with path.open(encoding="utf-8") as f:
        return {r["item_key"]: r for r in csv.DictReader(f)}


def test_append_verdict_writes_zotero_key_row(tmp_path):
    p = _empty_golden(tmp_path)
    assert append_verdict_to_golden(
        "ABCD1234", title="T", abstract="a", priority="dont_read", golden_csv_path=p,
    ) is True
    row = _rows(p)["ABCD1234"]
    assert row["gold_priority_final"] == "dont_read"
    assert row["gold_inferred_relevance"] == "1.0"     # dont_read → relevance 1
    assert row["gold_signal_tier"] == "feed_user_label"  # weight 0.5
    # Idempotent: a second call for the same key is a no-op (overlay covers it).
    assert append_verdict_to_golden(
        "ABCD1234", title="T", abstract="a", priority="dont_read", golden_csv_path=p,
    ) is False


def test_append_verdict_rejects_unknown_priority(tmp_path):
    p = _empty_golden(tmp_path)
    with pytest.raises(ValueError):
        append_verdict_to_golden("X", title="T", abstract="a", priority="nope", golden_csv_path=p)


def test_reexport_preserves_verdicted_zotero_row(tmp_path):
    p = _empty_golden(tmp_path)
    append_verdict_to_golden("ZKEY9999", title="Z", abstract="z", priority="dont_read", golden_csv_path=p)
    append_verdict_to_golden("feed:5", title="F", abstract="f", priority="should_read", golden_csv_path=p)

    # Re-export with fresh engagement-derived samples that do NOT include the
    # verdicted Zotero key.
    fresh = GoldenSample(
        item_key="OTHER111", title="o", authors="", year="", venue="", doi="", url="",
        abstract="o", matched_emojis="", gold_signal_tier="first_glance", note_count=0,
        annotation_count=0, collection_count=0, collections="", in_trash=False,
        days_since_added=0, gold_priority_inferred="could_read", gold_signal_strength="low",
        gold_inferred_relevance=3.0, gold_priority_final="could_read", gold_notes="",
    )
    goldenset._write_csv([fresh], p, preserve_keys=frozenset({"ZKEY9999"}))

    keys = set(_rows(p))
    assert "OTHER111" in keys                 # the fresh derived row
    assert "ZKEY9999" in keys                 # verdicted Zotero key preserved
    assert "feed:5" in keys                   # namespaced rows still preserved
    # Without preservation the Zotero-key verdict would be dropped:
    goldenset._write_csv([fresh], p)          # no preserve_keys
    assert "ZKEY9999" not in set(_rows(p))
    assert "feed:5" in set(_rows(p))          # namespaced still kept
