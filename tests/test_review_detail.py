"""Unit tests for services.review_detail — the dispatch layer for the
golden CSV's three item_key flavors (feed:* / note:* / library 8-char).

These tests focus on pure logic that doesn't need a Zotero SQLite or a
running FastAPI app:
  * classify_item_key + parse_{feed,note}_key
  * build_scoring (projecting shap_contribs_json into the React shape)
  * normalize_authors (the union of input shapes seen across the code)
  * _pick_note_by_id (the legacy-note resolution heuristic)

End-to-end tests for ``review_detail`` against a real Zotero DB live in
``test_review_workflow.py``; this file is intentionally narrow so the
unit-level fail modes are caught fast.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from zotero_summarizer.services.library import review_detail as rd
from zotero_summarizer.storage import feeds as fs


# ---------------------------------------------------------------------------
# Key classification + parsing
# ---------------------------------------------------------------------------


def test_classify_item_key_recognises_three_sources():
    assert rd.classify_item_key("feed:12345") == rd.SOURCE_FEED
    assert rd.classify_item_key("note:ABC12345:67") == rd.SOURCE_NOTE
    assert rd.classify_item_key("ABCDEFGH") == rd.SOURCE_LIBRARY


def test_classify_item_key_rejects_empty():
    with pytest.raises(rd.InvalidItemKey):
        rd.classify_item_key("")


def test_parse_feed_key_returns_int():
    assert rd.parse_feed_key("feed:33911") == 33911


def test_parse_feed_key_rejects_empty_suffix():
    with pytest.raises(rd.InvalidItemKey):
        rd.parse_feed_key("feed:")


def test_parse_feed_key_rejects_non_numeric():
    with pytest.raises(ValueError):
        rd.parse_feed_key("feed:notanumber")


def test_parse_note_key_returns_parent_and_id():
    parent, note_id = rd.parse_note_key("note:ABC12345:67")
    assert parent == "ABC12345"
    assert note_id == 67


def test_parse_note_key_rejects_wrong_arity():
    with pytest.raises(rd.InvalidItemKey):
        rd.parse_note_key("note:JUSTAPARENT")


def test_parse_note_key_rejects_empty_parent():
    with pytest.raises(rd.InvalidItemKey):
        rd.parse_note_key("note::42")


# ---------------------------------------------------------------------------
# Author normalization
# ---------------------------------------------------------------------------


def test_normalize_authors_from_comma_string_used_by_feed_metadata():
    authors = rd.normalize_authors("Smith J, Lee P, Park K")
    assert [a["name"] for a in authors] == ["Smith J", "Lee P", "Park K"]
    assert all(a["h_index"] is None for a in authors)


def test_normalize_authors_from_string_list_used_by_zotero():
    authors = rd.normalize_authors(["Smith J", "Lee P"])
    assert [a["name"] for a in authors] == ["Smith J", "Lee P"]


def test_normalize_authors_from_dict_list_first_last():
    authors = rd.normalize_authors([
        {"first_name": "Jane", "last_name": "Smith"},
        {"name": "Lee P"},
    ])
    assert [a["name"] for a in authors] == ["Jane Smith", "Lee P"]


def test_normalize_authors_empty_returns_empty_list():
    assert rd.normalize_authors(None) == []
    assert rd.normalize_authors("") == []
    assert rd.normalize_authors([]) == []


def test_normalize_authors_attaches_top_author_h_to_first_only():
    authors = rd.normalize_authors(["Smith J", "Lee P"], top_author_h=42)
    assert authors[0]["h_index"] == 42
    assert authors[1]["h_index"] is None


def test_normalize_authors_splits_semicolons_inside_list_elements():
    """Real-world feed metadata emits ``["A; B; C"]`` — one element, three names."""
    authors = rd.normalize_authors(["Ali Madani; Aadyot Bhatnagar; Peter Groth"])
    assert [a["name"] for a in authors] == ["Ali Madani", "Aadyot Bhatnagar", "Peter Groth"]


def test_normalize_authors_handles_semicolon_string():
    authors = rd.normalize_authors("Smith J; Lee P; Park K")
    assert [a["name"] for a in authors] == ["Smith J", "Lee P", "Park K"]


# ---------------------------------------------------------------------------
# Scoring extraction
# ---------------------------------------------------------------------------


def _shap_row(payload: dict | None, composite: float | None = None) -> dict:
    """Build a minimal processed_feed_items-shaped row dict for build_scoring."""
    return {
        "id": 1,
        "shap_contribs_json": json.dumps(payload) if payload else "",
        "composite_score": composite,
    }


def test_build_scoring_returns_none_when_payload_absent():
    row = _shap_row(None)
    assert rd.build_scoring(row) is None


def test_build_scoring_ranks_shap_by_absolute_contribution():
    payload = {
        "shap": [
            {"feature": "prestige", "contribution": 0.30},
            {"feature": "goal_alignment", "contribution": -0.05},
            {"feature": "novelty", "contribution": 0.22},
            {"feature": "rigor", "contribution": 0.10},
        ],
        "aux_context": {"max_author_h_index": 42, "venue_works_count": 14823},
        "summary": {"prestige_score": 4.2},
    }
    out = rd.build_scoring(_shap_row(payload, composite=3.7))
    assert out is not None
    features = [c["feature"] for c in out["shap_top"]]
    assert features[0] == "prestige"  # largest abs value
    assert features[1] == "novelty"
    assert features[2] == "rigor"
    assert features[3] == "goal_alignment"  # smallest abs value
    assert out["composite_score"] == pytest.approx(3.7)
    assert out["prestige_score"] == pytest.approx(4.2)
    assert out["prestige_inputs"]["max_author_h_index"] == 42
    assert out["prestige_inputs"]["venue_works_count"] == 14823


def test_build_scoring_caps_at_six_bars():
    payload = {
        "shap": [
            {"feature": f"f{i}", "contribution": 1.0 - i * 0.01}
            for i in range(20)
        ],
        "aux_context": {},
        "summary": {},
    }
    out = rd.build_scoring(_shap_row(payload, composite=2.0))
    assert out is not None
    assert len(out["shap_top"]) == 6


def test_build_scoring_omits_missing_prestige_inputs():
    payload = {
        "shap": [{"feature": "x", "contribution": 0.1}],
        "aux_context": {"max_author_h_index": 0},  # 0 is a real value, keep it
        "summary": {},
    }
    out = rd.build_scoring(_shap_row(payload, composite=1.0))
    assert out is not None
    assert "max_author_h_index" in out["prestige_inputs"]
    assert "venue_works_count" not in out["prestige_inputs"]


def test_build_scoring_corrupt_json_raises():
    row = {"id": 1, "shap_contribs_json": "{not json}", "composite_score": None}
    with pytest.raises(ValueError):
        rd.build_scoring(row)


# ---------------------------------------------------------------------------
# Note selection
# ---------------------------------------------------------------------------


def test_pick_note_matches_by_id_in_note_key():
    notes = [
        {"note_key": "ABC123", "note": "first"},
        {"note_key": "XYZ47XYZ", "note": "second"},
        {"note_key": "DEF222", "note": "third"},
    ]
    assert rd._pick_note_by_id(notes, 47) == notes[1]


def test_pick_note_falls_back_to_first_when_no_match():
    notes = [{"note_key": "ABC", "note": "first"}, {"note_key": "DEF", "note": "second"}]
    # 47 doesn't appear in any note_key → newest (index 0) wins
    assert rd._pick_note_by_id(notes, 47) == notes[0]


def test_pick_note_empty_returns_none():
    assert rd._pick_note_by_id([], 1) is None


# ---------------------------------------------------------------------------
# Feed-branch DB integration (in-memory, no Zotero)
# ---------------------------------------------------------------------------


def _fresh_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    fs.init_feeds_schema(conn)
    return conn


def test_get_processed_feed_item_by_id_returns_row():
    conn = _fresh_db()
    payload = json.dumps({
        "shap": [{"feature": "prestige", "contribution": 0.3}],
        "aux_context": {"max_author_h_index": 42},
        "summary": {"prestige_score": 4.2, "authors": "Smith, Lee"},
    })
    fs.record_decision(
        conn,
        run_id="r1",
        feed_item={"feed_library_id": 7, "item_id": 999, "guid": "g999", "title": "P"},
        decision=fs.DECISION_TRIAGED_PENDING,
        composite_score=3.7,
        shap_contribs_json=payload,
    )
    row = fs.get_processed_feed_item_by_id(conn, 999)
    assert row is not None
    assert row["title"] == "P"
    assert row["feed_library_id"] == 7
    assert row["composite_score"] == pytest.approx(3.7)


def test_get_processed_feed_item_by_id_missing_returns_none():
    conn = _fresh_db()
    assert fs.get_processed_feed_item_by_id(conn, 99999) is None


def test_get_processed_feed_item_by_id_rejects_non_positive():
    conn = _fresh_db()
    with pytest.raises(ValueError):
        fs.get_processed_feed_item_by_id(conn, 0)
    with pytest.raises(ValueError):
        fs.get_processed_feed_item_by_id(conn, -1)


# ---------------------------------------------------------------------------
# CSV-stub fallback (Phase 1.18 Step 3 — usability fix for deleted rows)
# ---------------------------------------------------------------------------


def test_build_csv_stub_detail_uses_csv_columns():
    """When source-store is gone, the stub carries CSV bibliographics."""
    row = {
        "item_key": "57ZSGVPD",
        "title": "BALAR : A Bayesian Agentic Loop",
        "authors": "Emily B. Fox; Aymen Echarghaoui",
        "year": "2026",
        "venue": "arxiv",
        "doi": "10.1234/balar",
        "url": "https://arxiv.org/abs/2604.12345",
        "abstract": "Large language models in interactive settings…",
    }
    stub = rd.build_csv_stub_detail(row)
    assert stub["source"] == "csv_stub"
    assert stub["title"] == "BALAR : A Bayesian Agentic Loop"
    assert [a["name"] for a in stub["authors"]] == ["Emily B. Fox", "Aymen Echarghaoui"]
    assert stub["year"] == "2026"
    assert stub["doi"] == "10.1234/balar"
    assert stub["abstract"].startswith("Large language models")
    assert stub["has_pdf"] is False
    assert stub["annotations"] == []
    assert stub["notes"] == []
    assert stub["date_added"] == ""  # no date column in the CSV
    assert stub["scoring"] is None


def test_build_library_detail_surfaces_date_added(monkeypatch):
    """The library detail must expose Zotero's dateAdded so the UI can show
    'Added <date>'. Scoring is isolated here (gate off)."""
    from zotero_summarizer.services.library import reading_queue

    monkeypatch.setattr(reading_queue, "get_cached_scoring", lambda k: None)
    monkeypatch.setattr(reading_queue, "live_scoring", lambda item: None)

    class _Reader:
        def get_item_detail(self, key):
            return {
                "title": "T", "abstract": "a", "publication_date": "2026",
                "doi": "", "url": "", "authors": ["X"], "tags": [], "collections": [],
                "annotations": [], "notes": [], "has_pdf": False, "pdf_path": None,
                "date_added": "2026-05-22 21:14:30", "date_modified": "",
            }

    out = rd.build_library_detail(_Reader(), "ABCDEFGH")
    assert out["source"] == "library"
    assert out["date_added"] == "2026-05-22 21:14:30"
    assert out["scoring"] is None


def test_load_csv_row_finds_match(tmp_path):
    csv_path = tmp_path / "g.csv"
    csv_path.write_text(
        "item_key,title,authors\n"
        "ABC12345,First paper,Alice; Bob\n"
        "feed:42,Second paper,Carol\n",
        encoding="utf-8",
    )
    r = rd.load_csv_row(csv_path, "feed:42")
    assert r is not None
    assert r["title"] == "Second paper"
    assert r["authors"] == "Carol"


def test_load_csv_row_returns_none_when_missing(tmp_path):
    csv_path = tmp_path / "g.csv"
    csv_path.write_text("item_key,title\nABC12345,First paper\n", encoding="utf-8")
    assert rd.load_csv_row(csv_path, "NOPE") is None


def test_load_csv_row_returns_none_when_csv_missing(tmp_path):
    assert rd.load_csv_row(tmp_path / "does-not-exist.csv", "ABC12345") is None
