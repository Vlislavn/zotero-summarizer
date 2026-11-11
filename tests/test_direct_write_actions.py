"""Prospective direct-editor outcomes through existing HTTP/services and real owned SQLite.

Authored only: no RED/GREEN observed. No proposed production module is imported.
"""
import json
from pathlib import Path
import sqlite3

import pytest

from tests._direct_write_fixtures import (
    KEY, OTHER, assert_receipts, backups, direct_boundary, edit, native, sql_rows, stored,
)
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.library import _review_cache


@pytest.mark.parametrize("kind", ["tags", "priority", "collections"])
def test_http_editor_preserves_fields_and_retains_actual_receipts(direct_boundary, kind):
    b = direct_boundary
    before = native(b.db)
    bodies = {"tags": {"add_tags": ["/owned/new"], "remove_tags": ["topic:old"]},
              "priority": {"priority": "must_read"},
              "collections": {"add": [{"collection_key": "RESEARCH1"}],
                              "remove": [{"collection_key": "EQIM47Z6"}]}}
    actions = {
        "tags": [(KEY, "tag_changes", {"add_tags": ["/owned/new"], "remove_tags": ["topic:old"]})],
        "priority": [(KEY, "tag_changes", {"add_tags": ["label:must_read"], "remove_tags": ["label:could_read"]})],
        "collections": [(KEY, "add_to_collection", {"collection_key": "RESEARCH1"}),
                        (KEY, "remove_from_collection", {"collection_key": "EQIM47Z6"})],
    }

    response = b.client.post(f"/api/zotero/items/{KEY}/{kind}", json=bodies[kind])

    assert response.status_code == 200
    data, after = response.json(), native(b.db)
    expected_tags = list(before["tags"])
    if kind == "tags":
        expected_tags = [row for row in expected_tags if not (row[0] == KEY and row[2].lower() == "topic:old")]
        expected_tags.append((KEY, 1, "/owned/new", 1, 1))
        assert data["add_tags"] == ["/owned/new"] and data["remove_tags"] == ["topic:old"]
    elif kind == "priority":
        expected_tags.remove((KEY, 1, "label:could_read", 0, 0))
        expected_tags.append((KEY, 1, "label:must_read", 0, 0))
        assert data["priority"] == "must_read"
        assert data["add_tags"] == ["label:must_read"] and data["remove_tags"] == ["label:could_read"]
    assert after["tags"] == sorted(expected_tags)
    new_record = {"tags": [("/owned/new", 1)], "priority": [("label:must_read", 0)], "collections": []}
    assert after["tag_records"] == sorted(before["tag_records"] + new_record[kind])
    assert after["deleted"] == before["deleted"]
    assert after["memberships"] == ([(KEY, 1, "RESEARCH1", 1)] if kind == "collections" else before["memberships"])
    assert after["fields"] == before["fields"] and after["notes"] == before["notes"]
    assert [row for row in after["clocks"] if row[0] == OTHER] == [row for row in before["clocks"] if row[0] == OTHER]
    assert data["updated"] == (2 if kind == "collections" else 1)
    assert data["item_key"] == KEY and data["item"]["item_key"] == KEY
    # Baseline can perform these actual effects, but has no durable receipt/locator.
    assert data["delivery_status"] == "applied"
    ids = data["action_ids"]
    rows = assert_receipts(b, ids, f"direct_{kind}", actions[kind], statuses=["applied"] * len(actions[kind]))
    assert len(rows) == data["updated"] and backups(b) == [data["backup_path"]]
    assert native(data["backup_path"]) == before
    assert all(row["backup_path"] == data["backup_path"] and row["applied_at"] for row in rows)
    assert all(row["audit"][0]["requested"] == bodies[kind] for row in rows)
    assert sql_rows(data["backup_path"], "PRAGMA integrity_check") == [("ok",)]


def test_claim_is_durable_before_real_native_apply_and_reference_is_consumed(direct_boundary, monkeypatch):
    b = direct_boundary
    before, actual, observations = native(b.db), b.writer.apply_changes, []

    def observe(changes, *args, **kwargs):
        reference = kwargs["precreated_backup"]
        rows = stored(b)  # independent connection after the claim commit
        assert {row["id"] for row in rows} == {change["id"] for change in changes}
        assert all(row["status"] == "applying" and row["backup_path"] == reference for row in rows)
        assert all(json.loads(row["audit_json"])[0]["event"] == "direct_requested" for row in rows)
        assert b.writer._prepared_backup[0] == reference
        assert kwargs["expected_target"].model_dump() == b.writer.target_identity().model_dump()
        assert native(reference) == before and native(b.db) == before
        result = actual(changes, *args, **kwargs)
        assert b.writer._prepared_backup is None
        observations.append(reference)
        return result

    monkeypatch.setattr(b.writer, "apply_changes", observe)
    result = edit(add_tags=["observed"])

    assert observations == [result["backup_path"]] == backups(b)
    assert_receipts(b, result["action_ids"], "direct_tags", [
        (KEY, "tag_changes", {"add_tags": ["observed"], "remove_tags": []}),
    ], statuses=["applied"])


@pytest.mark.parametrize("partial", [False, True])
def test_collection_failures_report_the_actual_committed_partition(direct_boundary, partial):
    b = direct_boundary
    before = native(b.db)
    additions = [{"collection_key": "FOREIGN", "collection_path": "Inbox"}]
    actions = [(KEY, "add_to_collection", {"collection_key": "FOREIGN", "collection_path": "Inbox"})]
    if partial:
        additions.insert(0, {"collection_key": "RESEARCH1"})
        actions.insert(0, (KEY, "add_to_collection", {"collection_key": "RESEARCH1"}))

    response = b.client.post(f"/api/zotero/items/{KEY}/collections", json={"add": additions})

    assert response.status_code == 500 and response.json()["error"] == "zotero_write_failed"
    details = response.json()["details"]
    assert details["delivery_status"] == ("partially_applied" if partial else "failed")
    ids = details["action_ids"]
    assert details["applied_ids"] == (ids[:1] if partial else [])
    assert details["failed_ids"] == ids[-1:]
    rows = assert_receipts(b, ids, "direct_collections", actions,
                           statuses=["applied", "failed"] if partial else ["failed"],
                           failed_io={1: False} if partial else {0: False})
    assert len(rows) == len(additions) and rows[-1]["error_message"]
    assert rows[-1]["error_message"] == "Collection not found: FOREIGN"
    assert native(details["backup_path"]) == before
    after = native(b.db)
    assert after["tags"] == before["tags"]
    assert after["memberships"] == sorted(before["memberships"] + ([(KEY, 1, "RESEARCH1", 1)] if partial else []))
    if not partial:
        assert after == before


@pytest.mark.parametrize("kind,values,message", [
    ("tags", {"add_tags": ["topic:keep"], "remove_tags": ["topic:keep"]}, "Tag update has no net changes"),
    ("priority", {"priority": "could_read"}, "Label tag already set"),
])
def test_true_tag_noop_retains_existing_shape_without_new_receipt(direct_boundary, kind, values, message):
    b = direct_boundary
    before = native(b.db)

    result = edit(kind, **values)

    expected = {"updated": 0, "item_key": KEY, "message": message, "item": b.reader.get_item_detail(KEY)}
    expected.update({"add_tags": [], "remove_tags": []} if kind == "tags" else {"priority": "could_read"})
    assert result == expected
    assert stored(b) == [] and backups(b) == [] and native(b.db) == before


@pytest.mark.parametrize("kind,values", [
    ("tags", {"add_tags": ["new"]}), ("priority", {"priority": "must_read"}),
    ("collections", {"add": [{"collection_key": "RESEARCH1"}]}),
])
def test_connector_refusal_is_prewrite_and_force_retains_typed_consent(direct_boundary, monkeypatch, kind, values):
    b = direct_boundary
    before = native(b.db)
    monkeypatch.setattr(b.writer, "is_connector_running", lambda: True)

    refusal = edit(kind, **values)

    assert set(refusal) == {"error", "message", "requires_force"}
    assert refusal["requires_force"] is True and refusal["error"] == "zotero_running"
    assert native(b.db) == before and stored(b) == [] and backups(b) == []
    result = edit(kind, force=True, **values)
    actions = {
        "tags": [(KEY, "tag_changes", {"add_tags": ["new"], "remove_tags": []})],
        "priority": [(KEY, "tag_changes", {"add_tags": ["label:must_read"], "remove_tags": ["label:could_read"]})],
        "collections": [(KEY, "add_to_collection", {"collection_key": "RESEARCH1"})],
    }
    rows = assert_receipts(b, result["action_ids"], f"direct_{kind}", actions[kind], statuses=["applied"])
    expected = {"tags": {"add_tags": ["new"], "remove_tags": []},
                "priority": {"priority": "must_read"},
                "collections": {"add": [{"collection_key": "RESEARCH1"}], "remove": []}}
    assert rows[0]["audit"][0]["requested"] == expected[kind]


def test_collection_repeat_is_new_dispatch_not_an_invented_noop_or_exact_replay(direct_boundary):
    b = direct_boundary
    before_version = sql_rows(b.db, "SELECT version FROM items WHERE key=?", (KEY,))[0][0]
    values = {"add": [{"collection_key": "RESEARCH1"}, {"collection_key": "RESEARCH1"}]}

    first, second = edit("collections", **values), edit("collections", **values)

    assert first["updated"] == second["updated"] == 2
    assert set(first["action_ids"]).isdisjoint(second["action_ids"])
    assert len(stored(b)) == 4
    assert sql_rows(b.db, "SELECT version FROM items WHERE key=?", (KEY,)) == [(before_version + 4,)]
    assert native(b.db)["memberships"] == [(KEY, 1, "EQIM47Z6", 1), (KEY, 1, "RESEARCH1", 1)]
    assert native(second["backup_path"])["memberships"] == native(b.db)["memberships"]
    for result in (first, second):
        assert_receipts(b, result["action_ids"], "direct_collections", [
            (KEY, "add_to_collection", {"collection_key": "RESEARCH1"}),
            (KEY, "add_to_collection", {"collection_key": "RESEARCH1"}),
        ], statuses=["applied", "applied"])


def test_predispatch_persistence_refusal_cannot_dispatch(direct_boundary):
    b = direct_boundary
    before = native(b.db)
    with sqlite3.connect(b.queue_db) as conn:
        conn.execute("CREATE TRIGGER owned_deny_claim_insert BEFORE INSERT ON pending_changes "
                     "WHEN NEW.status='applying' BEGIN SELECT RAISE(ABORT,'Owned claim refusal'); END")
        conn.execute("CREATE TRIGGER owned_deny_claim_update BEFORE UPDATE ON pending_changes "
                     "WHEN NEW.status='applying' BEGIN SELECT RAISE(ABORT,'Owned claim refusal'); END")

    with pytest.raises((sqlite3.Error, APIError)):
        edit(add_tags=["must-not-dispatch"])

    assert native(b.db) == before and stored(b) == []
    assert all(native(path) == before for path in backups(b))


def test_nth_terminal_audit_failure_rolls_back_all_receipts_not_native_effects(direct_boundary):
    b = direct_boundary
    before = native(b.db)
    with sqlite3.connect(b.queue_db) as conn:
        conn.execute("CREATE TRIGGER owned_deny_second_terminal BEFORE UPDATE ON pending_changes "
            "WHEN NEW.status='applied' AND EXISTS (SELECT 1 FROM pending_changes WHERE status='applied') "
            "BEGIN SELECT RAISE(ABORT,'Owned second terminal refusal'); END")

    with pytest.raises(APIError) as failure:
        edit("collections", add=[{"collection_key": "RESEARCH1"}], remove=[{"collection_key": "EQIM47Z6"}])

    assert failure.value.error == "zotero_write_uncertain" and failure.value.status_code == 409
    details = failure.value.details
    assert details["delivery_status"] == "uncertain"
    rows = assert_receipts(b, details["action_ids"], "direct_collections", [
        (KEY, "add_to_collection", {"collection_key": "RESEARCH1"}),
        (KEY, "remove_from_collection", {"collection_key": "EQIM47Z6"}),
    ], statuses=["applying", "applying"])
    assert all([event["event"] for event in row["audit"]] == ["direct_requested"] for row in rows)
    assert native(b.db)["memberships"] == [(KEY, 1, "RESEARCH1", 1)]
    assert native(details["backup_path"]) == before
    saved = stored(b)
    with pytest.raises(APIError) as blocked:
        edit(add_tags=["different-type"])
    assert blocked.value.details["blocking_action_ids"] == details["action_ids"]
    assert stored(b) == saved and backups(b) == [details["backup_path"]]


@pytest.mark.parametrize("fault", ["lost", "incomplete", "duplicate", "backup_mismatch"])
def test_real_native_commit_with_lost_or_inconsistent_outcome_stays_uncertain(direct_boundary, monkeypatch, fault):
    b = direct_boundary
    before, actual, completed = native(b.db), b.writer.apply_changes, []

    def lose(changes, *args, **kwargs):
        result = actual(changes, *args, **kwargs)
        completed.extend(result["applied_ids"])
        if fault == "lost":
            raise RuntimeError("Owned reply lost after actual native commit")
        if fault == "incomplete":
            result["applied_ids"] = []
        elif fault == "duplicate":
            result["applied_ids"] *= 2
        else:
            result["backup_path"] = str(b.db.parent / "not-the-issued-reference")
        return result

    monkeypatch.setattr(b.writer, "apply_changes", lose)
    with pytest.raises(APIError) as failure:
        edit(add_tags=["delivered-once"])

    assert failure.value.error == "zotero_write_uncertain" and failure.value.status_code == 409
    details = failure.value.details
    assert details["delivery_status"] == "uncertain" and completed == details["action_ids"]
    assert_receipts(b, completed, "direct_tags", [
        (KEY, "tag_changes", {"add_tags": ["delivered-once"], "remove_tags": []}),
    ], statuses=["applying"])
    assert native(b.db)["tags"] == sorted(before["tags"] + [(KEY, 1, "delivered-once", 0, 0)])
    assert native(details["backup_path"]) == before
    with pytest.raises(APIError):
        edit(add_tags=["delivered-once"])
    assert completed == details["action_ids"] and backups(b) == [details["backup_path"]]


@pytest.mark.parametrize("fault", ["corpus", "detail_error", "detail_missing"])
def test_followup_failure_retains_durable_native_success(direct_boundary, monkeypatch, fault):
    b = direct_boundary
    if fault == "corpus":
        b.refresh.side_effect = RuntimeError("Owned corpus follow-up failure")
    else:
        actual = b.reader.get_item_detail

        def read(key):
            if (KEY, 1, "committed", 0, 0) in native(b.db)["tags"]:
                if fault == "detail_missing":
                    return None
                raise RuntimeError("Owned detail follow-up failure")
            return actual(key)

        monkeypatch.setattr(b.reader, "get_item_detail", read)

    response = b.client.post(f"/api/zotero/items/{KEY}/tags", json={"add_tags": ["committed"]})

    assert response.status_code == 500 and response.json()["error"] == "zotero_refresh_failed"
    details = response.json()["details"]
    assert details["delivery_status"] == "applied" and details["failed_ids"] == []
    assert details["applied_ids"] == details["action_ids"]
    assert_receipts(b, details["action_ids"], "direct_tags", [
        (KEY, "tag_changes", {"add_tags": ["committed"], "remove_tags": []}),
    ], statuses=["applied"])
    assert (KEY, 1, "committed", 0, 0) in native(b.db)["tags"]
    assert sql_rows(b.db, "SELECT version FROM items WHERE key=?", (KEY,)) == [(1,)]
    assert backups(b) == [details["backup_path"]]


def test_direct_owner_needs_no_review_cache_or_publication_configuration(direct_boundary, monkeypatch):
    def forbidden():
        raise AssertionError("Direct edit must not resolve review-cache configuration")

    monkeypatch.setattr(_review_cache, "_cache_path", forbidden)
    monkeypatch.setattr(_review_cache, "settings", forbidden)
    result = edit(add_tags=["cache-independent"])

    assert_receipts(direct_boundary, result["action_ids"], "direct_tags", [
        (KEY, "tag_changes", {"add_tags": ["cache-independent"], "remove_tags": []}),
    ], statuses=["applied"])
    assert Path(result["backup_path"]).is_file()
