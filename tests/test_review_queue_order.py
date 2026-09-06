"""Feed-review ordering must select the right prefix, not re-sort a score slice."""
import asyncio
from types import SimpleNamespace

import pytest

from zotero_summarizer.api.routes.review import list_review_queue
from zotero_summarizer.domain import PRIORITY_COULD_READ_THRESHOLD
from zotero_summarizer.services.library import review
from zotero_summarizer.storage import feeds as fs


@pytest.mark.parametrize("state", ["awaiting_review", "gate_rejected"])
@pytest.mark.parametrize("sort", ["recent", "border"])
def test_review_orders_before_limit(tmp_path, monkeypatch, state, sort):
    settings = SimpleNamespace(triage_db_path=tmp_path / "triage.db")
    monkeypatch.setattr(review, "get_settings", lambda: settings)
    with fs.open_triage_conn(settings.triage_db_path) as conn:
        for item_id, score in enumerate([5.0, 4.9, PRIORITY_COULD_READ_THRESHOLD + 0.01, None], 1):
            row_id = fs.record_decision(
                conn, run_id="order-test",
                feed_item={"feed_library_id": 2, "item_id": item_id, "guid": str(item_id)},
                decision=state, decision_reason="test", composite_score=score,
            )
            conn.execute(
                "UPDATE processed_feed_items SET created_at = datetime('now', ?) WHERE id = ?",
                (f"-{5 - item_id} hours", row_id),
            )
        conn.commit()

    result = asyncio.run(list_review_queue(state=state, sort=sort, limit=2))
    expected = [4, 3] if sort == "recent" else [3, 2]
    assert [row["feed_item_id"] for row in result["items"]] == expected
    assert result["count"] == 2
    assert result["sort"] == sort
    full = asyncio.run(list_review_queue(state=state, sort=sort, limit=10))
    assert result["items"] == full["items"][:2]
    if sort == "border":
        assert full["items"][-1]["composite_score"] is None


def test_selector_keeps_score_order_and_filters(tmp_path):
    with fs.open_triage_conn(tmp_path / "triage.db") as conn:
        for item_id, library_id, hours_ago, score in [
            (1, 2, 1, 3.0), (2, 2, 2, 4.0), (3, 3, 1, 5.0), (4, 2, 48, 4.5),
        ]:
            row_id = fs.record_decision(
                conn, run_id="order-test",
                feed_item={"feed_library_id": library_id, "item_id": item_id},
                decision=fs.DECISION_TRIAGED_PENDING, decision_reason="test", composite_score=score,
            )
            conn.execute(
                "UPDATE processed_feed_items SET created_at = datetime('now', ?) WHERE id = ?",
                (f"-{hours_ago} hours", row_id),
            )
        assert [row["feed_item_id"] for row in fs.select_pending_triaged(conn, limit=2)] == [3, 2]
        assert [row["feed_item_id"] for row in fs.select_pending_triaged(
            conn, feed_library_ids=[2], limit=2,
        )] == [2, 1]
        assert [row["feed_item_id"] for row in fs.select_by_decisions(
            conn, decisions=[fs.DECISION_TRIAGED_PENDING], feed_library_ids=[2],
            since_hours=None, limit=2,
        )] == [4, 2]
        for sort in ("recent", "border"):
            assert [row["feed_item_id"] for row in fs.select_by_decisions(
                conn, decisions=[fs.DECISION_TRIAGED_PENDING], feed_library_ids=[2],
                sort=sort, limit=2,
            )] == [1, 2]
        with pytest.raises(ValueError, match="Unknown feed sort"):
            fs.select_by_decisions(conn, decisions=[fs.DECISION_TRIAGED_PENDING], sort="typo")


@pytest.mark.parametrize("sort", ["recent", "border"])
def test_review_sort_breaks_equal_timestamp_and_score_ties_by_id(tmp_path, sort):
    with fs.open_triage_conn(tmp_path / "triage.db") as conn:
        for item_id in (1, 2):
            fs.record_decision(
                conn, run_id="order-test",
                feed_item={"feed_library_id": 2, "item_id": item_id},
                decision=fs.DECISION_AWAITING_REVIEW, decision_reason="test", composite_score=3.5,
            )
        conn.execute("UPDATE processed_feed_items SET created_at = '2026-09-01 12:00:00'")
        rows = fs.select_by_decisions(
            conn, decisions=[fs.DECISION_AWAITING_REVIEW], since_hours=None, sort=sort, limit=1,
        )
        assert [row["feed_item_id"] for row in rows] == [2]
