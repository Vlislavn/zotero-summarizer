"""Two real SQLite stores: durable planning survives retry and concurrent writers."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest

from tests._zotero_fixtures import build_zotero_db
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.services.library import review_materialize
from zotero_summarizer.services.triage.feeds import _daily_materialize as dm
from zotero_summarizer.storage import feeds


@pytest.fixture
def stores(tmp_path, monkeypatch):
    triage = tmp_path / "triage.db"
    zotero = build_zotero_db(tmp_path / "zotero")
    settings = SimpleNamespace(triage_db_path=triage, zotero_data_dir=zotero.parent)
    monkeypatch.setattr(review_materialize, "get_settings", lambda: settings)
    monkeypatch.setattr(dm, "get_settings", lambda: settings)
    monkeypatch.setattr(dm, "_triage_conn", lambda: feeds.open_triage_conn(triage))
    monkeypatch.setattr(ZoteroWriter, "is_connector_running", lambda self: False)
    # Metadata acquisition is outside recovery; the two database writers stay real.
    payload = lambda row: {"title": row["title"], "abstract": "A real abstract."}
    monkeypatch.setattr(dm, "_feed_payload_from_row", payload)
    monkeypatch.setattr("zotero_summarizer.services.triage.feeds._feed_payload_from_row", payload)
    with feeds.open_triage_conn(triage) as conn:
        rid = feeds.record_decision(
            conn, run_id="seed", feed_item={
                "feed_library_id": 2, "item_id": 400, "guid": "recovery", "title": "Recovery paper",
            }, decision=feeds.DECISION_TRIAGED_PENDING, composite_score=4.0,
            reading_priority="should_read",
        )
        conn.commit()
    return triage, zotero, rid


def _row(stores):
    with feeds.open_triage_conn(stores[0]) as conn:
        return feeds.get_processed_feed_item_by_pk(conn, stores[2])


def _call(lane, row, writer, run="first"):
    if lane == "review":
        return review_materialize.materialize_row(row, writer=writer, used_keys=set(), reason=run)
    pick = dm._PendingScoredRow(4.0, 0.0, False, row, "2:400")
    return dm.materialize_pick(
        pick, writer=writer, run_id=run, used_keys=set(),
        ctx=dm._MaterializeCtx("Inbox", "black-swan", 7, "selected"),
    )


def _zotero_counts(stores):
    with sqlite3.connect(stores[1]) as conn:
        return tuple(conn.execute(
            "SELECT (SELECT COUNT(*) FROM items i JOIN itemTypes t USING(itemTypeID) "
            "WHERE i.libraryID=1 AND t.typeName='journalArticle'), "
            "(SELECT COUNT(*) FROM itemNotes)"
        ).fetchone())


@pytest.mark.parametrize("lane", ["review", "daily"])
def test_post_zotero_failure_reuses_committed_plan_after_reload(stores, lane):
    with sqlite3.connect(stores[0]) as conn:
        conn.execute("CREATE TRIGGER fail_finalize BEFORE UPDATE OF materialized_zotero_key "
                     "ON processed_feed_items BEGIN SELECT RAISE(ABORT, 'post-Zotero failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="post-Zotero failure"):
        _call(lane, _row(stores), ZoteroWriter(stores[1].parent))

    failed = _row(stores)
    assert failed["planned_zotero_key"]
    assert failed["materialized_zotero_key"] is None
    assert failed["decision"] == feeds.DECISION_TRIAGED_PENDING
    assert _zotero_counts(stores) == (1, 1)
    with sqlite3.connect(stores[0]) as conn:
        conn.execute("DROP TRIGGER fail_finalize")

    # New row + writer, different run/note HTML: no in-memory identity survives.
    key = _call(lane, _row(stores), ZoteroWriter(stores[1].parent), run="retry")
    assert key == failed["planned_zotero_key"] == _row(stores)["materialized_zotero_key"]
    assert _zotero_counts(stores) == (1, 1)


@pytest.mark.parametrize("lane", ["review", "daily"])
def test_plan_failure_prevents_zotero_write(stores, lane):
    with sqlite3.connect(stores[0]) as conn:
        conn.execute("CREATE TRIGGER fail_plan BEFORE UPDATE OF planned_zotero_key "
                     "ON processed_feed_items BEGIN SELECT RAISE(ABORT, 'plan failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="plan failure"):
        _call(lane, _row(stores), ZoteroWriter(stores[1].parent))
    assert _zotero_counts(stores) == (0, 0)
    assert _row(stores)["planned_zotero_key"] is None


@pytest.mark.parametrize("lanes", [("review", "review"), ("daily", "daily"), ("review", "daily")])
def test_concurrent_materializers_share_one_key_and_preserve_resolved_outcome(stores, monkeypatch, lanes):
    stale = _row(stores)
    barrier = Barrier(2)
    apply = ZoteroWriter.apply_feed_materialization

    def overlap(writer, **kwargs):
        barrier.wait(timeout=15)
        return apply(writer, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(ZoteroWriter, "apply_feed_materialization", overlap)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_call, lane, dict(stale), ZoteroWriter(stores[1].parent), lane)
                       for lane in lanes]
            keys = [future.result(timeout=25) for future in futures]
    assert keys[0] == keys[1] == _row(stores)["materialized_zotero_key"]
    assert _zotero_counts(stores) == (1, 1)

    with sqlite3.connect(stores[0]) as conn:
        conn.execute("UPDATE processed_feed_items SET final_outcome='trashed', "
                     "decision='user_rejected', decision_reason='later user decision', "
                     "outcome_eligible_at='2000-01-01', outcome_detected_at='2000-01-02'")
    with sqlite3.connect(stores[1]) as conn:
        conn.execute("DELETE FROM collectionItems")
        conn.execute("DELETE FROM itemTags")
    _call(lanes[0], stale, ZoteroWriter(stores[1].parent), run="late-retry")
    persisted = _row(stores)
    assert (persisted["final_outcome"], persisted["outcome_eligible_at"], persisted["outcome_detected_at"]) == (
        "trashed", "2000-01-01", "2000-01-02",
    )
    assert (persisted["decision"], persisted["decision_reason"]) == ("user_rejected", "later user decision")
    assert _zotero_counts(stores) == (1, 1)
    with sqlite3.connect(stores[1]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM collectionItems").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM itemTags").fetchone()[0] == 0


@pytest.mark.parametrize("lane", ["review", "daily"])
def test_zotero_transaction_failure_leaves_plan_for_complete_retry(stores, lane):
    with sqlite3.connect(stores[1]) as conn:
        conn.execute("CREATE TRIGGER fail_note BEFORE INSERT ON itemNotes "
                     "BEGIN SELECT RAISE(ABORT, 'note failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="note failure"):
        _call(lane, _row(stores), ZoteroWriter(stores[1].parent))
    failed = _row(stores)
    assert failed["planned_zotero_key"]
    assert failed["materialized_zotero_key"] is None
    assert _zotero_counts(stores) == (0, 0)
    with sqlite3.connect(stores[1]) as conn:
        conn.execute("DROP TRIGGER fail_note")
    assert _call(lane, _row(stores), ZoteroWriter(stores[1].parent)) == failed["planned_zotero_key"]
    assert _zotero_counts(stores) == (1, 1)
