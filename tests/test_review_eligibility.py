"""The review gate is checked before any app-controlled feed Add side effects."""
from __future__ import annotations

import pytest

from zotero_summarizer.services.library import _review_cache, review, review_eligibility
from zotero_summarizer.services.triage import daily_actions
from zotero_summarizer.storage import feeds as fs, repositories as repo
from tests.test_daily_actions import env, _record, _decision  # noqa: F401 — shared fixture


def test_add_requires_usable_review_before_label_pending_or_writer(env, monkeypatch):
    db, appended = env
    pk = _record(db, 99)
    monkeypatch.setattr(_review_cache, "get_current_review", lambda key: None)
    result = daily_actions.add_to_library([pk])
    assert result["added"] == result["pending_sync"] == 0
    assert result["failed"] == [{"id": pk, "title": "P99", "code": "review_required",
                                 "error": "Generate a review before adding this paper to the library."}]
    assert _decision(db, pk) == fs.DECISION_TRIAGED_PENDING
    assert repo.get_label_verdict(db, "feed:99") is None
    assert appended == []
    assert daily_actions.trash([pk])["trashed"] == 1


def test_one_verified_add_does_not_fail_after_cache_changes_mid_request(env, monkeypatch):
    db, appended = env
    pk = _record(db, 84)
    reads = iter([{"digest": {"tldr": "Reviewed paper"}}, None])
    monkeypatch.setattr(_review_cache, "get_current_review", lambda key: next(reads))
    monkeypatch.setattr(review, "materialize_row", lambda row, **kwargs: "PAPER001")
    result = daily_actions.add_to_library([pk])
    assert result["added"] == 1 and result["failed_count"] == 0
    assert repo.get_label_verdict(db, "feed:84")["user_priority"] == "should_read"
    assert len(appended) == 1


def test_mixed_batch_only_labels_and_materializes_reviewed_items(env, monkeypatch):
    db, appended = env
    ready, blocked = _record(db, 100), _record(db, 101)
    monkeypatch.setattr(review_eligibility, "review_ready", lambda row: row["feed_item_id"] == 100)
    monkeypatch.setattr(review, "materialize_row", lambda row, **kwargs: "PAPER001")
    result = daily_actions.add_to_library([ready, blocked])
    assert result["added"] == 1 and result["pending_sync"] == 0
    assert result["failed_count"] == 1 and result["failed"][0]["code"] == "review_required"
    assert _decision(db, blocked) == fs.DECISION_TRIAGED_PENDING
    assert repo.get_label_verdict(db, "feed:101") is None
    assert len(appended) == 1


@pytest.mark.parametrize("entry", [
    None,
    {"needs_pdf": True, "digest": None},
    {"error": "login needed", "digest": {"tldr": "placeholder"}},
    {"digest": None},
    {"digest": {}},
    {"digest": {"tldr": "  ", "key_findings": []}},
    {"quality": {"grade": "A"}, "digest": {}},
])
def test_missing_or_unusable_review_cannot_unlock_add(monkeypatch, entry):
    monkeypatch.setattr(_review_cache, "get_current_review", lambda key: entry)
    with pytest.raises(review_eligibility.ReviewRequired) as exc:
        review_eligibility.require_review({"stable_feed_key": "feed:g:" + "a" * 64, "feed_item_id": 42})
    assert exc.value.error == "review_required"


def test_concurrent_rejection_cannot_be_overwritten_by_inflight_add(env, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from types import SimpleNamespace
    from zotero_summarizer.services.library import review_materialize
    from zotero_summarizer.services.triage.feeds import _daily_materialize

    db, _labels = env
    pk = _record(db, 77)
    writer_entered, release_writer, reject_started, reject_committed = Event(), Event(), Event(), Event()

    class PausingWriter:
        def apply_feed_materialization(self, **kwargs):
            writer_entered.set()
            assert release_writer.wait(8)
            return {"item_key": kwargs["new_item_key"]}

        def mark_feed_items_read(self, ids):
            return len(ids)

    monkeypatch.setattr(review_materialize, "get_settings", daily_actions.get_settings)
    monkeypatch.setattr(_daily_materialize, "ZoteroReader",
                        lambda *_: SimpleNamespace(get_feed_items=lambda **_: []))
    monkeypatch.setattr(daily_actions, "_open_optional_writer", lambda: (PausingWriter(), None))
    original_record = daily_actions._record_label

    def observe_rejection(row, priority, note, **kwargs):
        if priority == "dont_read":
            reject_started.set()
        return original_record(row, priority, note, **kwargs)

    monkeypatch.setattr(daily_actions, "_record_label", observe_rejection)
    original_decision = daily_actions._set_decision

    def observe_decision(row, decision, reason):
        original_decision(row, decision, reason)
        if decision == fs.DECISION_USER_REJECTED:
            reject_committed.set()

    monkeypatch.setattr(daily_actions, "_set_decision", observe_decision)
    monkeypatch.setattr(daily_actions.deep_review, "copy_review", lambda *a: False)
    monkeypatch.setattr(daily_actions, "_attach_fulltext_best_effort", lambda _: {"attached": 0})
    monkeypatch.setattr(daily_actions, "_carry_renders_best_effort", lambda _: None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        add = pool.submit(daily_actions.add_to_library, [pk])
        assert writer_entered.wait(5)
        reject = pool.submit(daily_actions.trash, [pk])
        assert reject_started.wait(5)
        try:
            assert not reject_committed.wait(0.5), "rejection overtook an in-flight Add"
        finally:
            release_writer.set()
        assert add.result(timeout=8)["added"] == 1
        assert reject.result(timeout=8)["trashed"] == 1
    assert _decision(db, pk) == fs.DECISION_USER_REJECTED
    assert repo.get_label_verdict(db, "feed:77")["user_priority"] == "dont_read"


def test_apply_all_reuses_one_zotero_item_for_reviewed_sibling_rows(env, monkeypatch):
    from types import SimpleNamespace
    from zotero_summarizer.integrations import zotero_write
    from zotero_summarizer.services.library import review_materialize
    from zotero_summarizer.services.triage.feeds import _daily_materialize

    db, _labels = env
    a, b = _record(db, 97), _record(db, 98)
    with fs.open_triage_conn(db) as conn:
        key = conn.execute("SELECT stable_feed_key FROM processed_feed_items WHERE id=?", (a,)).fetchone()[0]
        conn.execute("UPDATE processed_feed_items SET stable_feed_key=? WHERE id=?", (key, b))
        conn.execute("UPDATE processed_feed_items SET decision='user_approved' WHERE id IN (?,?)", (a, b))
        conn.commit()
    writes = []

    class Writer:
        def apply_feed_materialization(self, **kwargs):
            writes.append(kwargs["new_item_key"])

    monkeypatch.setattr(zotero_write, "ZoteroWriter", lambda *args: Writer())
    monkeypatch.setattr(review_materialize, "get_settings", daily_actions.get_settings)
    monkeypatch.setattr(_daily_materialize, "ZoteroReader",
                        lambda *_: SimpleNamespace(get_feed_items=lambda **_: []))
    result = review_materialize.apply_all_approved()
    assert result["applied"] == 2 and result["failed_count"] == 0
    assert len(writes) == 1
    with fs.open_triage_conn(db) as conn:
        rows = conn.execute("SELECT decision, materialized_zotero_key FROM processed_feed_items ORDER BY id").fetchall()
    assert [tuple(row) for row in rows] == [("selected", writes[0]), ("selected", writes[0])]


def test_rejection_before_writer_intent_prevents_zotero_add(env, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from types import SimpleNamespace
    from zotero_summarizer.services.library import review_materialize
    from zotero_summarizer.services.triage.feeds import _daily_materialize

    db, _labels = env
    pk = _record(db, 78)
    planned, resume = Event(), Event()
    original_reserve = fs.reserve_materialization_key

    def pause_after_plan(*args):
        key = original_reserve(*args)
        planned.set()
        assert resume.wait(5)
        return key

    class Writer:
        def apply_feed_materialization(self, **kwargs):
            raise AssertionError("rejected paper reached Zotero")

        def mark_feed_items_read(self, ids):
            return len(ids)

    monkeypatch.setattr(fs, "reserve_materialization_key", pause_after_plan)
    monkeypatch.setattr(review_materialize, "get_settings", daily_actions.get_settings)
    monkeypatch.setattr(_daily_materialize, "ZoteroReader",
                        lambda *_: SimpleNamespace(get_feed_items=lambda **_: []))
    monkeypatch.setattr(daily_actions, "_open_optional_writer", lambda: (Writer(), None))
    monkeypatch.setattr(daily_actions, "_attach_fulltext_best_effort", lambda _: {"attached": 0})
    with ThreadPoolExecutor(max_workers=2) as pool:
        add = pool.submit(daily_actions.add_to_library, [pk])
        assert planned.wait(5)
        assert daily_actions.trash([pk])["trashed"] == 1
        resume.set()
        result = add.result(timeout=8)
    assert result["added"] == result["pending_sync"] == 0
    assert result["failed"][0]["code"] == "superseded"
    assert _decision(db, pk) == fs.DECISION_USER_REJECTED


def test_one_papers_review_proof_cannot_authorize_another_row(monkeypatch):
    source = {"id": 1, "stable_feed_key": "feed:g:" + "a" * 64, "feed_item_id": 1}
    other = {"id": 2, "stable_feed_key": "feed:g:" + "b" * 64, "feed_item_id": 1}
    monkeypatch.setattr(_review_cache, "get_current_review", lambda key: {
        "digest": {"tldr": "Source review"},
    } if key == source["stable_feed_key"] else None)
    proof = review_eligibility.require_review(source)
    with pytest.raises(review_eligibility.ReviewRequired):
        review_eligibility.require_authorization(other, proof)


def test_legacy_feed_id_review_cannot_authorize_another_paper(monkeypatch):
    monkeypatch.setattr(_review_cache, "get_current_review", lambda key: (
        {"digest": {"tldr": "Other paper's review"}} if key == "feed:42" else None
    ))
    with pytest.raises(review_eligibility.ReviewRequired):
        review_eligibility.require_review({"stable_feed_key": "feed:g:" + "b" * 64,
                                           "feed_item_id": 42})


def test_negative_recommendation_is_still_a_usable_review(monkeypatch):
    monkeypatch.setattr(_review_cache, "get_current_review", lambda key: {
        "needs_pdf": False,
        "digest": {"read_decision": "skip", "key_findings": ["No independent validation"]},
    })
    review_eligibility.require_review({"stable_feed_key": "feed:g:" + "a" * 64, "feed_item_id": 42})
