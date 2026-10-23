"""Prospective direct-history isolation at actual queue/producer/editor interfaces.

Seeded direct-origin events specify the future contract; they are not current receipts.
"""
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json
import sqlite3
from threading import Barrier, Event
from types import SimpleNamespace

import pytest

from tests._direct_write_fixtures import (
    KEY, OTHER, WEEKLY, ObservedLock, apply_presented, backups, direct_boundary,
    edit, native, presented, queue, seed_direct, stored,
)
from tests._zotero_fixtures import add_library_item, build_zotero_db
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.models.zotero_actions import (
    DigestNotePayload, GeneratedNoteProposal, PendingApprovalPreview,
)
from zotero_summarizer.services.library import _review_cache
from zotero_summarizer.services.research_feed import runner
from zotero_summarizer.services.zotero import pending_confirmation, zotero
from zotero_summarizer.storage import pending_actions, repositories


@pytest.mark.parametrize("status", ["pending", "failed", "applying", "applied", "rejected"])
@pytest.mark.parametrize("availability", ["configured", "missing", "replaced"])
@pytest.mark.parametrize("source", ["direct_tags", "direct_priority", "direct_collections"])
def test_direct_history_never_gets_pending_authority(direct_boundary, monkeypatch, status, availability, source):
    b = direct_boundary
    row = seed_direct(b, status, source=source)
    saved, before = stored(b), native(b.db)
    if availability == "missing":
        def unavailable():
            raise APIError("zotero_unavailable", "Owned absent writer", 503)
        monkeypatch.setattr(zotero, "get_zotero_writer_or_raise", unavailable)
    elif availability == "replaced":
        other_db = build_zotero_db(b.db.parent.parent / "other-owned-target")
        add_library_item(other_db, item_key=KEY, title="Same key, different native target")
        replacement = ZoteroWriter(other_db.parent)
        monkeypatch.setattr(zotero, "get_zotero_writer_or_raise", lambda: replacement)

    dto = presented(row)

    assert dto["approval_preview"] is None
    assert dto["audit"] == row["audit"] and dto["backup_path"] == row["backup_path"]
    assert stored(b) == saved and native(b.db) == before and backups(b) == [row["backup_path"]]


def test_guessed_exact_hash_direct_failed_retry_refuses_the_entire_selection(direct_boundary):
    b = direct_boundary
    direct = seed_direct(b)
    ordinary = queue({"add_tags": ["must-not-deliver"], "remove_tags": []})
    repositories.set_pending_changes_status([ordinary["id"]], "failed")
    ordinary = presented(ordinary)
    # Adversarial hash construction only: never used as ordinary caller authority.
    guessed = PendingApprovalPreview(change_id=direct["id"], item_key=KEY, change_type="tag_changes",
        payload=json.loads(direct["payload_json"]), edit_revision=0, target=b.writer.target_identity()).preview_sha256
    before, saved, paths = native(b.db), stored(b), backups(b)

    response = b.client.post("/api/pending/apply", json={
        "change_ids": [ordinary["id"], direct["id"]], "retry": True, "force": True,
        "previews": {str(ordinary["id"]): ordinary["approval_preview"]["preview_sha256"],
                     str(direct["id"]): guessed},
    })

    assert response.status_code == 409 and response.json()["error"] == "pending_write_conflict"
    assert native(b.db) == before and stored(b) == saved and backups(b) == paths


def test_direct_edit_reject_and_storage_reset_cannot_reclassify_history(direct_boundary):
    b = direct_boundary
    direct = seed_direct(b, "pending")  # Deliberate corrupted-state fixture, not a production transition.
    saved, before, paths = stored(b), native(b.db), backups(b)

    response = b.client.put(f"/api/pending/change/{direct['id']}", json={"payload": {"add_tags": ["replacement"]}})

    assert response.status_code in {404, 409}
    rejected = b.client.post("/api/pending/reject", json={"change_ids": [direct["id"]]})
    assert rejected.status_code == 409 or (rejected.status_code == 200 and rejected.json()["updated"] == 0)
    operations = [lambda: repositories.update_pending_change_payload(direct["id"], {"add_tags": ["replacement"]}),
                  lambda: repositories.set_pending_changes_status([direct["id"]], "pending")]
    for operation in operations:
        try:
            assert not operation()
        except ValueError:
            assert stored(b) == saved
        assert stored(b) == saved
    assert native(b.db) == before and backups(b) == paths


def test_mixed_rejection_must_not_commit_generated_sibling_first(direct_boundary):
    b = direct_boundary
    direct = seed_direct(b)
    body = "<p>Owned generated fixture</p><p>zs:note_type=digest;version=1</p>"
    generated_id = pending_actions.stage("Owned generated fixture", GeneratedNoteProposal(
        item_key=OTHER, target=b.writer.target_identity(),
        review_revision="00000000-0000-0000-0000-000000000001",
        source_sha256=hashlib.sha256(b"owned-fixture-source").hexdigest(),
        body_sha256=hashlib.sha256(body.encode()).hexdigest(),
        payload=DigestNotePayload(marker="zs:note_type=digest", note_html=body),
    ))
    saved, before = stored(b), native(b.db)

    response = b.client.post("/api/pending/reject", json={"change_ids": [generated_id, direct["id"]]})

    assert response.status_code == 409
    assert stored(b) == saved and native(b.db) == before


def weekly(b, *, key=KEY, worth="yes"):
    return runner._queue_tags(b.settings, [{
        "candidate": {"source_id": "owned-feed", "title": "Owned weekly paper"},
        "card": {"worth_reading": worth, "topic_tags": []},
        "triage": {"matched_projects": []},
    }], {"owned-feed": {"materialized_zotero_key": key}})


@pytest.mark.parametrize("status", ["failed", "applying", "applied"])
def test_actual_weekly_producer_ignores_direct_history_but_dedups_ordinary(direct_boundary, status):
    b = direct_boundary
    direct = seed_direct(b, status, payload=WEEKLY)
    saved_direct, before, paths = stored(b)[0], native(b.db), backups(b)
    assert repositories.pending_change_exists(KEY, "tag_changes", direct["payload_json"])
    assert repositories.pending_change_exists(KEY, "tag_changes", WEEKLY)
    assert repositories.get_pending_changes(status=None, item_key=KEY) == [direct]
    assert not repositories.pending_change_exists(KEY, "tag_changes", WEEKLY, status="pending")
    assert not repositories.pending_change_exists(KEY, "tag_changes", json.dumps(WEEKLY, separators=(",", ":")))

    first = weekly(b)

    assert first == {"attempted": 1, "succeeded": 1, "failed": 0, "skipped": 0}
    rows = repositories.get_pending_changes(status=None, item_key=KEY)
    ordinary, = [row for row in rows if row["id"] != direct["id"]]
    assert ordinary["status"] == "pending" and ordinary["audit"] == []
    assert json.loads(ordinary["payload_json"]) == WEEKLY
    assert stored(b)[0] == saved_direct
    assert weekly(b) == {"attempted": 0, "succeeded": 0, "failed": 0, "skipped": 1}
    assert len(stored(b)) == 2 and native(b.db) == before and backups(b) == paths
    if status == "applying":
        with pytest.raises(APIError) as blocked:
            apply_presented([presented(ordinary)])
        assert blocked.value.error == "zotero_write_uncertain" and blocked.value.status_code == 409
        assert blocked.value.details["blocking_action_ids"] == [direct["id"]]
        assert native(b.db) == before and backups(b) == paths
        assert repositories.get_pending_changes_by_ids([ordinary["id"]])[0]["audit"] == []


@pytest.mark.parametrize("status", ["pending", "failed", "applied", "rejected", "applying"])
def test_ordinary_history_dedup_stays_all_status_and_uncapped(direct_boundary, status):
    b = direct_boundary
    direct = seed_direct(b, payload=WEEKLY)
    assert repositories.pending_change_exists(KEY, "tag_changes", direct["payload_json"])
    assert repositories.get_pending_changes(status=None, item_key=KEY) == [direct]
    ordinary = queue(WEEKLY)
    repositories.set_pending_changes_status([ordinary["id"]], status)
    with sqlite3.connect(b.queue_db) as conn:
        conn.execute("UPDATE pending_changes SET created_at='2000-01-01' WHERE id=?", (ordinary["id"],))
    repositories.insert_pending_changes(KEY, "Owned newer proposals", [{
        "change_type": "tag_changes", "payload": {"add_tags": [f"unrelated-{index}"], "remove_tags": []},
    } for index in range(5000)])
    assert ordinary["id"] not in {row["id"] for row in repositories.get_pending_changes(None, 5000, item_key=KEY)}

    assert weekly(b) == {"attempted": 0, "succeeded": 0, "failed": 0, "skipped": 1}

    assert repositories.pending_change_exists(KEY, "tag_changes", WEEKLY)
    assert repositories.get_pending_changes_by_ids([ordinary["id"]])[0]["status"] == status
    assert len(stored(b)) == 5002


@pytest.mark.parametrize("key,worth,expected,nonmatching", [
    (KEY, "yes", WEEKLY, {"add_tags": ["ri:weekly", "ri:no"], "remove_tags": []}),
    (KEY, "yes", WEEKLY, {"remove_tags": [], "add_tags": ["ri:weekly", "ri:yes"]}),
    (KEY, "yes", WEEKLY, {"add_tags": ["ri:yes", "ri:weekly"], "remove_tags": []}),
    (KEY, "yes", WEEKLY, {"add_tags": ["ri:Weekly", "ri:yes"], "remove_tags": []}),
    (OTHER, "no", {"add_tags": ["ri:no", "ri:weekly"], "remove_tags": []}, WEEKLY),
])
def test_weekly_signature_does_not_collapse_nonmatching_ordinary(direct_boundary, key, worth, expected, nonmatching):
    b = direct_boundary
    direct = seed_direct(b, payload=expected, key=key)
    assert repositories.pending_change_exists(key, "tag_changes", direct["payload_json"])
    assert repositories.get_pending_changes(status=None, item_key=key) == [direct]
    different = queue(nonmatching, key=key)
    serialized = json.dumps(expected, ensure_ascii=False)
    assert different["payload_json"] != serialized
    saved, before, paths = stored(b), native(b.db), backups(b)

    assert weekly(b, key=key, worth=worth) == {"attempted": 1, "succeeded": 1, "failed": 0, "skipped": 0}

    rows = repositories.get_pending_changes(status=None, item_key=key)
    ordinary, = [row for row in rows if row["id"] not in {direct["id"], different["id"]}]
    assert (ordinary["item_key"], ordinary["change_type"], ordinary["payload_json"]) == (key, "tag_changes", serialized)
    assert ordinary["status"] == "pending" and ordinary["audit"] == []
    assert stored(b)[:2] == saved
    assert weekly(b, key=key, worth=worth) == {"attempted": 0, "succeeded": 0, "failed": 0, "skipped": 1}
    assert len(stored(b)) == 3 and native(b.db) == before and backups(b) == paths


@pytest.mark.parametrize("other_key,other_type", [(OTHER, "add_to_collection"), (KEY, "remove_from_collection")])
def test_ordinary_signature_selects_both_item_and_change_type(direct_boundary, other_key, other_type):
    b = direct_boundary
    direct = seed_direct(b)
    assert repositories.pending_change_exists(KEY, "tag_changes", direct["payload_json"])
    payload = {"collection_key": "RESEARCH1"}
    different = queue(payload, kind=other_type, key=other_key)
    saved, before, paths = stored(b), native(b.db), backups(b)

    assert repositories.insert_pending_change_if_absent(KEY, "Owned membership", "add_to_collection", payload)

    rows = repositories.get_pending_changes(status=None, item_key=KEY)
    ordinary, = [row for row in rows if row["id"] not in {direct["id"], different["id"]}]
    assert (ordinary["item_key"], ordinary["change_type"], ordinary["payload_json"]) == (
        KEY, "add_to_collection", '{"collection_key": "RESEARCH1"}')
    assert not repositories.insert_pending_change_if_absent(KEY, "Owned membership", "add_to_collection", payload)
    assert repositories.pending_change_exists(other_key, other_type, payload)
    assert stored(b)[:2] == saved and len(stored(b)) == 3
    assert native(b.db) == before and backups(b) == paths


def test_origin_aware_proposal_insert_remains_atomic_with_context_overrides(direct_boundary):
    b = direct_boundary
    direct = seed_direct(b)
    assert repositories.pending_change_exists(KEY, "tag_changes", direct["payload_json"])
    ready = Barrier(2)
    def insert():
        with repositories.with_db_path(b.queue_db):
            ready.wait(timeout=5)
            return repositories.insert_pending_change_if_absent(KEY, "Owned weekly", "tag_changes", WEEKLY)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(insert), pool.submit(insert)
        results = [first.result(timeout=10), second.result(timeout=10)]

    assert sorted(results) == [False, True]
    rows = repositories.get_pending_changes(status=None, item_key=KEY)
    assert len(rows) == 2 and sum(row["audit"] == [] for row in rows) == 1


@pytest.mark.parametrize("fault", ["target", "source", "late_declaration"])
def test_declared_direct_corruption_fails_loudly_not_as_ordinary_authority(direct_boundary, fault):
    b = direct_boundary
    row = seed_direct(b)
    audit = deepcopy(row["audit"])
    if fault == "target":
        audit[0]["target"] = None
    elif fault == "source":
        audit[0]["source"] = "direct_unknown"
    else:
        audit.insert(0, {"event": "payload_edited", "actor": "current_user", "source": "pending_edit"})
    with sqlite3.connect(b.queue_db) as conn:
        conn.execute("UPDATE pending_changes SET audit_json=? WHERE id=?", (json.dumps(audit), row["id"]))
    saved = stored(b)

    with pytest.raises((ValueError, APIError)):
        pending_confirmation.present_changes(repositories.get_pending_changes(status=None), b.writer)
    with pytest.raises((ValueError, APIError)):
        repositories.insert_pending_change_if_absent(KEY, "Owned weekly", "tag_changes", WEEKLY)

    assert stored(b) == saved
    assert weekly(b) == {"attempted": 1, "succeeded": 0, "failed": 1, "skipped": 0}


@pytest.mark.parametrize("kind,values", [
    ("tags", {"add_tags": ["topic:keep"]}),
    ("priority", {"priority": "could_read"}),
    ("collections", {"remove": [{"collection_key": "RESEARCH1"}]}),
])
def test_uncertain_direct_intent_blocks_other_types_and_matching_state_noops(direct_boundary, kind, values):
    b = direct_boundary
    row = seed_direct(b, "applying")
    saved, before, paths = stored(b), native(b.db), backups(b)

    with pytest.raises(APIError) as blocked:
        edit(kind, **values)

    assert blocked.value.error == "zotero_write_uncertain" and blocked.value.status_code == 409
    assert blocked.value.details["delivery_status"] == "uncertain"
    assert blocked.value.details["blocking_action_ids"] == [row["id"]]
    assert stored(b) == saved and native(b.db) == before and backups(b) == paths


@pytest.mark.parametrize("kind,payload", [
    ("tag_changes", {"add_tags": ["new-proposal"], "remove_tags": []}),
    ("add_note", {"note_html": "<p>Owned proposed note</p>"}),
    ("remove_from_collection", {"collection_key": "EQIM47Z6"}),
])
def test_direct_uncertainty_blocks_other_pending_action_types(direct_boundary, kind, payload):
    b = direct_boundary
    direct = seed_direct(b, "applying")
    proposal = presented(queue(payload, kind=kind))
    saved, before, paths = stored(b), native(b.db), backups(b)

    with pytest.raises(APIError) as blocked:
        apply_presented([proposal])

    assert blocked.value.error == "zotero_write_uncertain" and blocked.value.status_code == 409
    assert blocked.value.details["blocking_action_ids"] == [direct["id"]]
    assert stored(b) == saved and native(b.db) == before and backups(b) == paths


def test_multiple_uncertain_receipts_do_not_manufacture_a_common_backup(direct_boundary):
    b = direct_boundary
    first, second = seed_direct(b, "applying"), seed_direct(b, "applying", source="direct_collections")
    saved, before, paths = stored(b), native(b.db), backups(b)
    assert first["backup_path"] != second["backup_path"]

    with pytest.raises(APIError) as blocked:
        edit("priority", priority="could_read")

    assert blocked.value.error == "zotero_write_uncertain" and blocked.value.status_code == 409
    assert sorted(blocked.value.details["blocking_action_ids"]) == sorted([first["id"], second["id"]])
    assert blocked.value.details.get("backup_path") is None
    assert stored(b) == saved and native(b.db) == before and backups(b) == paths


def test_unknown_applying_target_cannot_be_rebound_or_acknowledged(direct_boundary):
    b = direct_boundary
    row = seed_direct(b, "applying")
    audit = deepcopy(row["audit"])
    audit[0]["target"] = None
    with sqlite3.connect(b.queue_db) as conn:
        conn.execute("UPDATE pending_changes SET audit_json=? WHERE id=?", (json.dumps(audit), row["id"]))
    saved, before, paths = stored(b), native(b.db), backups(b)

    with pytest.raises(APIError) as blocked:
        edit(add_tags=["topic:keep"])

    assert blocked.value.error == "zotero_write_uncertain" and blocked.value.status_code == 409
    assert blocked.value.details["blocking_action_ids"] == [row["id"]]
    assert stored(b) == saved and native(b.db) == before and backups(b) == paths


def test_uncertainty_is_item_scoped_not_a_global_native_write_freeze(direct_boundary):
    b = direct_boundary
    row = seed_direct(b, "applying")
    saved = stored(b)[0]

    result = edit(key=OTHER, add_tags=["unrelated-human-edit"])

    assert result["delivery_status"] == "applied"
    assert row["id"] not in result["action_ids"] and stored(b)[0] == saved
    assert (OTHER, 1, "unrelated-human-edit", 0, 0) in native(b.db)["tags"]


def test_same_key_on_different_actual_native_target_does_not_inherit_uncertainty(direct_boundary):
    b = direct_boundary
    other_db = build_zotero_db(b.db.parent.parent / "independent-owned-target")
    add_library_item(other_db, item_key=KEY, title="Other actual native target")
    peer = SimpleNamespace(writer=ZoteroWriter(other_db.parent))
    row = seed_direct(peer, "applying")
    saved, other_before = stored(b)[0], native(other_db)

    result = edit(add_tags=["selected-target-only"])

    assert result["delivery_status"] == "applied" and row["id"] not in result["action_ids"]
    assert stored(b)[0] == saved and native(other_db) == other_before
    assert (KEY, 1, "selected-target-only", 0, 0) in native(b.db)["tags"]


def test_actual_uncertain_pending_delivery_also_blocks_direct_editor(direct_boundary, monkeypatch):
    b = direct_boundary
    proposal = presented(queue({"add_tags": ["pending-delivered"], "remove_tags": []}))
    actual = b.writer.apply_changes
    def lose(changes, *args, **kwargs):
        actual(changes, *args, **kwargs)
        raise RuntimeError("Owned lost Pending result after native commit")

    with monkeypatch.context() as patch:
        patch.setattr(b.writer, "apply_changes", lose)
        with pytest.raises((RuntimeError, APIError)):
            apply_presented([proposal])
    assert repositories.get_pending_changes_by_ids([proposal["id"]])[0]["status"] == "applying"
    before, paths = native(b.db), backups(b)
    with pytest.raises(APIError) as blocked:
        edit("priority", priority="must_read")

    assert blocked.value.error == "zotero_write_uncertain" and blocked.value.status_code == 409
    assert blocked.value.details["blocking_action_ids"] == [proposal["id"]]
    assert native(b.db) == before and backups(b) == paths


@pytest.mark.parametrize("second_kind", ["direct", "pending"])
def test_competing_direct_and_pending_work_keep_actual_prepared_reference(direct_boundary, monkeypatch, second_kind):
    b = direct_boundary
    proposal = presented(queue({"add_tags": ["second"], "remove_tags": []}, key=OTHER))
    role = ContextVar("direct197_role", default=None)
    a_phase, b_phase, release = Event(), Event(), Event()
    lock = ObservedLock(_review_cache._CACHE_LOCK, role, b_phase)
    monkeypatch.setattr(_review_cache, "_CACHE_LOCK", lock)
    actual, calls, initial = b.writer.apply_changes, {}, native(b.db)

    def observe(changes, *args, **kwargs):
        label = role.get()
        reference = kwargs["precreated_backup"]
        rows = repositories.get_pending_changes_by_ids([change["id"] for change in changes])
        assert all(row["status"] == "applying" and row["backup_path"] == reference for row in rows)
        assert b.writer._prepared_backup[0] == reference
        assert b.writer._prepared_backup[2] == kwargs["expected_target"]
        calls[label] = reference
        if label == "A":
            a_phase.set()
            assert release.wait(timeout=5)
        result = actual(changes, *args, **kwargs)
        assert b.writer._prepared_backup is None and result["backup_path"] == reference
        return result

    def run(label):
        role.set(label)
        with repositories.with_db_path(b.queue_db):
            if label == "A":
                return edit(add_tags=["first"])
            if second_kind == "pending":
                return apply_presented([proposal])
            return edit(key=OTHER, add_tags=["second"])

    monkeypatch.setattr(b.writer, "apply_changes", observe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run, "A")
        first.add_done_callback(lambda _future: a_phase.set())
        try:
            assert a_phase.wait(timeout=5)
            assert "A" in calls, first.result(timeout=5)
            second = pool.submit(run, "B")
            second.add_done_callback(lambda _future: b_phase.set())
            assert b_phase.wait(timeout=5)
            assert lock.probe == [False] and "B" not in calls
            assert native(b.db) == initial
        finally:
            release.set()
        first.result(timeout=10)
        second.result(timeout=10)
    assert calls["A"] != calls["B"] and sorted(calls.values()) == backups(b)
    assert native(calls["A"]) == initial
    assert native(calls["B"])["tags"] == sorted(initial["tags"] + [(KEY, 1, "first", 0, 0)])
    assert native(b.db)["tags"] == sorted(initial["tags"] + [(KEY, 1, "first", 0, 0), (OTHER, 1, "second", 0, 0)])
    assert native(b.db)["memberships"] == initial["memberships"]


def test_ordinary_presented_history_replay_and_missing_writer_preserve_old_contract(direct_boundary, monkeypatch):
    b = direct_boundary
    row = presented(queue({"add_tags": ["ordinary"], "remove_tags": []}))
    original = deepcopy(row)

    result = apply_presented([row])

    assert result["applied"] == 1 and result["failed"] == 0
    saved, paths, before = stored(b), backups(b), native(b.db)
    assert apply_presented([row])["applied"] == 0
    def unavailable():
        raise APIError("zotero_unavailable", "Owned absent writer", 503)
    monkeypatch.setattr(zotero, "get_zotero_writer_or_raise", unavailable)
    history = presented(row)
    assert history["approval_preview"] is None
    assert history["audit"][-1]["event"] == "applied"
    assert history["backup_path"] == result["backup_path"]
    assert row == original and stored(b) == saved and backups(b) == paths and native(b.db) == before


@pytest.mark.parametrize("source", ["direct_tags", "direct_priority", "direct_collections"])
def test_uncertain_direct_receipt_before_history_limit_still_blocks(direct_boundary, source):
    b = direct_boundary
    other_db = build_zotero_db(b.db.parent.parent / "uncapped-owned-target")
    add_library_item(other_db, item_key=KEY, title="Other actual native target")
    peer = SimpleNamespace(writer=ZoteroWriter(other_db.parent), db=other_db)
    first = seed_direct(b, "applying", source=source)
    newer = seed_direct(peer, "applying", source=source)
    assert first["item_key"] == newer["item_key"] == KEY
    assert b.writer.target_identity() != peer.writer.target_identity()
    assert first["audit"][0]["target"] != newer["audit"][0]["target"]
    columns = ", ".join(column for column in stored(b)[-1] if column != "id")
    with sqlite3.connect(b.queue_db) as conn:
        conn.execute("UPDATE pending_changes SET created_at='2000-01-01' WHERE id=?", (first["id"],))
        conn.execute("UPDATE pending_changes SET created_at='2001-01-01' WHERE id=?", (newer["id"],))
        # Synthetic read-path stress: B's seed plus 4,999 schema-valid claim copies.
        conn.executemany(f"INSERT INTO pending_changes ({columns}) SELECT {columns} "
                         "FROM pending_changes WHERE id=?", [(newer["id"],)] * 4999)
    view = repositories.get_pending_changes("applying", 5000, item_key=KEY)
    assert len(view) == 5000 and first["id"] not in {row["id"] for row in view}
    saved, before, other_before = stored(b), native(b.db), native(other_db)
    assert len(saved) == 5001
    paths, other_paths = backups(b), backups(peer)
    backup_states = [native(path) for path in paths + other_paths]

    with pytest.raises(APIError) as blocked:
        edit(add_tags=["uncapped-human-edit"])

    assert blocked.value.error == "zotero_write_uncertain" and blocked.value.status_code == 409
    assert blocked.value.details["delivery_status"] == "uncertain"
    assert blocked.value.details["blocking_action_ids"] == [first["id"]]
    assert stored(b) == saved
    assert native(b.db) == before and native(other_db) == other_before
    assert backups(b) == paths and backups(peer) == other_paths
    assert [native(path) for path in paths + other_paths] == backup_states
    b.refresh.assert_not_awaited()
