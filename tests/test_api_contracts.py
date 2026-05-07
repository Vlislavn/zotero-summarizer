from __future__ import annotations

from zotero_summarizer.storage import repositories as triage_db
import pytest
from pydantic import ValidationError

from zotero_summarizer.models import (
    CorpusImportRequest,
    CorpusItem,
    PendingPriorityOverrideRequest,
    ZoteroItemPriorityUpdateRequest,
    ZoteroItemTagUpdateRequest,
)


def test_corpus_import_request_rejects_oversized_batches():
    oversized_items = [
        {"item_id": f"paper-{index}", "title": f"Title {index}"}
        for index in range(5001)
    ]

    with pytest.raises(ValidationError):
        CorpusImportRequest(items=[CorpusItem.model_validate(item) for item in oversized_items])


def test_insert_feedback_events_upserts_duplicate_signal(monkeypatch, tmp_path):
    monkeypatch.setattr(triage_db, "DB_PATH", tmp_path / "triage_history.db")
    triage_db.init_db()

    triage_db.insert_feedback_events(
        [
            {
                "item_id": "paper-1",
                "feedback_type": "implicit_engagement",
                "signal": "brain_tag",
                "original_priority": "dont_read",
                "inferred_relevance": 5.0,
            }
        ]
    )
    triage_db.insert_feedback_events(
        [
            {
                "item_id": "paper-1",
                "feedback_type": "implicit_engagement_false_negative",
                "signal": "brain_tag",
                "original_priority": "should_read",
                "inferred_relevance": 5.0,
            }
        ]
    )

    rows = triage_db.get_feedback_events(limit=10)

    assert len(rows) == 1
    assert rows[0]["feedback_type"] == "implicit_engagement_false_negative"
    assert rows[0]["original_priority"] == "should_read"


def test_get_latest_results_for_items_returns_latest_prediction(monkeypatch, tmp_path):
    monkeypatch.setattr(triage_db, "DB_PATH", tmp_path / "triage_history.db")
    triage_db.init_db()

    triage_db.insert_result(
        item_id="paper-1",
        title="Paper One",
        response_dict={
            "relevance_score": 2,
            "composite_relevance_score": 2.1,
            "reading_priority": "dont_read",
            "triage_confidence": 0.4,
        },
    )
    triage_db.insert_result(
        item_id="paper-1",
        title="Paper One",
        response_dict={
            "relevance_score": 4,
            "composite_relevance_score": 4.2,
            "reading_priority": "should_read",
            "triage_confidence": 0.8,
        },
    )

    rows = triage_db.get_latest_results_for_items(["paper-1"])

    assert rows["paper-1"]["reading_priority"] == "should_read"
    assert rows["paper-1"]["composite_score"] == 4.2


def test_pending_priority_override_request_normalizes_fields():
    req = PendingPriorityOverrideRequest(
        item_key="  RGW9H2AL  ",
        item_title="  GPT4o-Receipt  ",
        new_priority="must_read",
    )

    assert req.item_key == "RGW9H2AL"
    assert req.item_title == "GPT4o-Receipt"
    assert req.new_priority == "must_read"


def test_pending_priority_override_request_rejects_empty_item_key():
    with pytest.raises(ValidationError):
        PendingPriorityOverrideRequest(
            item_key="   ",
            item_title="Any title",
            new_priority="could_read",
        )


def test_zotero_item_priority_update_request_rejects_invalid_priority():
    with pytest.raises(ValidationError):
        ZoteroItemPriorityUpdateRequest(priority="urgent")


def test_zotero_item_tag_update_request_normalizes_csv_and_deduplicates():
    req = ZoteroItemTagUpdateRequest(add_tags=" alpha, beta,alpha ", remove_tags=["beta", "gamma"])

    assert req.add_tags == ["alpha", "beta"]
    assert req.remove_tags == ["beta", "gamma"]