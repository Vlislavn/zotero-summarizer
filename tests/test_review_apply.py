"""Apply-all must preserve explicit labels and process the entire approval set."""
import asyncio
import sqlite3

import pytest

from test_review_workflow import _insert_awaiting, patched_settings  # noqa: F401
from tests._zotero_fixtures import add_feed_item, build_zotero_db
from zotero_summarizer.api.routes.review import apply_all
from zotero_summarizer.integrations import zotero_write
from zotero_summarizer.services.library import review, review_materialize
from zotero_summarizer.services.triage import feeds as feed_service
from zotero_summarizer.services.triage.feeds import _daily_materialize
from zotero_summarizer.storage import feeds as fs, repositories
from zotero_summarizer.storage.feed_identity import row_feed_keys


@pytest.mark.parametrize("priority", ["must_read", "should_read", "could_read"])
def test_apply_all_writes_review_label_into_real_zotero(patched_settings, monkeypatch, priority):
    path = patched_settings
    zotero_db = build_zotero_db(path / "zotero")
    add_feed_item(zotero_db, feed_library_id=2, item_id=101, guid="guid-101", title="Reviewed paper")
    monkeypatch.setattr(_daily_materialize, "get_settings", review_materialize.get_settings)
    monkeypatch.setattr(zotero_write.ZoteroWriter, "is_connector_running", lambda self: False)
    with fs.open_triage_conn(path / "triage.db") as conn:
        row_id = _insert_awaiting(conn)
        conn.commit()
    review.relabel(row_id, priority)

    assert asyncio.run(apply_all())["applied"] == 1
    with fs.open_triage_conn(path / "triage.db") as conn:
        row = conn.execute("SELECT * FROM processed_feed_items WHERE id = ?", (row_id,)).fetchone()
        assert row["decision"] == fs.DECISION_SELECTED
        key = row["materialized_zotero_key"]
    with sqlite3.connect(zotero_db) as conn:
        tags = {row[0] for row in conn.execute(
            "SELECT t.name FROM tags t JOIN itemTags it USING(tagID) "
            "JOIN items i USING(itemID) WHERE i.key = ?", (key,),
        )}
    assert {tag for tag in tags if tag.startswith("label:")} == {f"label:{priority}"}
    assert list((path / "zotero").glob("zotero.sqlite.backup_*"))
    assert asyncio.run(apply_all())["applied"] == 0


@pytest.mark.parametrize("source", ["user", "machine_add", None])
@pytest.mark.parametrize("legacy", [False, True])
def test_pending_apply_uses_verdict_not_machine_priority(patched_settings, monkeypatch, source, legacy):
    path = patched_settings
    with fs.open_triage_conn(path / "triage.db") as conn:
        row_id = _insert_awaiting(conn)
        conn.execute("UPDATE processed_feed_items SET decision = ?, reading_priority = 'dont_read'",
                     (fs.DECISION_USER_APPROVED,))
        row = dict(conn.execute("SELECT * FROM processed_feed_items WHERE id = ?", (row_id,)).fetchone())
        conn.commit()
    if source is not None:
        repositories.insert_or_update_label_verdict(
            path / "triage.db", item_key="feed:101" if legacy else row_feed_keys(row)[0],
            original_derived_priority="dont_read", user_priority="must_read", comment="", source=source,
        )
    captured = []

    class Writer:
        def apply_feed_materialization(self, **kwargs):
            captured.append(kwargs)

    monkeypatch.setattr(zotero_write, "ZoteroWriter", lambda *args: Writer())
    monkeypatch.setattr(feed_service, "_feed_payload_from_row", lambda row: {"title": row["title"]})
    assert review.apply_all_approved()["applied"] == 1
    assert {tag for tag in captured[0]["tags"] if tag.startswith("label:")} == (
        {"label:must_read"} if source == "user" else set()
    )
    if source == "user":
        assert "Must Read" in captured[0]["note_html"]


@pytest.mark.parametrize("available", [False, True])
def test_apply_all_has_no_hidden_row_cap(patched_settings, monkeypatch, available):
    path = patched_settings
    with fs.open_triage_conn(path / "triage.db") as conn:
        conn.executemany(
            "INSERT INTO processed_feed_items "
            "(feed_library_id, feed_item_id, guid, title, decision, run_id, created_at) "
            "VALUES (2, ?, ?, 'Approved', 'user_approved', 'cap-test', '2020-01-01')",
            [(n, str(n)) for n in range(1, 5002)],
        )
        conn.commit()
        assert len(fs.select_by_decisions(conn, decisions=[fs.DECISION_USER_APPROVED],
                                          since_hours=None, limit=9999)) == 5000
        assert len(fs.select_by_decisions(conn, decisions=[fs.DECISION_USER_APPROVED],
                                          since_hours=None)) == 1000
    calls = []
    if available:
        monkeypatch.setattr(zotero_write, "ZoteroWriter", lambda *args: object())
        monkeypatch.setattr(review_materialize, "materialize_row", lambda row, **kwargs: calls.append(row["id"]))
    result = review.apply_all_approved()
    assert result["applied" if available else "pending_sync"] == 5001
    if available:
        assert len(set(calls)) == 5001
    else:
        with fs.open_triage_conn(path / "triage.db") as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM processed_feed_items WHERE decision = 'user_approved' "
                "AND zotero_sync_status = 'pending' AND final_outcome = ?",
                (fs.OUTCOME_KEPT_UNREAD_APP,),
            ).fetchone()[0] == 5001


@pytest.mark.parametrize("failure", ["writer", "row"])
def test_unexpected_apply_error_propagates(patched_settings, monkeypatch, failure):
    with fs.open_triage_conn(patched_settings / "triage.db") as conn:
        _insert_awaiting(conn)
        conn.execute("UPDATE processed_feed_items SET decision = 'user_approved'")
        conn.commit()

    def fail(*args, **kwargs):
        raise RuntimeError("unexpected apply failure")

    monkeypatch.setattr(zotero_write, "ZoteroWriter", fail if failure == "writer" else lambda *args: object())
    monkeypatch.setattr(review_materialize, "materialize_row", fail)
    with pytest.raises(RuntimeError, match="unexpected apply failure"):
        asyncio.run(apply_all())


def test_changed_negative_verdict_blocks_stale_approval(patched_settings, monkeypatch):
    with fs.open_triage_conn(patched_settings / "triage.db") as conn:
        row_id = _insert_awaiting(conn)
        conn.commit()
        row = dict(conn.execute("SELECT * FROM processed_feed_items WHERE id = ?", (row_id,)).fetchone())
    review.approve(row_id)
    repositories.insert_or_update_label_verdict(
        patched_settings / "triage.db", item_key=row_feed_keys(row)[0],
        original_derived_priority="should_read", user_priority="dont_read", comment="Changed my mind",
    )
    calls = []
    monkeypatch.setattr(zotero_write, "ZoteroWriter", lambda *args: object())
    monkeypatch.setattr(review_materialize, "materialize_row", lambda row, **kwargs: calls.append(row))
    with pytest.raises(ValueError, match="dont_read"):
        review.apply_all_approved()
    assert calls == []
