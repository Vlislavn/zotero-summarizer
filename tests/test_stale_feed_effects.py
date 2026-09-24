"""A replayed positive UUID cannot re-add a paper after a newer rejection."""
from __future__ import annotations

from tests.test_offline_sync import _db, _mutation
from zotero_summarizer.services.golden import verdict_effects
from zotero_summarizer.services.sync import service
from zotero_summarizer.storage import feeds, repositories


def test_legacy_negative_key_cancels_only_its_pending_stable_family(tmp_path):
    db = _db(tmp_path)
    with feeds.open_triage_conn(db) as conn:
        feeds.record_decision(conn, run_id="legacy", feed_item={
            "feed_library_id": 1, "item_id": 71, "guid": "legacy-add", "title": "Paper",
        }, decision=feeds.DECISION_USER_APPROVED, composite_score=3.0)
        conn.commit()
    assert verdict_effects.cancel_pending_feed_add(db, "feed:71") == 1
    with feeds.open_triage_conn(db) as conn:
        row = conn.execute("SELECT decision FROM processed_feed_items").fetchone()
    assert row[0] == "user_rejected"


def test_other_legacy_sibling_rejection_blocks_stale_positive_replay(tmp_path, monkeypatch):
    from zotero_summarizer.services.library import _review_cache
    from zotero_summarizer.services.triage import daily_actions

    db = _db(tmp_path)
    with feeds.open_triage_conn(db) as conn:
        for fid in (17, 18):
            feeds.record_decision(conn, run_id="x", feed_item={
                "feed_library_id": 1, "item_id": fid, "guid": "same-paper", "title": "Paper",
            }, decision=feeds.DECISION_TRIAGED_PENDING)
        conn.execute("UPDATE processed_feed_items SET created_at='2099-01-01 00:00:00' WHERE feed_item_id=18")
        key = conn.execute("SELECT stable_feed_key FROM processed_feed_items WHERE feed_item_id=18").fetchone()[0]
        conn.commit()
    monkeypatch.setattr(_review_cache, "get_current_review", lambda _: {"digest": {"tldr": "Reviewed"}})
    monkeypatch.setattr(daily_actions, "_db_path", lambda: db)
    monkeypatch.setattr(daily_actions, "_open_optional_writer", lambda: (None, RuntimeError("offline")))
    monkeypatch.setattr(service, "_log_applied_verdict", lambda *_: None)
    monkeypatch.setattr(verdict_effects, "append_training_row", lambda *_: None)
    monkeypatch.setattr(verdict_effects, "mirror_current_verdict", lambda *_a, **_kw: None)
    keep = _mutation(key, "verdict", "must_read", 0)
    assert service.push(db, [keep])["results"][0]["status"] == "applied"
    assert service.push(db, [_mutation("feed:17", "verdict", "dont_read", 0)])["results"][0]["status"] == "applied"
    assert service.push(db, [keep])["results"][0]["status"] == "already_applied"
    with feeds.open_triage_conn(db) as conn:
        row = conn.execute("SELECT decision, zotero_sync_status FROM processed_feed_items WHERE feed_item_id=18").fetchone()
    assert tuple(row) == ("user_rejected", "cancelled")


def test_offline_legacy_positive_reauthorizes_stable_negative(tmp_path, monkeypatch):
    from zotero_summarizer.services.triage import daily_actions

    db = _db(tmp_path)
    with feeds.open_triage_conn(db) as conn:
        feeds.record_decision(conn, run_id="x", feed_item={
            "feed_library_id": 1, "item_id": 17, "guid": "same-paper", "title": "Paper",
        }, decision=feeds.DECISION_USER_REJECTED)
        stable = conn.execute("SELECT stable_feed_key FROM processed_feed_items").fetchone()[0]
        conn.commit()
    repositories.insert_or_update_label_verdict(db, item_key=stable,
        original_derived_priority="should_read", user_priority="dont_read", comment="")
    monkeypatch.setattr(service, "require_feed_review", lambda *_: None)
    monkeypatch.setattr(service, "_log_applied_verdict", lambda *_: None)
    monkeypatch.setattr(verdict_effects, "append_training_row", lambda *_: None)
    monkeypatch.setattr(verdict_effects, "mirror_current_verdict", lambda *_a, **_kw: None)
    calls = []
    monkeypatch.setattr(daily_actions, "materialize_feed_verdict",
        lambda *args, **kwargs: calls.append(args) or {"added": False, "status": "zotero_unavailable", "zotero_key": None})
    assert service.push(db, [_mutation("feed:17", "verdict", "must_read", 0)])["results"][0]["status"] == "applied"
    assert calls == [("feed:17", "must_read")]


def test_new_positive_verdict_reauthorizes_a_previously_cancelled_add(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from zotero_summarizer.services.library import _review_cache, review_materialize
    from zotero_summarizer.services.library.review_eligibility import require_review
    from zotero_summarizer.services.triage.feeds import _daily_materialize

    db = _db(tmp_path)
    with feeds.open_triage_conn(db) as conn:
        feeds.record_decision(conn, run_id="retry", feed_item={
            "feed_library_id": 1, "item_id": 91, "guid": "resurrect-by-human", "title": "Paper",
        }, decision=feeds.DECISION_USER_APPROVED, composite_score=3.0)
        conn.commit()
        row = dict(conn.execute("SELECT * FROM processed_feed_items").fetchone())
    key = row["stable_feed_key"]
    repositories.insert_or_update_label_verdict(db, item_key=key,
        original_derived_priority="should_read", user_priority="dont_read", comment="reject")
    assert verdict_effects.cancel_pending_feed_add(db, key) == 1
    repositories.insert_or_update_label_verdict(db, item_key=key,
        original_derived_priority="should_read", user_priority="must_read", comment="now keep")
    monkeypatch.setattr(_review_cache, "get_current_review", lambda _: {"digest": {"tldr": "Reviewed"}})
    monkeypatch.setattr(review_materialize, "get_settings",
                        lambda: SimpleNamespace(triage_db_path=db, zotero_data_dir=tmp_path))
    monkeypatch.setattr(_daily_materialize, "ZoteroReader",
                        lambda *_: SimpleNamespace(get_feed_items=lambda **_: []))
    writes = []

    class Writer:
        def apply_feed_materialization(self, **kwargs):
            writes.append(kwargs["new_item_key"])

    new_key = review_materialize.materialize_row(row, writer=Writer(), used_keys=set(),
        label_priority="must_read", review_proof=require_review(row))
    assert writes == [new_key]
    with feeds.open_triage_conn(db) as conn:
        assert conn.execute("SELECT decision FROM processed_feed_items").fetchone()[0] == "selected"


def test_stale_positive_replay_cannot_revive_pending_add(tmp_path, monkeypatch):
    db = _db(tmp_path)
    with feeds.open_triage_conn(db) as conn:
        feeds.record_decision(conn, run_id="sync", feed_item={
            "feed_library_id": 1, "item_id": 17, "guid": "offline-review", "title": "Paper",
        }, decision=feeds.DECISION_TRIAGED_PENDING, composite_score=3.0)
        conn.commit()
        key = conn.execute("SELECT stable_feed_key FROM processed_feed_items").fetchone()[0]
    monkeypatch.setattr(service, "require_feed_review", lambda *_: None)
    monkeypatch.setattr(service, "_log_applied_verdict", lambda *_: None)
    monkeypatch.setattr(verdict_effects, "append_training_row", lambda *_: None)
    monkeypatch.setattr(verdict_effects, "mirror_current_verdict", lambda *_a, **_kw:
                        {"label_written": False, "note_written": False})
    calls = []

    def materialize(_key, priority, **kwargs):
        calls.append(priority)
        return {"added": False, "zotero_key": None,
                "status": "zotero_unavailable" if priority != "dont_read" else "not_applicable"}

    from zotero_summarizer.services.triage import daily_actions
    monkeypatch.setattr(daily_actions, "materialize_feed_verdict", materialize)
    keep = _mutation(key, "verdict", "must_read", 0)
    assert service.push(db, [keep])["results"][0]["status"] == "applied"
    with feeds.open_triage_conn(db) as conn:
        conn.execute("UPDATE processed_feed_items SET decision='user_approved', "
                     "zotero_sync_status='pending', final_outcome='kept_unread'")
        conn.commit()
    reject = _mutation(key, "verdict", "dont_read", 1)
    assert service.push(db, [reject])["results"][0]["status"] == "applied"
    assert service.push(db, [keep])["results"][0]["status"] == "already_applied"
    assert calls == ["must_read", "dont_read"]
    assert repositories.get_label_verdict(db, key)["user_priority"] == "dont_read"
    with feeds.open_triage_conn(db) as conn:
        row = conn.execute(
            "SELECT decision, zotero_sync_status, final_outcome, materialized_zotero_key "
            "FROM processed_feed_items"
        ).fetchone()
    assert tuple(row) == ("user_rejected", "cancelled", None, None)
