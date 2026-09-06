"""Online/offline mirrors deliver current note intent, including replay and clearing."""
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from tests.test_golden_input_boundaries import _app
from tests.test_offline_sync import _mutation
from tests.test_verdict_mirror_retraction import _setup
from zotero_summarizer.api.routes import sync as sync_routes
from zotero_summarizer.integrations.zotero_write import ZoteroWriteError
from zotero_summarizer.services.golden import verdict_effects
from zotero_summarizer.services.sync import service
from zotero_summarizer.services.zotero.pending import build_user_note_html
from zotero_summarizer.storage import repositories as db


def _note(zdb):
    with closing(sqlite3.connect(zdb)) as conn:
        rows = conn.execute("SELECT note FROM itemNotes").fetchall()
    assert len(rows) == 1
    return rows[0][0]


@pytest.mark.parametrize("operation,value", [("set", "new <note>"), ("delete", None)])
def test_old_uuid_replay_never_replaces_newer_note_or_clear(tmp_path, monkeypatch, operation, value):
    path, zdb, *_ = _setup(tmp_path, monkeypatch, "offline")
    old = _mutation("PARENT", "review_note", "old note", 0)
    first = service.push(path, [old])["results"][0]
    newer = _mutation("PARENT", "review_note", value, first["applied_revision"], operation=operation)
    assert service.push(path, [newer])["results"][0]["status"] == "applied"
    expected = build_user_note_html("" if value is None else value)
    assert _note(zdb) == expected

    assert service.push(path, [old])["results"][0]["status"] == "already_applied"

    assert db.get_review_note(path, "PARENT") == value
    assert _note(zdb) == expected


def test_online_delayed_mirror_reads_newer_committed_note(tmp_path, monkeypatch):
    path, zdb, *_ = _setup(tmp_path, monkeypatch, "online")
    mirror = verdict_effects.mirror_review_note

    def delayed(*args):
        db.upsert_review_note(path, "PARENT", "newer online save")
        return mirror(*args)

    monkeypatch.setattr(verdict_effects, "mirror_review_note", delayed)
    with TestClient(_app()) as client:
        response = client.post("/api/golden/review-note", json={"item_key": "PARENT", "note": "old request"})

    assert response.status_code == 200, response.text
    assert response.json()["note_written"] is True
    assert db.get_review_note(path, "PARENT") == "newer online save"
    assert _note(zdb) == build_user_note_html("newer online save")


@pytest.mark.parametrize("deleted", [False, True])
def test_library_replay_respects_newer_feed_alias_intent(tmp_path, monkeypatch, deleted):
    path, zdb, *_ = _setup(tmp_path, monkeypatch, "offline")
    stable = "feed:g:" + "a" * 64
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            "INSERT INTO processed_feed_items (feed_library_id, feed_item_id, stable_feed_key, "
            "title, guid, decision, run_id, materialized_zotero_key) "
            "VALUES (1, 42, ?, 'Paper', 'guid', 'selected', 'test', 'PARENT')", (stable,),
        )
    old = _mutation("PARENT", "review_note", "old library note", 0)
    first = service.push(path, [old])["results"][0]
    db.apply_sync_mutation(path, _mutation(
        stable, "review_note", None if deleted else "new alias note", first["applied_revision"],
        operation="delete" if deleted else "set",
    ))

    assert service.push(path, [old])["results"][0]["status"] == "already_applied"

    assert _note(zdb) == build_user_note_html("" if deleted else "new alias note")


def test_failed_delivery_propagates_and_same_uuid_retries_current_note(tmp_path, monkeypatch):
    path, zdb, _, _, writer, _ = _setup(tmp_path, monkeypatch, "offline")
    old = _mutation("PARENT", "review_note", "old note", 0)
    monkeypatch.setattr(writer, "is_connector_running", lambda: True)

    with pytest.raises(ZoteroWriteError, match="Zotero is open"):
        service.push(path, [old])

    assert db.get_review_note(path, "PARENT") == "old note"
    db.upsert_review_note(path, "PARENT", "new note after failed mirror")
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)
    assert service.push(path, [old])["results"][0]["status"] == "already_applied"
    assert _note(zdb) == build_user_note_html("new note after failed mirror")


def test_mirror_holds_writer_lock_until_external_delivery_and_releases_it(tmp_path, monkeypatch):
    path, zdb, _, _, writer, _ = _setup(tmp_path, monkeypatch, "offline")
    apply = writer.apply_changes
    checked = []

    def guarded(*args, **kwargs):
        with closing(sqlite3.connect(path, timeout=0)) as conn:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                conn.execute("BEGIN IMMEDIATE")
        checked.append(True)
        return apply(*args, **kwargs)

    monkeypatch.setattr(writer, "apply_changes", guarded)
    service.push(path, [_mutation("PARENT", "review_note", "locked delivery", 0)])

    assert checked == [True]
    assert _note(zdb) == build_user_note_html("locked delivery")
    db.upsert_review_note(path, "PARENT", "later commit")
    assert db.get_review_note(path, "PARENT") == "later commit"


@pytest.mark.parametrize("lane", ["online", "offline"])
def test_http_delivery_failure_is_not_a_successful_sync_receipt(tmp_path, monkeypatch, lane):
    path, _, _, _, writer, _ = _setup(tmp_path, monkeypatch, lane)
    monkeypatch.setattr(writer, "is_connector_running", lambda: True)
    monkeypatch.setattr(sync_routes, "_db_path", lambda: path)
    app = _app()
    app.include_router(sync_routes.router)
    endpoint, body = (("/api/golden/review-note", {"item_key": "PARENT", "note": "durable"})
                      if lane == "online" else ("/api/sync/push", {
                          "protocol": 1, "mutations": [_mutation("PARENT", "review_note", "durable", 0)],
                      }))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(endpoint, json=body)

    assert response.status_code == 503, response.text
    assert response.json()["error"] == "zotero_write_failed"
    assert db.get_review_note(path, "PARENT") == "durable"
