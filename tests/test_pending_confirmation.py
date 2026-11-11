"""Real owned queue/native/HTTP and competing Pending confirmation boundaries."""
import asyncio
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from threading import Event
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests._zotero_fixtures import add_library_item
from tests.test_generated_write_review import boundary as generated_boundary
from tests._generated_write_fixtures import config, paper_io, published_review
from tests.test_review_cache import _isolate_cache
from tests.test_pending_approval_snapshot import KEY, _native_tags, _present, boundary
from tests.test_pending_retry import counts, presented
from zotero_summarizer import runtime
from zotero_summarizer.api.errors import APIError, install_error_handlers
from zotero_summarizer.api.routes.pending import router
from zotero_summarizer.integrations.zotero_write import ZoteroWriteError
from zotero_summarizer.models import PendingChangeApplyRequest, PendingChangeUpdateRequest
from zotero_summarizer.models.zotero_actions import preview_hash
from zotero_summarizer.services import _common
from zotero_summarizer.services.library import _review_cache
from zotero_summarizer.services.zotero import pending, pending_confirmation, zotero
from zotero_summarizer.storage import pending_actions, repositories


@pytest.fixture(autouse=True)
def refresh(monkeypatch):
    from zotero_summarizer.services import corpus

    leaf = AsyncMock(return_value=(0, 0, 0))
    monkeypatch.setattr(corpus, "refresh_corpus_items_by_keys", leaf)
    return leaf


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    install_error_handlers(app)
    transport = TestClient(app)
    try:
        yield transport
    finally:
        transport.close()


def request(rows, **flags):
    return PendingChangeApplyRequest(change_ids=[row["id"] for row in rows], previews={
        row["id"]: row["approval_preview"]["preview_sha256"] for row in rows
    }, **flags)


def apply(rows, **flags):
    return asyncio.run(pending.apply_pending_changes(request(rows, **flags)))


def queue(payload, kind="tag_changes", key=KEY):
    repositories.insert_pending_changes(key, "Owned paper", [{"change_type": kind, "payload": payload}])
    return max(presented(), key=lambda row: row["id"])


def edit(row, payload):
    return asyncio.run(pending.update_pending_change(row["id"], PendingChangeUpdateRequest(payload=payload)))


def no_effect(b):
    assert b.dispatched == [] and counts(b.db) == (0, 0)
    assert not list(b.db.parent.glob("zotero.sqlite.backup_*"))
    assert all(not any(event["event"] == "approved" for event in row["audit"])
               for row in repositories.get_pending_changes(status=None))


@pytest.mark.parametrize("field", ["note_title", "note_html"])
def test_non_tag_replacement_cannot_inherit_approval(boundary, field):
    b = boundary
    payload = {"note_title": "A", "note_html": "<p>Body A</p>"}
    row = queue(payload, "add_note")
    captured = deepcopy(row)
    changed = {**payload, field: "<p>Body B</p>" if field == "note_html" else "B"}
    assert edit(row, changed)["payload"] == changed
    assert json.loads(repositories.get_pending_changes_by_ids([row["id"]])[0]["payload_json"]) == changed
    with pytest.raises(APIError) as error:
        apply([row])
    assert error.value.status_code == 409 and row == captured
    no_effect(b)


@pytest.mark.parametrize("fault", ["wrong_id", "wrong_hash", "unknown", "failed", "retry_pending", "rejected"])
def test_force_cannot_approve_wrong_identity_or_state(boundary, monkeypatch, fault):
    b = boundary
    row = _present(b)
    req = request([row], force=True)
    monkeypatch.setattr(zotero.get_zotero_writer_or_raise(), "is_connector_running", lambda: True)
    if fault == "wrong_id":
        other = queue({"add_tags": ["other"], "remove_tags": []})
        req = PendingChangeApplyRequest(change_ids=[other["id"]], previews={
            other["id"]: row["approval_preview"]["preview_sha256"],
        }, force=True)
    elif fault == "wrong_hash":
        req.previews[row["id"]] = "0" * 64
    elif fault == "unknown":
        req = PendingChangeApplyRequest(change_ids=[99999], previews={99999: "a" * 64}, force=True)
    elif fault in {"failed", "rejected"}:
        repositories.set_pending_changes_status([row["id"]], fault)
    else:
        req.retry = True
    with pytest.raises(APIError) as error:
        asyncio.run(pending.apply_pending_changes(req))
    assert error.value.status_code == 409
    no_effect(b)


def test_force_reuses_original_current_snapshot(boundary, monkeypatch):
    row = _present(boundary)
    monkeypatch.setattr(zotero.get_zotero_writer_or_raise(), "is_connector_running", lambda: True)
    assert apply([row])["requires_force"] is True
    no_effect(boundary)
    assert apply([row], force=True)["applied"] == 1
    assert _native_tags(boundary.db) == ["pending-186-A"]


@pytest.mark.parametrize("body", [{}, {"previews": {}}, {"previews": {"1": "invalid"}},
                                   {"previews": {"1": "a" * 64, "999": "b" * 64}}])
def test_http_missing_or_malformed_approval_is_422_before_effects(boundary, body, client):
    row = _present(boundary)
    response = client.post("/api/pending/apply", json={
        "change_ids": [row["id"]], "force": True, **body,
    })
    assert response.status_code == 422
    no_effect(boundary)


def test_http_displayed_envelope_stale_refusal_manual_refresh_and_replay(boundary, client):
    row = _present(boundary)
    displayed, = client.get("/api/pending").json()["items"]
    envelope = displayed["approval_preview"]
    core = {key: value for key, value in envelope.items() if key != "preview_sha256"}
    assert set(core) == {"version", "change_id", "item_key", "change_type", "payload", "edit_revision", "target", "generated_preview_sha256"}
    assert envelope["preview_sha256"] == preview_hash(core)
    assert core["change_id"] == row["id"] and core["payload"] == json.loads(row["payload_json"])
    body = request([displayed]).model_dump(mode="json")
    changed = {"add_tags": ["HTTP-B"], "remove_tags": []}
    assert client.put(f"/api/pending/change/{row['id']}", json={"payload": changed}).status_code == 200
    assert client.post("/api/pending/apply", json=body).status_code == 409
    no_effect(boundary)
    refreshed, = client.get("/api/pending").json()["items"]
    current = request([refreshed]).model_dump(mode="json")
    applied = client.post("/api/pending/apply", json=current)
    assert applied.status_code == 200 and applied.json()["applied"] == 1
    assert set(applied.json()) == {"applied", "failed", "backup_path", "failed_items"}
    assert _native_tags(boundary.db) == ["HTTP-B"]
    assert client.post("/api/pending/apply", json=current).json()["applied"] == 0
    assert len(boundary.dispatched) == 1
    assert len(list(boundary.db.parent.glob("zotero.sqlite.backup_*"))) == 1


@pytest.mark.parametrize("aba", [False, True])
def test_identical_and_aba_saves_advance_durable_revision(boundary, aba):
    row = _present(boundary)
    original = json.loads(row["payload_json"])
    if aba:
        edit(row, {"add_tags": ["intervening"], "remove_tags": []})
    edit(row, original)
    durable, = repositories.get_pending_changes_by_ids([row["id"]])
    assert json.loads(durable["payload_json"]) == original
    assert pending_actions.edit_revision(durable["audit"]) == (2 if aba else 1)
    with pytest.raises(APIError) as error:
        apply([row])
    assert error.value.status_code == 409
    no_effect(boundary)
    fresh, = presented()
    assert fresh["approval_preview"]["edit_revision"] == (2 if aba else 1)
    assert apply([fresh])["applied"] == 1


def test_edit_and_audit_failure_roll_back_together(boundary, monkeypatch):
    row = _present(boundary)

    def deny(conn, *_args, **_kwargs):
        assert conn.in_transaction
        raise sqlite3.OperationalError("Owned audit refusal")

    with monkeypatch.context() as patch:
        patch.setattr(pending_actions, "record", deny)
        with pytest.raises(sqlite3.OperationalError, match="Owned audit refusal"):
            edit(row, {"add_tags": ["must rollback"]})
    durable, = repositories.get_pending_changes_by_ids([row["id"]])
    assert durable["payload_json"] == row["payload_json"] and durable["audit"] == []
    assert apply([row])["applied"] == 1


def test_queue_lock_and_committed_claim_block_edit_interleavings(boundary, monkeypatch):
    row = _present(boundary)
    writer = zotero.get_zotero_writer_or_raise()
    backup, native = writer.backup_database, writer.apply_changes
    phases = []

    def preparing(**kwargs):
        conn = repositories._get_conn()
        try:
            conn.execute("PRAGMA busy_timeout=0")
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                conn.execute("BEGIN IMMEDIATE")
        finally:
            conn.close()
        phases.append("queue_locked")
        return backup(**kwargs)

    def delivering(*args, **kwargs):
        saved, = repositories.get_pending_changes_by_ids([row["id"]])
        assert saved["status"] == "applying"
        assert not repositories.update_pending_change_payload(row["id"], {"add_tags": ["unseen"]})
        with pytest.raises(APIError) as error:
            edit(row, {"add_tags": ["unseen"]})
        assert error.value.status_code == 404
        phases.append("claim_committed")
        return native(*args, **kwargs)

    monkeypatch.setattr(writer, "backup_database", preparing)
    monkeypatch.setattr(writer, "apply_changes", delivering)
    assert apply([row])["applied"] == 1
    saved, = repositories.get_pending_changes_by_ids([row["id"]])
    assert phases == ["queue_locked", "claim_committed"]
    assert saved["status"] == "applied" and saved["payload_json"] == row["payload_json"]
    assert _native_tags(boundary.db) == ["pending-186-A"]


@pytest.mark.parametrize("fault", ["incomplete", "duplicate", "changed_claim"])
def test_native_delivery_without_matching_final_receipt_stays_uncertain(boundary, monkeypatch, fault):
    row = _present(boundary)
    writer = zotero.get_zotero_writer_or_raise()
    native = writer.apply_changes

    def lose(*args, **kwargs):
        result = native(*args, **kwargs)
        if fault == "changed_claim":
            with closing(repositories._get_conn()) as conn:
                conn.execute("UPDATE pending_changes SET payload_json=? WHERE id=?", ('{"add_tags":["unseen"]}', row["id"]))
                conn.commit()
        else:
            result["applied_ids"] = [] if fault == "incomplete" else [row["id"], row["id"]]
        return result

    monkeypatch.setattr(writer, "apply_changes", lose)
    with pytest.raises(ValueError):
        apply([row])
    assert repositories.get_pending_changes_by_ids([row["id"]])[0]["status"] == "applying"
    for retry in (False, True):
        with pytest.raises(APIError) as error:
            apply([row], retry=retry)
        assert error.value.status_code == 409
    assert _native_tags(boundary.db) == ["pending-186-A"] and len(boundary.dispatched) == 1
    assert len(list(boundary.db.parent.glob(writer._BACKUP_GLOB))) == 1


def test_ordinary_delivery_and_reentrance_need_no_cache_configuration(boundary, monkeypatch):
    row = _present(boundary)
    before = dict(_review_cache._PUBLICATION_LOCKS)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Ordinary approval must not resolve cache/runtime/model settings")

    monkeypatch.setattr(_review_cache, "_cache_path", forbidden)
    monkeypatch.setattr(_review_cache, "settings", forbidden)
    monkeypatch.setattr(_review_cache, "FileLock", forbidden)
    monkeypatch.setattr(runtime, "get_context", forbidden)
    monkeypatch.setattr(_common, "get_context", forbidden)
    monkeypatch.setattr(_common, "settings", forbidden)
    with _review_cache.review_publication_lock(require_publication=False):
        with _review_cache.review_publication_lock(require_publication=False):
            assert _review_cache._PUBLICATION_LOCKS == before
    assert apply([row])["applied"] == 1
    assert _review_cache._PUBLICATION_LOCKS == before
    assert counts(boundary.db) == (1, 0)


def test_malformed_native_target_is_not_masked_as_unavailable(boundary):
    _present(boundary)
    with closing(sqlite3.connect(boundary.db)) as conn:
        conn.execute("UPDATE libraries SET type='user' WHERE libraryID=2")
        conn.commit()
    with pytest.raises(ZoteroWriteError):
        presented()
    no_effect(boundary)


@pytest.mark.parametrize("stale", ["ordinary", "generated"])
@pytest.mark.parametrize("reverse", [False, True])
def test_mixed_stale_selection_refuses_all_families_before_backup(generated_boundary, stale, reverse, monkeypatch):
    b = generated_boundary
    ordinary = queue({"add_tags": ["mixed"], "remove_tags": []}, key=b.key)
    rows = [b.presented, ordinary]
    if reverse:
        rows.reverse()
    if stale == "ordinary":
        edit(ordinary, {"add_tags": ["unseen"], "remove_tags": []})
    else:
        changed = deepcopy(b.entry)
        changed["digest"]["tldr"] += " Changed publication."
        _review_cache._write_one(b.key, changed)
    backup, native = Mock(wraps=b.writer.backup_database), Mock(wraps=b.writer.apply_changes)
    monkeypatch.setattr(b.writer, "backup_database", backup)
    monkeypatch.setattr(b.writer, "apply_changes", native)
    with pytest.raises(APIError) as error:
        apply(rows)
    assert error.value.status_code == 409
    backup.assert_not_called()
    native.assert_not_called()
    assert counts(b.db) == (0, 0)
    assert all(not any(event["event"] == "approved" for event in item["audit"])
               for item in repositories.get_pending_changes(status=None))


def test_mixed_delivery_and_applied_replay_share_one_backup(generated_boundary, monkeypatch, refresh):
    b = generated_boundary
    ordinary = queue({"add_tags": ["mixed"], "remove_tags": []}, key=b.key)
    rows = [b.presented, ordinary]
    backup, native = Mock(wraps=b.writer.backup_database), Mock(wraps=b.writer.apply_changes)
    monkeypatch.setattr(b.writer, "backup_database", backup)
    monkeypatch.setattr(b.writer, "apply_changes", native)
    assert apply(rows)["applied"] == 2
    assert counts(b.db) == (1, 1)
    refresh.assert_awaited_once_with([b.key])
    assert backup.call_count == native.call_count == 1
    assert {row["id"] for row in native.call_args.args[0]} == {row["id"] for row in rows}
    assert apply(rows)["applied"] == 0
    extra = queue({"add_tags": ["second"], "remove_tags": []}, key=b.key)
    assert apply([*rows, extra])["applied"] == 1
    assert counts(b.db) == (2, 1) and backup.call_count == native.call_count == 2


@pytest.mark.parametrize("remove", [False, True])
def test_explicit_queued_collection_membership_is_preserved(boundary, remove):
    with closing(sqlite3.connect(boundary.db)) as conn:
        if remove:
            conn.execute("INSERT INTO collectionItems SELECT itemID,90 FROM items WHERE key=?", (KEY,))
            conn.commit()
    row = queue({"collection_key": "EQIM47Z6"}, "remove_from_collection" if remove else "add_to_collection")
    result = apply([row])
    assert result["applied"] == 1 and result["failed"] == 0
    with closing(sqlite3.connect(boundary.db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM collectionItems").fetchone()[0] == int(not remove)
    with closing(sqlite3.connect(result["backup_path"])) as conn:
        assert conn.execute("SELECT COUNT(*) FROM collectionItems").fetchone()[0] == int(remove)
    assert len(boundary.dispatched) == 1


def assert_native_packets(path, packets):
    tags, notes = [], []
    for packet in packets:
        preview = packet["approval_preview"]
        key, library = preview["item_key"], preview["target"]["library_id"]
        payload = preview["payload"]
        if preview["change_type"] == "tag_changes":
            tags.extend((key, library, tag, int(tag.startswith("/")), int(tag.startswith("/")))
                        for tag in payload["add_tags"])
        else:
            assert preview["change_type"] == "upsert_note"
            fragment = payload["note_html"]
            assert payload["marker"] in fragment
            assert not fragment.startswith('<div class="zotero-note znv')
            notes.append((key, library, library, "note", payload["note_title"],
                          '<div class="zotero-note znv1">' + fragment + '</div>'))
    with closing(sqlite3.connect(path)) as conn:
        actual_tags = conn.execute(
            "SELECT i.key,i.libraryID,t.name,t.type,it.type FROM itemTags it "
            "JOIN items i ON i.itemID=it.itemID JOIN tags t ON t.tagID=it.tagID "
            "ORDER BY i.key,t.name"
        ).fetchall()
        actual_notes = conn.execute(
            "SELECT p.key,p.libraryID,i.libraryID,t.typeName,n.title,n.note FROM itemNotes n "
            "JOIN items p ON p.itemID=n.parentItemID JOIN items i ON i.itemID=n.itemID "
            "JOIN itemTypes t ON t.itemTypeID=i.itemTypeID ORDER BY p.key,n.title,n.note"
        ).fetchall()
        assert actual_tags == sorted(tags)
        assert actual_notes == sorted(notes)
        assert conn.execute("SELECT COUNT(*) FROM deletedItems").fetchone()[0] == 0


class _ObservedLock:
    def __init__(self, actual, role, phase):
        self.actual, self.role, self.phase = actual, role, phase
        self.probe = []

    def __enter__(self):
        if self.role.get() == "B" and not self.probe:
            acquired = self.actual.acquire(blocking=False)
            self.probe.append(acquired)
            self.phase.set()
            if acquired:
                return self
        self.actual.acquire()
        return self

    def __exit__(self, *_args):
        self.actual.release()


@pytest.mark.parametrize("pair", [("ordinary", "ordinary"), ("ordinary", "generated"), ("generated", "ordinary")])
def test_competing_batches_keep_their_prepared_reference_after_claim_commit(generated_boundary, pair, monkeypatch):
    b = generated_boundary
    add_library_item(b.db, item_key=KEY, title="First ordinary target")
    add_library_item(b.db, item_key="ORD191BB", title="Second ordinary target")
    first = queue({"add_tags": ["first"], "remove_tags": []}, key=KEY)
    second = queue({"add_tags": ["second"], "remove_tags": []}, key="ORD191BB")
    rows = {"A": [b.presented if pair[0] == "generated" else first],
            "B": [b.presented if pair[1] == "generated" else second]}
    packets = deepcopy(rows)
    role = ContextVar("pending_test_role", default=None)
    a_phase, b_phase, release = Event(), Event(), Event()
    lock = _ObservedLock(_review_cache._CACHE_LOCK, role, b_phase)
    monkeypatch.setattr(_review_cache, "_CACHE_LOCK", lock)
    native, take = b.writer.apply_changes, b.writer._take_prepared_backup
    calls, taken = {}, {}

    def consume(reference, target):
        assert role.get() not in taken
        taken[role.get()] = (reference, target.model_dump())
        take(reference, target)

    def delivering(changes, *args, **kwargs):
        label = role.get()
        saved, = repositories.get_pending_changes_by_ids([changes[0]["id"]])
        assert saved["status"] == "applying"
        reference = kwargs["precreated_backup"]
        assert saved["backup_path"] == saved["audit"][-1]["backup_path"] == reference
        calls[label] = (reference, kwargs["expected_target"].model_dump())
        if label == "A":
            a_phase.set()
            release.wait()
        return native(changes, *args, **kwargs)

    def run(label):
        role.set(label)
        with repositories.with_db_path(repositories.DB_PATH):
            return apply(rows[label])

    monkeypatch.setattr(b.writer, "apply_changes", delivering)
    monkeypatch.setattr(b.writer, "_take_prepared_backup", consume)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(run, "A")
        a.add_done_callback(lambda _future: a_phase.set())
        try:
            a_phase.wait()
            assert "A" in calls or a.result()["applied"] == 1
            assert "A" in calls
            competing = pool.submit(run, "B")
            competing.add_done_callback(lambda _future: b_phase.set())
            b_phase.wait()
            assert lock.probe == [False]
            assert "B" not in calls
            saved, = repositories.get_pending_changes_by_ids([rows["B"][0]["id"]])
            assert saved["status"] == "pending" and not any(e["event"] == "approved" for e in saved["audit"])
        finally:
            release.set()
        assert a.result()["applied"] == competing.result()["applied"] == 1
    assert calls == taken and calls["A"][0] != calls["B"][0]
    assert len(list(b.db.parent.glob(b.writer._BACKUP_GLOB))) == 2
    assert all(Path(reference).parent == b.db.parent for reference, _target in calls.values())
    assert all(target == b.writer.target_identity().model_dump() for _reference, target in calls.values())
    after_a = (0, 1) if pair[0] == "generated" else (1, 0)
    assert counts(calls["A"][0]) == (0, 0) and counts(calls["B"][0]) == after_a
    assert counts(b.db) == ((2, 0) if pair == ("ordinary", "ordinary") else (1, 1))
    assert_native_packets(calls["A"][0], [])
    assert_native_packets(calls["B"][0], packets["A"])
    assert_native_packets(b.db, [*packets["A"], *packets["B"]])
    assert rows == packets
    saved = repositories.get_pending_changes_by_ids([row["id"] for batch in rows.values() for row in batch])
    assert all(row["status"] == "applied" and row["error_message"] == "" for row in saved)
