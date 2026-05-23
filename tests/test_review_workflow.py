"""Phase 1.14 — review-mode service + golden CSV append tests."""
from __future__ import annotations

import csv as _csv
import json as _json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from zotero_summarizer.services.library import review, review_summary
from zotero_summarizer.storage import feeds as fs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_triage_db(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "triage.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    fs.init_feeds_schema(conn)
    return conn


def _insert_awaiting(
    conn: sqlite3.Connection,
    *,
    feed_library_id: int = 2,
    feed_item_id: int = 101,
    title: str = "Awesome paper on agents",
    summary: dict | None = None,
    shap: list[dict] | None = None,
    aux: dict | None = None,
) -> int:
    payload = _json.dumps({
        "shap": shap,
        "aux_context": aux,
        "summary": summary or {
            "executive_summary": "x",
            "relevance_score": 4,
            "reading_priority": "should_read",
            "triage_rationale": "auto",
        },
    })
    return fs.record_decision(
        conn,
        run_id="test_run",
        feed_item={
            "feed_library_id": feed_library_id,
            "item_id": feed_item_id,
            "guid": f"guid-{feed_item_id}",
            "title": title,
        },
        decision=fs.DECISION_AWAITING_REVIEW,
        decision_reason="awaiting_review",
        composite_score=3.2,
        reading_priority="should_read",
        shap_contribs_json=payload,
    )


@pytest.fixture
def patched_settings(tmp_path: Path, monkeypatch):
    """Stub `services._common.settings` so the review service uses tmp paths.

    Also creates a tiny golden CSV with the right header so append works.
    """
    golden = tmp_path / "zotero-summarizer-golden.csv"
    fields = [
        "item_key", "title", "authors", "year", "venue", "doi", "url", "abstract",
        "matched_emojis", "gold_signal_tier", "note_count", "annotation_count",
        "collection_count", "collections", "in_trash", "days_since_added",
        "gold_priority_inferred", "gold_signal_strength", "gold_inferred_relevance",
        "gold_priority_final", "gold_notes", "our_composite_score",
        "our_prestige_score", "our_priority", "our_corpus_affinity",
    ]
    with golden.open("w", encoding="utf-8", newline="") as f:
        writer = _csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

    fake_settings = SimpleNamespace(
        triage_db_path=tmp_path / "triage.db",
        project_root=tmp_path,
        golden_csv_path=golden,
        zotero_data_dir=tmp_path / "zotero",   # _fetch_feed_metadata reads this
    )
    monkeypatch.setattr(review, "get_settings", lambda: fake_settings)
    # The golden-append + summary helpers now live in review_summary.
    monkeypatch.setattr(review_summary, "get_settings", lambda: fake_settings)
    # Stub _fetch_feed_metadata so tests don't need a real Zotero install.
    monkeypatch.setattr(review_summary, "_fetch_feed_metadata", lambda **kw: {})
    # Pre-create the schema so `_init_triage_db` and `_conn` agree.
    _init_triage_db(tmp_path).close()
    return tmp_path


# ---------------------------------------------------------------------------
# list_awaiting + _decorate_row
# ---------------------------------------------------------------------------


def test_list_awaiting_parses_shap_and_aux_payload(patched_settings):
    db = sqlite3.connect(str(patched_settings / "triage.db"))
    db.row_factory = sqlite3.Row
    _insert_awaiting(
        db,
        shap=[{"feature": "corpus_affinity", "contribution": 0.42}],
        aux={"max_author_h_index": 88.0, "venue_works_count": 12345.0, "cited_by_count": 0.0},
    )
    db.commit()
    db.close()

    items = review.list_awaiting()
    assert len(items) == 1
    item = items[0]
    assert item["shap"] == [{"feature": "corpus_affinity", "contribution": 0.42}]
    assert item["aux_context"]["max_author_h_index"] == 88.0
    assert item["summary"]["reading_priority"] == "should_read"


def test_list_awaiting_returns_empty_when_no_rows(patched_settings):
    assert review.list_awaiting() == []


# ---------------------------------------------------------------------------
# approve / reject / relabel
# ---------------------------------------------------------------------------


def test_approve_flips_state(patched_settings):
    """Phase 1.14: approve only flips state; materialisation happens in apply_all_approved."""
    db = sqlite3.connect(str(patched_settings / "triage.db"))
    db.row_factory = sqlite3.Row
    row_id = _insert_awaiting(db)
    db.commit()
    db.close()

    result = review.approve(row_id)
    assert result["state"] == fs.DECISION_USER_APPROVED

    db = sqlite3.connect(str(patched_settings / "triage.db"))
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT decision FROM processed_feed_items WHERE id = ?", (row_id,)).fetchone()
    assert row["decision"] == fs.DECISION_USER_APPROVED


def test_reject_appends_dont_read_to_golden(patched_settings):
    db = sqlite3.connect(str(patched_settings / "triage.db"))
    db.row_factory = sqlite3.Row
    row_id = _insert_awaiting(db, title="Reject me")
    db.commit()
    db.close()

    result = review.reject(row_id, write_to_golden=True)
    assert result["golden_csv_row_added"] is True

    # State flipped.
    db = sqlite3.connect(str(patched_settings / "triage.db"))
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT decision FROM processed_feed_items WHERE id = ?", (row_id,)).fetchone()
    assert row["decision"] == fs.DECISION_USER_REJECTED

    # Golden CSV grew by one row with dont_read.
    with (patched_settings / "zotero-summarizer-golden.csv").open() as f:
        rows = list(_csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["gold_priority_final"] == "dont_read"
    assert rows[0]["title"] == "Reject me"
    assert rows[0]["item_key"].startswith("feed:")


def test_reject_without_golden_write_does_not_touch_csv(patched_settings):
    db = sqlite3.connect(str(patched_settings / "triage.db"))
    db.row_factory = sqlite3.Row
    row_id = _insert_awaiting(db)
    db.commit()
    db.close()

    result = review.reject(row_id, write_to_golden=False)
    assert result["golden_csv_row_added"] is False

    with (patched_settings / "zotero-summarizer-golden.csv").open() as f:
        rows = list(_csv.DictReader(f))
    assert rows == []


def test_relabel_to_dont_read_routes_through_reject(patched_settings):
    db = sqlite3.connect(str(patched_settings / "triage.db"))
    db.row_factory = sqlite3.Row
    row_id = _insert_awaiting(db)
    db.commit()
    db.close()

    result = review.relabel(row_id, "dont_read")
    assert result["golden_csv_row_added"] is True

    db = sqlite3.connect(str(patched_settings / "triage.db"))
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT decision FROM processed_feed_items WHERE id = ?", (row_id,)).fetchone()
    assert row["decision"] == fs.DECISION_USER_REJECTED


def test_relabel_to_must_read_approves_and_appends(patched_settings):
    """Phase 1.14: relabel→must flips to user_approved + appends golden CSV.
    No pending_changes are queued — materialisation now happens in apply_all_approved.
    """
    db = sqlite3.connect(str(patched_settings / "triage.db"))
    db.row_factory = sqlite3.Row
    row_id = _insert_awaiting(db, title="Force must-read")
    db.commit()
    db.close()

    result = review.relabel(row_id, "must_read")
    assert result["state"] == fs.DECISION_USER_APPROVED
    assert result["golden_csv_row_added"] is True

    db = sqlite3.connect(str(patched_settings / "triage.db"))
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT decision FROM processed_feed_items WHERE id = ?", (row_id,)).fetchone()
    assert row["decision"] == fs.DECISION_USER_APPROVED

    with (patched_settings / "zotero-summarizer-golden.csv").open() as f:
        rows = list(_csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["gold_priority_final"] == "must_read"


def test_relabel_rejects_unknown_priority(patched_settings):
    db = sqlite3.connect(str(patched_settings / "triage.db"))
    db.row_factory = sqlite3.Row
    row_id = _insert_awaiting(db)
    db.commit()
    db.close()
    with pytest.raises(ValueError, match="new_priority"):
        review.relabel(row_id, "garbage")


def test_action_on_missing_row_raises_keyerror(patched_settings):
    with pytest.raises(KeyError):
        review.approve(99999)
    with pytest.raises(KeyError):
        review.reject(99999)


def test_action_on_wrong_state_raises_value_error(patched_settings):
    db = sqlite3.connect(str(patched_settings / "triage.db"))
    db.row_factory = sqlite3.Row
    row_id = _insert_awaiting(db)
    fs.update_to_decision(
        db, feed_library_id=2, feed_item_id=101,
        decision=fs.DECISION_USER_APPROVED, decision_reason="already_done",
    )
    db.commit()
    db.close()

    with pytest.raises(ValueError, match="expected"):
        review.approve(row_id)


def test_approve_without_summary_payload_fails(patched_settings):
    db = sqlite3.connect(str(patched_settings / "triage.db"))
    db.row_factory = sqlite3.Row
    # Insert an awaiting_review row WITHOUT shap_contribs_json — approve must fail loud.
    fs.record_decision(
        db,
        run_id="test",
        feed_item={"feed_library_id": 2, "item_id": 999, "guid": "g", "title": "no payload"},
        decision=fs.DECISION_AWAITING_REVIEW,
    )
    db.commit()
    row_id = db.execute("SELECT id FROM processed_feed_items WHERE feed_item_id = 999").fetchone()["id"]
    db.close()
    with pytest.raises(ValueError, match="summary"):
        review.approve(row_id)


# ---------------------------------------------------------------------------
# Golden CSV append idempotency
# ---------------------------------------------------------------------------


def test_append_to_golden_is_idempotent_on_duplicate_key(patched_settings):
    """Two append_to_golden calls with the same feed_item_id must produce one CSV row."""
    fake_row = {
        "id": 1,
        "feed_item_id": 42,
        "feed_library_id": 2,
        "title": "Dup Title",
        "doi": "",
        "shap_contribs_json": _json.dumps({"summary": {"abstract_preview": "abstract"}}),
    }
    first = review.append_to_golden(fake_row, label="dont_read", note="reject")
    second = review.append_to_golden(fake_row, label="dont_read", note="reject again")
    assert first is True
    assert second is False, "duplicate item_key must not double-append"

    with (patched_settings / "zotero-summarizer-golden.csv").open() as f:
        rows = list(_csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["item_key"] == "feed:42"


# ---------------------------------------------------------------------------
# select_by_decisions (decision filter)
# ---------------------------------------------------------------------------


def test_select_by_decisions_filters_correctly(patched_settings):
    db = sqlite3.connect(str(patched_settings / "triage.db"))
    db.row_factory = sqlite3.Row
    _insert_awaiting(db, feed_item_id=1)
    fs.record_decision(
        db, run_id="x",
        feed_item={"feed_library_id": 2, "item_id": 2, "guid": "g2", "title": "pending"},
        decision=fs.DECISION_TRIAGED_PENDING,
    )
    db.commit()

    awaiting = fs.select_by_decisions(db, decisions=[fs.DECISION_AWAITING_REVIEW])
    pending = fs.select_by_decisions(db, decisions=[fs.DECISION_TRIAGED_PENDING])
    both = fs.select_by_decisions(
        db,
        decisions=[fs.DECISION_AWAITING_REVIEW, fs.DECISION_TRIAGED_PENDING],
    )
    db.close()
    assert len(awaiting) == 1 and awaiting[0]["feed_item_id"] == 1
    assert len(pending) == 1 and pending[0]["feed_item_id"] == 2
    assert len(both) == 2
