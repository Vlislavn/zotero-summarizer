"""High-complexity, production-readiness edge cases for the golden /
border / verdict surface.

These exercise the FastAPI route handlers directly (they are plain async
functions) with a hermetic tmp project root, plus the pure parse/scoring
helpers at their boundaries. The focus is the failure modes that would
surface in production: malformed keys, out-of-range params, missing
source rows, concurrent writes, and threshold boundaries.
"""
from __future__ import annotations

import asyncio
import csv
import sqlite3
import threading
from pathlib import Path

import pytest

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.runtime import AppContext, set_context
from zotero_summarizer.settings import Settings
from zotero_summarizer.services.library import review_detail as rd
from zotero_summarizer.storage import repositories


GOLDEN_HEADER = [
    "item_key", "title", "authors", "year", "venue", "doi", "url", "abstract",
    "gold_inferred_relevance", "gold_priority_final", "gold_signal_tier", "in_trash",
]


def _make_project(tmp_path: Path, rows: list[dict[str, str]]) -> Settings:
    """Create a hermetic project root with a golden CSV + label_verdicts table."""
    settings = Settings.load(project_root=tmp_path)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    csv_path = settings.golden_csv_path
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GOLDEN_HEADER)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in GOLDEN_HEADER})
    # Create the label_verdicts table directly in the tmp triage DB.
    conn = sqlite3.connect(str(settings.triage_db_path))
    try:
        conn.execute(repositories._CREATE_LABEL_VERDICTS_TABLE)
        conn.commit()
    finally:
        conn.close()
    set_context(AppContext(settings=settings))
    return settings


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# review_detail route — malformed keys map to clean 4xx, never 500
# ---------------------------------------------------------------------------


def test_review_detail_empty_key_422(tmp_path):
    _make_project(tmp_path, [{"item_key": "ABC12345", "title": "T", "abstract": "a"}])
    from zotero_summarizer.api.routes import golden
    with pytest.raises(APIError) as ei:
        _run(golden.review_detail("   "))
    assert ei.value.status_code == 422


def test_review_detail_key_not_in_csv_404(tmp_path):
    _make_project(tmp_path, [{"item_key": "ABC12345", "title": "T", "abstract": "a"}])
    from zotero_summarizer.api.routes import golden
    with pytest.raises(APIError) as ei:
        _run(golden.review_detail("NOPE0001"))
    assert ei.value.status_code == 404


def test_review_detail_malformed_feed_key_in_csv_is_422_not_500(tmp_path):
    """A structurally-broken key that IS in the CSV must 422, not crash."""
    _make_project(tmp_path, [{
        "item_key": "feed:abc", "title": "Broken feed", "abstract": "x",
        "gold_priority_final": "could_read",
    }])
    from zotero_summarizer.api.routes import golden
    with pytest.raises(APIError) as ei:
        _run(golden.review_detail("feed:abc"))
    assert ei.value.status_code == 422


def test_review_detail_csv_stub_for_missing_source(tmp_path):
    """A library key in the CSV but absent from Zotero falls back to a stub."""
    _make_project(tmp_path, [{
        "item_key": "GONE1234", "title": "Deleted from Zotero",
        "authors": "Smith J; Lee P", "abstract": "Body text",
        "year": "2026", "venue": "arxiv", "doi": "10.1/x",
        "gold_priority_final": "must_read", "gold_inferred_relevance": "5.0",
    }])
    from zotero_summarizer.api.routes import golden
    # No Zotero reader available in tests → library lookup returns None →
    # the route must fall back to the csv_stub payload (HTTP 200 shape).
    out = _run(golden.review_detail("GONE1234"))
    assert out["source"] == "csv_stub"
    assert out["title"] == "Deleted from Zotero"
    assert [a["name"] for a in out["authors"]] == ["Smith J", "Lee P"]
    assert out["provenance"] is not None


# ---------------------------------------------------------------------------
# border_suggestions route — top_k bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_top_k", [-1, 0, 2001, 99999])
def test_border_top_k_out_of_range_422(tmp_path, bad_top_k):
    _make_project(tmp_path, [{"item_key": "ABC12345", "title": "T", "abstract": "a",
                              "gold_priority_final": "could_read"}])
    from zotero_summarizer.api.routes import golden
    with pytest.raises(APIError) as ei:
        _run(golden.border_suggestions(top_k=bad_top_k))
    assert ei.value.status_code == 422


def test_border_missing_csv_404(tmp_path):
    settings = Settings.load(project_root=tmp_path)  # no CSV written
    set_context(AppContext(settings=settings))
    from zotero_summarizer.api.routes import golden
    with pytest.raises(APIError) as ei:
        _run(golden.border_suggestions(top_k=50))
    assert ei.value.status_code == 404


# ---------------------------------------------------------------------------
# parse helpers — malformed keys raise InvalidItemKey (a ValueError subclass)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["feed:abc", "feed:", "feed: ", "feed:1.5", "feed:x9"])
def test_parse_feed_key_malformed_raises_invaliditemkey(key):
    with pytest.raises(rd.InvalidItemKey):
        rd.parse_feed_key(key)


@pytest.mark.parametrize("key", [
    "note:", "note:ONLYPARENT", "note:PARENT:notanum", "note::42", "note:P:1:2",
])
def test_parse_note_key_malformed_raises_invaliditemkey(key):
    with pytest.raises(rd.InvalidItemKey):
        rd.parse_note_key(key)


def test_invaliditemkey_is_valueerror_subclass():
    # Callers may catch either; the contract must keep this inheritance.
    assert issubclass(rd.InvalidItemKey, ValueError)


# ---------------------------------------------------------------------------
# Concurrency — parallel verdict UPSERTs must not corrupt or duplicate
# ---------------------------------------------------------------------------


def test_concurrent_verdict_upserts_single_row(tmp_path):
    """20 threads UPSERT verdicts for the same key; exactly one row survives,
    and the table is never left half-written. Guards the WAL write path."""
    settings = _make_project(tmp_path, [
        {"item_key": "RACE0001", "title": "T", "abstract": "a",
         "gold_priority_final": "could_read"},
    ])
    db = settings.triage_db_path
    priorities = ["must_read", "should_read", "could_read", "dont_read"]
    errors: list[Exception] = []

    def worker(i: int):
        try:
            repositories.insert_or_update_label_verdict(
                db,
                item_key="RACE0001",
                original_derived_priority="could_read",
                user_priority=priorities[i % 4],
                comment=f"thread {i}",
            )
        except Exception as exc:  # capture to assert later — this is a test harness
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"concurrent UPSERT raised: {errors}"
    rows = repositories.list_label_verdicts(db)
    matching = [r for r in rows if r["item_key"] == "RACE0001"]
    assert len(matching) == 1, f"expected exactly 1 row, got {len(matching)}"
    assert matching[0]["user_priority"] in priorities


def test_concurrent_distinct_verdict_writes(tmp_path):
    """50 threads each write a distinct key; all 50 land, none lost."""
    settings = _make_project(tmp_path, [
        {"item_key": f"K{i:05d}", "title": "T", "abstract": "a",
         "gold_priority_final": "could_read"} for i in range(50)
    ])
    db = settings.triage_db_path
    errors: list[Exception] = []

    def worker(i: int):
        try:
            repositories.insert_or_update_label_verdict(
                db,
                item_key=f"K{i:05d}",
                original_derived_priority="could_read",
                user_priority="must_read",
                comment="",
            )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"concurrent writes raised: {errors}"
    rows = repositories.list_label_verdicts(db)
    assert len([r for r in rows if r["item_key"].startswith("K")]) == 50
