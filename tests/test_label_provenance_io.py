"""Phase 1.18 Step 1 — tests for label-provenance I/O + verdict semantics.

Covers: CSV-row parsing, direct-user-verdict detection, manual-override
detection, bulk loading from disk, find/flag helpers, JSON serialization.

Pure scoring math lives in ``test_label_provenance_scoring.py``.
"""

from __future__ import annotations

import json

import pytest

from zotero_summarizer.services.golden import label_provenance as lp


# ---------------------------------------------------------------------------
# is_direct_user_verdict + is_manual_override semantics
# ---------------------------------------------------------------------------


def test_feed_prefix_row_is_direct_user_verdict():
    """Rows with item_key 'feed:NNN' come from button clicks; persisted is authoritative."""
    row = {
        "item_key": "feed:34303",
        "title": "Some paper",
        "matched_emojis": "",
        "in_trash": "False",
        "annotation_count": "0",
        "note_count": "0",
        "days_since_added": "-1",
        "gold_priority_final": "dont_read",
        "gold_inferred_relevance": "1.0",
    }
    p = lp.provenance_from_row(row)
    assert p.is_direct_user_verdict is True
    # Persisted dont_read but derived would be could_read (no signals → baseline 3.0)
    # → DISAGREEMENT but NOT a manual override; the row bypasses derivation.
    assert p.is_manual_override is False


def test_note_prefix_row_is_direct_user_verdict():
    row = {
        "item_key": "note:KEY:123",
        "title": "Note",
        "matched_emojis": "",
        "in_trash": "False",
        "annotation_count": "0",
        "note_count": "0",
        "days_since_added": "-1",
        "gold_priority_final": "must_read",
        "gold_inferred_relevance": "5.0",
    }
    p = lp.provenance_from_row(row)
    assert p.is_direct_user_verdict is True
    assert p.is_manual_override is False


def test_library_row_with_disagreement_is_manual_override():
    """Library row (no colon prefix) with persisted != derived: real override."""
    row = {
        "item_key": "ABC12345",
        "title": "Paper",
        "matched_emojis": "",
        "in_trash": "False",
        "annotation_count": "0",
        "note_count": "0",
        "days_since_added": "10",
        "gold_priority_final": "must_read",  # manually set; would derive to could_read
        "gold_inferred_relevance": "5.0",
    }
    p = lp.provenance_from_row(row)
    assert p.is_direct_user_verdict is False
    assert p.is_manual_override is True
    assert p.derived_priority == "could_read"
    assert p.persisted_priority == "must_read"


def test_library_row_with_agreement_is_not_override():
    row = {
        "item_key": "ABC12345",
        "title": "Paper",
        "matched_emojis": "🧠",
        "in_trash": "False",
        "annotation_count": "5",
        "note_count": "2",
        "days_since_added": "10",
        "gold_priority_final": "must_read",
        "gold_inferred_relevance": "5.0",
    }
    p = lp.provenance_from_row(row)
    assert p.is_direct_user_verdict is False
    assert p.is_manual_override is False
    assert p.derived_priority == "must_read"


# ---------------------------------------------------------------------------
# CSV-row parsing
# ---------------------------------------------------------------------------


def test_provenance_from_row_basic():
    row = {
        "item_key": "K1",
        "title": "Title",
        "matched_emojis": "🧠 ✅",
        "in_trash": "False",
        "annotation_count": "3",
        "note_count": "1",
        "days_since_added": "50",
        "gold_priority_final": "must_read",
        "gold_inferred_relevance": "4.8",
    }
    p = lp.provenance_from_row(row)
    assert p.item_key == "K1"
    assert p.title == "Title"
    assert p.persisted_priority == "must_read"
    assert len(p.emoji_contributions) == 2  # 🧠 + ✅


def test_provenance_from_row_missing_item_key_raises():
    row = {"item_key": "", "title": "T"}
    with pytest.raises(ValueError, match="item_key"):
        lp.provenance_from_row(row)


def test_parse_int_garbage_raises():
    """Non-empty unparseable int should propagate ValueError (fail-fast)."""
    row = {
        "item_key": "K1",
        "title": "T",
        "matched_emojis": "",
        "in_trash": "False",
        "annotation_count": "not_a_number",
        "note_count": "0",
        "days_since_added": "0",
        "gold_priority_final": "could_read",
        "gold_inferred_relevance": "3.0",
    }
    with pytest.raises(ValueError):
        lp.provenance_from_row(row)


def test_parse_int_empty_string_uses_default():
    """Empty CSV cell is a legitimate 'absent' value; treated as 0."""
    row = {
        "item_key": "K1",
        "title": "T",
        "matched_emojis": "",
        "in_trash": "False",
        "annotation_count": "",
        "note_count": "",
        "days_since_added": "",
        "gold_priority_final": "could_read",
        "gold_inferred_relevance": "",
    }
    p = lp.provenance_from_row(row)
    assert p.annotation_count == 0
    assert p.user_note_count == 0
    assert p.days_since_added == 0


# ---------------------------------------------------------------------------
# Bulk loading + searching
# ---------------------------------------------------------------------------


def test_load_golden_provenance_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        lp.load_golden_provenance(tmp_path / "no.csv")


def test_load_golden_provenance_reads_csv(tmp_path):
    csv_path = tmp_path / "g.csv"
    csv_path.write_text(
        "item_key,title,matched_emojis,in_trash,annotation_count,note_count,"
        "days_since_added,gold_priority_final,gold_inferred_relevance\n"
        "K1,Paper A,🧠,False,2,0,10,must_read,5.0\n"
        "feed:99,Paper B,,False,0,0,-1,dont_read,1.0\n",
        encoding="utf-8",
    )
    provs = lp.load_golden_provenance(csv_path)
    assert len(provs) == 2
    assert provs[0].item_key == "K1"
    assert provs[1].is_direct_user_verdict is True


def test_compute_provenance_lookup_by_item_key():
    provs = [
        lp.compute_provenance(
            item_key=k, title="T",
            tags=[], in_trash=False,
            annotation_count=0, user_note_count=0, days_since_added=0,
        )
        for k in ("A", "B", "C")
    ]
    assert [p.item_key for p in provs] == ["A", "B", "C"]


def test_flag_summary_groups_by_flag():
    provs = [
        lp.compute_provenance(
            item_key="weak1", title="T",
            tags=["🧠"], in_trash=False,
            annotation_count=0, user_note_count=0, days_since_added=0,
        ),  # weak_must_read flag
        lp.compute_provenance(
            item_key="strong1", title="T",
            tags=["🧠", "✅"], in_trash=False,
            annotation_count=3, user_note_count=1, days_since_added=0,
        ),  # no flags
    ]
    summary = lp.flag_summary(provs)
    assert "weak_must_read" in summary
    assert "weak1" in summary["weak_must_read"]
    assert "strong1" not in summary.get("weak_must_read", [])


def test_flag_summary_includes_manual_override():
    """Library-row manual overrides should appear under the 'manual_override' key."""
    row = {
        "item_key": "ABC12345",
        "title": "Paper",
        "matched_emojis": "",
        "in_trash": "False",
        "annotation_count": "0",
        "note_count": "0",
        "days_since_added": "10",
        "gold_priority_final": "must_read",
        "gold_inferred_relevance": "5.0",
    }
    provs = [lp.provenance_from_row(row)]
    summary = lp.flag_summary(provs)
    assert "manual_override" in summary
    assert "ABC12345" in summary["manual_override"]


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------


def test_provenance_to_dict_is_json_serializable():
    p = lp.compute_provenance(
        item_key="K1", title="T",
        tags=["🧠"], in_trash=False,
        annotation_count=3, user_note_count=1, days_since_added=50,
    )
    d = lp.provenance_to_dict(p)
    text = json.dumps(d)  # must not raise
    parsed = json.loads(text)
    assert parsed["item_key"] == "K1"
    assert parsed["derived_priority"] == "must_read"
    assert "emoji_contributions" in parsed["additive_scoring"]
    assert "is_direct_user_verdict" in parsed
