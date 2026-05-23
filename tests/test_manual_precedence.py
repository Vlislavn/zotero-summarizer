"""Part A regression tests: the user's manual verdict must always win
and stay visible/editable — even after a Refresh-labels re-derivation
that changes the CSV's auto priority, and even for keys no longer in the
golden CSV (orphaned verdicts, Today feed items).
"""
from __future__ import annotations

import asyncio
import csv
import sqlite3
from pathlib import Path

import pytest

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.runtime import AppContext, set_context
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import repositories


GOLDEN_HEADER = [
    "item_key", "title", "authors", "year", "venue", "doi", "url", "abstract",
    "gold_inferred_relevance", "gold_priority_final", "gold_signal_tier", "in_trash",
]


def _make_project(tmp_path: Path, rows: list[dict[str, str]]) -> Settings:
    settings = Settings.load(project_root=tmp_path)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    csv_path = settings.golden_csv_path
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=GOLDEN_HEADER)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in GOLDEN_HEADER})
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


def _seed_verdict(settings: Settings, item_key: str, derived: str, user: str, comment: str = "") -> None:
    repositories.insert_or_update_label_verdict(
        settings.triage_db_path,
        item_key=item_key,
        original_derived_priority=derived,
        user_priority=user,
        comment=comment,
    )


# ---------------------------------------------------------------------------
# A1 — effective priority drives the list + filters
# ---------------------------------------------------------------------------


def test_list_shows_effective_priority_when_verdict_exists(tmp_path):
    s = _make_project(tmp_path, [
        {"item_key": "ABC12345", "title": "Paper A", "abstract": "x",
         "gold_priority_final": "could_read", "gold_inferred_relevance": "3.0"},
    ])
    _seed_verdict(s, "ABC12345", derived="could_read", user="must_read")
    from zotero_summarizer.api.routes import golden

    out = _run(golden.list_all())
    row = next(it for it in out["items"] if it["item_key"] == "ABC12345")
    assert row["effective_priority"] == "must_read"
    assert row["user_priority"] == "must_read"
    assert row["is_user_override"] is True
    assert row["derived_priority"] == "could_read"


def test_filter_by_priority_uses_effective_not_derived(tmp_path):
    """A paper derived could_read but manually set must_read appears under
    the must_read filter, not could_read."""
    s = _make_project(tmp_path, [
        {"item_key": "ABC12345", "title": "Paper A", "abstract": "x",
         "gold_priority_final": "could_read", "gold_inferred_relevance": "3.0"},
    ])
    _seed_verdict(s, "ABC12345", derived="could_read", user="must_read")
    from zotero_summarizer.api.routes import golden

    must = _run(golden.list_all(priority="must_read"))
    assert any(it["item_key"] == "ABC12345" for it in must["items"])

    could = _run(golden.list_all(priority="could_read"))
    assert not any(it["item_key"] == "ABC12345" for it in could["items"])


def test_orphaned_verdict_appears_in_list(tmp_path):
    """A verdict whose key is not in the CSV is surfaced as orphaned."""
    s = _make_project(tmp_path, [
        {"item_key": "ABC12345", "title": "Paper A", "abstract": "x",
         "gold_priority_final": "could_read", "gold_inferred_relevance": "3.0"},
    ])
    _seed_verdict(s, "feed:9001", derived="unknown", user="must_read", comment="from Today")
    from zotero_summarizer.api.routes import golden

    out = _run(golden.list_all())
    orphan = next((it for it in out["items"] if it["item_key"] == "feed:9001"), None)
    assert orphan is not None
    assert orphan["orphaned"] is True
    assert orphan["effective_priority"] == "must_read"
    assert out["flag_counts"]["orphaned"] == 1

    # And it's findable under its manual class.
    must = _run(golden.list_all(priority="must_read", flag="orphaned"))
    assert any(it["item_key"] == "feed:9001" for it in must["items"])


# ---------------------------------------------------------------------------
# A2 — never 404 a key that has (or should have) a verdict
# ---------------------------------------------------------------------------


def test_submit_verdict_for_key_not_in_csv_succeeds(tmp_path):
    s = _make_project(tmp_path, [
        {"item_key": "ABC12345", "title": "Paper A", "abstract": "x",
         "gold_priority_final": "could_read", "gold_inferred_relevance": "3.0"},
    ])
    from zotero_summarizer.api.routes import golden

    req = golden.VerdictRequest(item_key="feed:9001", user_priority="must_read", comment="Today")
    out = _run(golden.submit_verdict(req))
    assert "id" in out
    stored = repositories.get_label_verdict(s.triage_db_path, "feed:9001")
    assert stored is not None
    assert stored["user_priority"] == "must_read"
    assert stored["original_derived_priority"] == "unknown"


def test_submit_verdict_anchors_to_provenance_when_in_csv(tmp_path):
    s = _make_project(tmp_path, [
        {"item_key": "ABC12345", "title": "Paper A", "abstract": "x",
         "gold_priority_final": "could_read", "gold_inferred_relevance": "3.0"},
    ])
    from zotero_summarizer.api.routes import golden

    req = golden.VerdictRequest(item_key="ABC12345", user_priority="must_read", comment="")
    _run(golden.submit_verdict(req))
    stored = repositories.get_label_verdict(s.triage_db_path, "ABC12345")
    # original anchored to the derived value, not "unknown"
    assert stored["original_derived_priority"] in ("could_read", "dont_read", "should_read", "must_read")


def test_review_detail_orphan_verdict_no_404(tmp_path):
    """A verdict on a key not in any source still returns a payload (stub)
    with the verdict attached, so it stays viewable + editable."""
    s = _make_project(tmp_path, [
        {"item_key": "ABC12345", "title": "Paper A", "abstract": "x",
         "gold_priority_final": "could_read", "gold_inferred_relevance": "3.0"},
    ])
    _seed_verdict(s, "ZGONE999", derived="unknown", user="dont_read", comment="left library")
    from zotero_summarizer.api.routes import golden

    out = _run(golden.review_detail("ZGONE999"))
    assert out["item_key"] == "ZGONE999"
    assert out["provenance"] is None
    assert out["verdict"] is not None
    assert out["verdict"]["user_priority"] == "dont_read"
    assert out["source"] == "csv_stub"


def test_review_detail_unknown_key_no_verdict_still_404(tmp_path):
    _make_project(tmp_path, [
        {"item_key": "ABC12345", "title": "Paper A", "abstract": "x",
         "gold_priority_final": "could_read", "gold_inferred_relevance": "3.0"},
    ])
    from zotero_summarizer.api.routes import golden

    with pytest.raises(APIError) as ei:
        _run(golden.review_detail("NEVER0001"))
    assert ei.value.status_code == 404
