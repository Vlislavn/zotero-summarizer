"""A positive Today pill takes the paper-page Add path; Undo never deletes a Zotero item (#207)."""
import sqlite3
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_daily_actions import env, _record  # noqa: F401 - shared isolated fixture
from zotero_summarizer.api.errors import install_error_handlers
from zotero_summarizer.api.routes import daily
from zotero_summarizer.integrations.zotero_write import ZoteroWriteError
from zotero_summarizer.services.golden import label_verdicts
from zotero_summarizer.services.library import review_materialize
from zotero_summarizer.services.triage import daily_actions, daily_members
from zotero_summarizer.services.triage.feeds import _daily_materialize


@pytest.fixture
def writer(env, monkeypatch):  # noqa: F811
    db, _labels = env
    fake = Mock()
    fake.apply_feed_materialization.side_effect = lambda **kw: {"item_key": kw["new_item_key"]}
    monkeypatch.setattr(daily, "_db_path", lambda: db)
    monkeypatch.setattr(daily_actions, "ZoteroWriter", lambda _: fake)
    monkeypatch.setattr(review_materialize, "get_settings", daily_actions.get_settings)
    monkeypatch.setattr(_daily_materialize, "ZoteroReader", lambda _: Mock(get_feed_items=lambda **_: []))
    monkeypatch.setattr(daily_actions.deep_review, "copy_review", lambda *args: None)
    monkeypatch.setattr(daily_actions, "_attach_fulltext_best_effort", lambda _: {"attached": 0})
    monkeypatch.setattr(daily_actions, "_carry_renders_best_effort", lambda _: None)
    monkeypatch.setattr(label_verdicts, "log_committed_transition", lambda **_: None)
    return fake


@pytest.fixture
def client():
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(daily.router)
    with TestClient(app, raise_server_exceptions=False) as http:
        yield http


def _row(db, pk):
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT decision, zotero_sync_status, materialized_zotero_key, planned_zotero_key "
            "FROM processed_feed_items WHERE id=?", (pk,)).fetchone()


def _pill(client, pk, priority="must_read", **extra):
    reply = client.post("/api/daily/verdict", json={"item_id": pk, "user_priority": priority, **extra})
    assert reply.status_code == 200, reply.text
    return reply.json()


def test_positive_pill_saves_the_verdict_and_adds_exactly_once(env, writer, client):
    db, _labels = env
    pk = _record(db, 401)
    first = _pill(client, pk)
    assert (first["add_attempted"], first["add_status"], first["added_to_library"]) == (True, "added", True)
    assert first["zotero_key"] == _row(db, pk)[2]
    again = _pill(client, pk, reasons=["has_takeaway"])  # the reason-chip re-submit of the same decision
    assert (again["add_attempted"], again["add_status"]) == (False, "unchanged")
    assert again["reasons_saved"] is True
    assert writer.apply_feed_materialization.call_count == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT user_priority FROM label_verdicts").fetchall() == [("must_read",)]


def test_pill_with_zotero_open_parks_the_add_and_the_banner_sees_it(env, writer, client, monkeypatch):
    db, _labels = env
    pk = _record(db, 402)
    writer.apply_feed_materialization.side_effect = ZoteroWriteError("Zotero is running; close it")
    reply = _pill(client, pk, "should_read")
    assert (reply["add_attempted"], reply["add_status"], reply["added_to_library"]) == (True, "zotero_pending", False)
    assert _row(db, pk)[:3] == ("user_approved", "pending", None)
    monkeypatch.setattr(daily_members, "settings", daily_actions.get_settings)
    monkeypatch.setattr(daily_members, "usable_reviews_for_keys", lambda _keys: {})
    member = daily_members.read_members([pk])["members"][0]  # what the Today banner polls
    assert member["zotero_sync_status"] == "pending" and member["user_priority"] == "should_read"


def test_not_interested_attempts_no_add_and_cancels_a_parked_one(env, writer, client):
    db, _labels = env
    pk = _record(db, 403)
    writer.apply_feed_materialization.side_effect = ZoteroWriteError("Zotero is running")
    _pill(client, pk, "could_read")
    reply = _pill(client, pk, "dont_read")
    assert (reply["add_attempted"], reply["add_status"]) == (False, "not_applicable")
    assert writer.apply_feed_materialization.call_count == 1  # only the earlier positive attempt
    assert _row(db, pk)[:2] == ("user_rejected", "cancelled")


def test_undo_after_a_parked_add_releases_it(env, writer, client):
    db, _labels = env
    pk = _record(db, 404)
    writer.apply_feed_materialization.side_effect = ZoteroWriteError("Zotero is running")
    _pill(client, pk)
    undone = client.post("/api/daily/verdict/undo", json={"item_id": pk}).json()
    assert (undone["deleted"], undone["released_pending_add"], undone["item_stays_in_zotero"]) == (True, 1, False)
    assert _row(db, pk)[:2] == ("triaged_pending", None)


def test_undo_after_a_materialized_add_withdraws_the_label_but_keeps_the_item(env, writer, client):
    db, _labels = env
    pk = _record(db, 405)
    key = _pill(client, pk)["zotero_key"]
    undone = client.post("/api/daily/verdict/undo", json={"item_id": pk}).json()
    assert (undone["deleted"], undone["item_stays_in_zotero"], undone["zotero_key"]) == (True, True, key)
    assert undone["released_pending_add"] == 0
    assert _row(db, pk)[2] == key
    assert [c[0] for c in writer.method_calls] == ["apply_feed_materialization"]  # nothing deleted in Zotero
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM label_verdicts").fetchone()[0] == 0
