"""goldenset._write_csv preserves UI-appended rows on re-export.

Regression test for the data-loss bug discovered 2026-05-14: the original
``_write_csv`` opened the golden CSV in ``"w"`` mode and overwrote every row,
silently destroying ~650 rows the user had labelled in the Feed Review UI
(``feed:NNN`` keys) and the analyse-notes flow (``note:KEY:ID`` keys).

The fix: rows whose ``item_key`` contains ``":"`` are NOT re-derivable from
Zotero's ``items`` table and must survive re-export.
"""

from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path

import pytest

from zotero_summarizer.services.golden.goldenset import GoldenSample, _write_csv


def _sample(item_key: str, priority: str = "could_read") -> GoldenSample:
    return GoldenSample(
        item_key=item_key,
        title=f"Title {item_key}",
        authors="A. Author",
        year="2026",
        venue="Test Venue",
        doi="",
        url="",
        abstract="abstract",
        matched_emojis="",
        gold_signal_tier="medium_positive",
        note_count=0,
        annotation_count=0,
        collection_count=0,
        collections="",
        in_trash=False,
        days_since_added=10,
        gold_priority_inferred=priority,
        gold_signal_strength="medium",
        gold_inferred_relevance=3.0,
        gold_priority_final=priority,
        gold_notes="",
    )


def _ui_row(fieldnames: list[str], item_key: str, priority: str) -> dict[str, str]:
    """Build a namespaced row like the review UI appends (tier=first_glance)."""
    base = asdict(_sample("placeholder", priority))
    base["item_key"] = item_key
    base["gold_signal_tier"] = "first_glance"
    base["gold_signal_strength"] = "high"
    base["gold_notes"] = "first-glance annotation from feed predictions"
    return {col: str(base.get(col, "")) for col in fieldnames}


def _write_with_ui_rows(path: Path, zotero_samples: list[GoldenSample],
                        ui_rows: list[dict[str, str]]) -> None:
    fieldnames = list(asdict(zotero_samples[0]).keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for s in zotero_samples:
            w.writerow(asdict(s))
        for r in ui_rows:
            w.writerow(r)


def _read_keys(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as f:
        return [r["item_key"] for r in csv.DictReader(f)]


def test_first_export_creates_csv_without_preserving_anything(tmp_path: Path):
    """No existing file → just write what _pull_samples produced."""
    csv_path = tmp_path / "golden.csv"
    samples = [_sample("ABC123"), _sample("DEF456")]
    _write_csv(samples, csv_path)

    assert csv_path.exists()
    assert _read_keys(csv_path) == ["ABC123", "DEF456"]


def test_reexport_preserves_namespaced_ui_rows(tmp_path: Path):
    """Re-export must keep feed:* and note:* rows that the UI appended."""
    csv_path = tmp_path / "golden.csv"
    fieldnames = list(asdict(_sample("X")).keys())

    # First write: 2 Zotero rows + 3 UI-appended rows.
    initial_samples = [_sample("ABC123"), _sample("DEF456")]
    ui_rows = [
        _ui_row(fieldnames, "feed:34303", "dont_read"),
        _ui_row(fieldnames, "feed:34427", "could_read"),
        _ui_row(fieldnames, "note:KEY1:475", "must_read"),
    ]
    _write_with_ui_rows(csv_path, initial_samples, ui_rows)
    assert len(_read_keys(csv_path)) == 5

    # Re-export: same 2 Zotero rows + 1 newly-engaged Zotero row.
    new_samples = [_sample("ABC123"), _sample("DEF456"), _sample("GHI789")]
    _write_csv(new_samples, csv_path)

    keys = _read_keys(csv_path)
    assert keys == ["ABC123", "DEF456", "GHI789", "feed:34303", "feed:34427", "note:KEY1:475"]


def test_reexport_drops_zotero_rows_no_longer_engaged(tmp_path: Path):
    """A Zotero key (no ':') that disappears from samples must NOT be preserved.
    Only namespaced rows get the survival guarantee."""
    csv_path = tmp_path / "golden.csv"
    fieldnames = list(asdict(_sample("X")).keys())

    _write_with_ui_rows(
        csv_path,
        [_sample("ABC123"), _sample("DEF456")],
        [_ui_row(fieldnames, "feed:111", "dont_read")],
    )
    # Re-export: DEF456 no longer in the engaged set.
    _write_csv([_sample("ABC123")], csv_path)

    keys = _read_keys(csv_path)
    assert keys == ["ABC123", "feed:111"]
    assert "DEF456" not in keys


def test_reexport_zotero_row_wins_when_key_collides_with_namespaced(tmp_path: Path):
    """If a sample's item_key matches a preserved namespaced row's key (edge
    case — shouldn't happen since Zotero keys never contain ':'), the fresh
    Zotero row wins. We dedupe against ``sample_keys``."""
    csv_path = tmp_path / "golden.csv"
    fieldnames = list(asdict(_sample("X")).keys())

    # Seed a namespaced row whose key happens to contain ':' but matches a future sample.
    _write_with_ui_rows(
        csv_path,
        [_sample("ABC123")],
        [_ui_row(fieldnames, "weird:key", "must_read")],
    )
    # Re-export emits a sample with the same key.
    _write_csv([_sample("weird:key", priority="could_read")], csv_path)

    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["item_key"] == "weird:key"
    assert rows[0]["gold_priority_final"] == "could_read"  # Zotero sample wins.


def test_corrupted_existing_csv_without_item_key_column_raises(tmp_path: Path):
    """If somehow the existing CSV is missing the item_key column, refuse to
    silently overwrite — fail-fast so the user can investigate."""
    csv_path = tmp_path / "golden.csv"
    csv_path.write_text("wrong_column,another\nfoo,bar\n", encoding="utf-8")

    with pytest.raises(ValueError, match="no item_key column"):
        _write_csv([_sample("ABC123")], csv_path)


def test_empty_samples_writes_empty_file(tmp_path: Path):
    """Pre-existing behavior: empty samples → empty CSV file (no error)."""
    csv_path = tmp_path / "golden.csv"
    csv_path.write_text("seed", encoding="utf-8")
    _write_csv([], csv_path)
    assert csv_path.read_text() == ""
