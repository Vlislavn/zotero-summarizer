"""A060: online/offline retraction must survive the Zotero reverse bridge."""

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from zotero_summarizer.api.errors import APIError
from tests._zotero_fixtures import add_library_item, add_tag_to_item, build_zotero_db
from tests.test_offline_sync import _mutation
from zotero_summarizer.api.routes import golden
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter, ZoteroWriteError
from zotero_summarizer.services.golden import label_verdicts, user_labels, verdict_effects
from zotero_summarizer.services.sync import service
from zotero_summarizer.services.zotero import zotero
from zotero_summarizer.storage import repositories as db


def _setup(tmp_path, monkeypatch, lane):
    path = tmp_path / "triage.db"
    with db.with_db_path(path):
        db.init_db()
    zdb = build_zotero_db(tmp_path / "zotero")
    item = add_library_item(zdb, item_key="PARENT", title="Parent")
    add_tag_to_item(zdb, item_id=item, tag_name="label:must_read")
    add_tag_to_item(zdb, item_id=item, tag_name="topic:x")
    reader, writer = ZoteroReader(zdb.parent), ZoteroWriter(zdb.parent)
    monkeypatch.setattr(zotero, "get_zotero_reader_or_raise", lambda: reader)
    monkeypatch.setattr(zotero, "get_zotero_writer_or_raise", lambda: writer)
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)
    monkeypatch.setattr(golden, "_db_path", lambda: path)
    monkeypatch.setattr(golden, "log_retract_event", lambda *args: None)
    monkeypatch.setattr(label_verdicts, "log_committed_transition", lambda **kw: None)
    db.insert_or_update_label_verdict(path, item_key="PARENT", original_derived_priority="unknown",
                                     user_priority="must_read", comment="")
    revision = db.sync_current_fields(path)[("PARENT", "verdict")]["revision"]
    mutation = _mutation("PARENT", "verdict", None, revision, operation="delete")

    def retract():
        return (asyncio.run(golden.remove_verdict("PARENT")) if lane == "online"
                else service.push(path, [mutation]))

    return path, zdb, item, reader, writer, retract


def _sample():
    return SimpleNamespace(item_key="PARENT", gold_signal_tier="user_label",
                           gold_priority_inferred="must_read")


@pytest.mark.parametrize("lane", ["online", "offline"])
def test_delete_removes_mirror_and_stale_export_cannot_restore_it(tmp_path, monkeypatch, lane):
    path, zdb, item, reader, writer, retract = _setup(tmp_path, monkeypatch, lane)

    retract()

    assert reader.get_item_detail("PARENT")["tags"] == ["topic:x"]
    assert db.get_label_verdict(path, "PARENT") is None
    assert user_labels.reconcile_label_verdicts([_sample()], zdb, path).synced == 0
    assert db.get_label_verdict(path, "PARENT") is None
    # A later deliberate Zotero edit must still work, even to the same priority.
    add_tag_to_item(zdb, item_id=item, tag_name="label:must_read")
    assert user_labels.reconcile_label_verdicts([_sample()], zdb, path).synced == 1
    assert db.get_label_verdict(path, "PARENT")["user_priority"] == "must_read"


@pytest.mark.parametrize("lane", ["online", "offline"])
def test_failed_mirror_is_not_acknowledged_or_resurrected_and_retry_clears_it(tmp_path, monkeypatch, lane):
    path, zdb, item, reader, writer, retract = _setup(tmp_path, monkeypatch, lane)
    monkeypatch.setattr(writer, "is_connector_running", lambda: True)

    with pytest.raises(ZoteroWriteError, match="Zotero is open"):
        retract()

    assert db.get_label_verdict(path, "PARENT") is None
    assert "label:must_read" in reader.get_item_detail("PARENT")["tags"]
    assert user_labels.reconcile_label_verdicts([_sample()], zdb, path).synced == 0
    assert db.get_label_verdict(path, "PARENT") is None
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)
    retract()
    assert reader.get_item_detail("PARENT")["tags"] == ["topic:x"]


def test_replayed_offline_delete_does_not_clear_a_newer_verdict(tmp_path, monkeypatch):
    path, zdb, item, reader, writer, retract = _setup(tmp_path, monkeypatch, "offline")
    retract()
    db.insert_or_update_label_verdict(path, item_key="PARENT", original_derived_priority="unknown",
                                     user_priority="could_read", comment="")
    zotero.zotero_set_label_tag("PARENT", "could_read")

    assert retract()["results"][0]["status"] == "already_applied"

    assert set(reader.get_item_detail("PARENT")["tags"]) == {"topic:x", "label:could_read"}
    assert db.get_label_verdict(path, "PARENT")["user_priority"] == "could_read"


def test_retraction_during_reconciliation_cannot_be_overwritten(tmp_path, monkeypatch):
    path, zdb, item, reader, writer, retract = _setup(tmp_path, monkeypatch, "online")
    db.delete_label_verdict(path, "PARENT")
    # Start with no local history so reconciliation will attempt a fresh import.
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM sync_changes")
    write = db.insert_or_update_label_verdict

    def retract_before_write(*args, **kwargs):
        write(path, item_key="PARENT", original_derived_priority="unknown",
              user_priority="must_read", comment="")
        retract()
        return write(*args, **kwargs)

    monkeypatch.setattr(db, "insert_or_update_label_verdict", retract_before_write)

    assert user_labels.reconcile_label_verdicts([_sample()], zdb, path).synced == 0
    assert db.get_label_verdict(path, "PARENT") is None


@pytest.mark.parametrize("lane", ["online", "offline"])
@pytest.mark.parametrize("key", ["feed:42", "feed:guid:stable"])
def test_materialized_feed_retraction_and_newer_library_label(tmp_path, monkeypatch, lane, key):
    path, zdb, item, reader, writer, _ = _setup(tmp_path, monkeypatch, lane)
    db.delete_label_verdict(path, "PARENT")
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO processed_feed_items(feed_library_id, feed_item_id, stable_feed_key, guid, title, decision, run_id, materialized_zotero_key) VALUES (2, 42, 'feed:guid:stable', 'g', 'Paper', 'auto_materialized', 'run', 'PARENT')")
        conn.execute("INSERT INTO feed_key_aliases(old_key, stable_feed_key) VALUES ('feed:42', 'feed:guid:stable')")
    db.insert_or_update_label_verdict(path, item_key="feed:guid:stable", original_derived_priority="unknown",
                                     user_priority="must_read", comment="")
    revision = db.sync_current_fields(path)[("feed:guid:stable", "verdict")]["revision"]
    mutation = _mutation("feed:guid:stable", "verdict", None, revision, operation="delete")
    if lane == "online":
        asyncio.run(golden.remove_verdict(key))
    else:
        service.push(path, [mutation])
    assert reader.get_item_detail("PARENT")["tags"] == ["topic:x"]
    assert user_labels.reconcile_label_verdicts([_sample()], zdb, path).synced == 0

    db.insert_or_update_label_verdict(path, item_key="PARENT", original_derived_priority="unknown",
                                     user_priority="could_read", comment="")
    zotero.zotero_set_label_tag("PARENT", "could_read")
    verdict_effects.mirror_current_verdict(path, key)
    assert user_labels.reconcile_label_verdicts([_sample()], zdb, path).synced == 0
    assert set(reader.get_item_detail("PARENT")["tags"]) == {"topic:x", "label:could_read"}
    assert db.get_label_verdict(path, "PARENT")["user_priority"] == "could_read"


def test_reconciliation_retries_a_pending_mirror_when_zotero_is_closed(tmp_path, monkeypatch):
    path, zdb, item, reader, writer, retract = _setup(tmp_path, monkeypatch, "online")
    monkeypatch.setattr(writer, "is_connector_running", lambda: True)
    with pytest.raises(ZoteroWriteError):
        retract()
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)

    assert user_labels.reconcile_label_verdicts([_sample()], zdb, path).synced == 0

    assert reader.get_item_detail("PARENT")["tags"] == ["topic:x"]
    assert db.get_label_verdict(path, "PARENT") is None


def test_unmaterialized_feed_delete_stays_pending_until_a_target_exists(tmp_path, monkeypatch):
    from zotero_summarizer.storage import label_mirrors

    path, zdb, item, reader, writer, _ = _setup(tmp_path, monkeypatch, "online")
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO processed_feed_items(feed_library_id, feed_item_id, stable_feed_key, guid, title, decision, run_id) VALUES (2, 42, 'feed:guid:stable', 'g', 'Paper', 'pending', 'run')")
    db.insert_or_update_label_verdict(path, item_key="feed:guid:stable", original_derived_priority="unknown",
                                     user_priority="must_read", comment="")
    db.delete_label_verdict(path, "feed:guid:stable")

    assert not verdict_effects.mirror_current_verdict(path, "feed:guid:stable")
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM label_mirror_receipts").fetchone()[0] == 0
        conn.execute("UPDATE processed_feed_items SET materialized_zotero_key = 'PARENT'")

    assert label_mirrors.states(path)["PARENT"]["mirrored"] is None
    assert user_labels.reconcile_label_verdicts([_sample()], zdb, path).synced == 0
    assert reader.get_item_detail("PARENT")["tags"] == ["topic:x"]


@pytest.mark.parametrize("created", [False, True])
def test_old_set_effect_does_not_restore_a_deleted_label(tmp_path, monkeypatch, created):
    path, zdb, item, reader, writer, retract = _setup(tmp_path, monkeypatch, "online")
    retract()
    monkeypatch.setattr(verdict_effects, "append_training_row", lambda *args: None)
    monkeypatch.setattr(verdict_effects, "add_feed_verdict_to_library", lambda *args: {
        "added_to_library": created, "add_status": "added" if created else "not_applicable",
        "add_error": None,
    })
    if created:
        # An in-flight materialization may stamp its old label after retraction.
        zotero.zotero_set_label_tag("PARENT", "must_read")

    verdict_effects.apply_verdict_effects(path, "PARENT", "must_read", "")

    assert reader.get_item_detail("PARENT")["tags"] == ["topic:x"]
    assert db.get_label_verdict(path, "PARENT") is None


def test_failed_receipt_commit_is_retryable_without_another_zotero_write(tmp_path, monkeypatch):
    path, zdb, item, reader, writer, retract = _setup(tmp_path, monkeypatch, "online")
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON label_mirror_receipts BEGIN SELECT RAISE(ABORT, 'receipt unavailable'); END")
    with pytest.raises(sqlite3.IntegrityError, match="receipt unavailable"):
        retract()
    assert reader.get_item_detail("PARENT")["tags"] == ["topic:x"]
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM label_mirror_receipts").fetchone()[0] == 0
        conn.execute("DROP TRIGGER fail_receipt")
    backups = list(zdb.parent.glob(writer._BACKUP_GLOB))

    retract()

    assert list(zdb.parent.glob(writer._BACKUP_GLOB)) == backups
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM label_mirror_receipts").fetchone()[0] == 1


def test_migration_retains_and_mirrors_a_pre_sync_verdict(tmp_path, monkeypatch):
    from zotero_summarizer.storage.migrations import TRIAGE_MIGRATIONS, run_migrations

    _, zdb, item, reader, writer, _ = _setup(tmp_path, monkeypatch, "online")
    path = tmp_path / "legacy.db"
    run_migrations(path, "triage", TRIAGE_MIGRATIONS[:1])
    db.insert_or_update_label_verdict(path, item_key="PARENT", original_derived_priority="unknown",
                                     user_priority="dont_read", comment="before sync")
    run_migrations(path, "triage", TRIAGE_MIGRATIONS)
    run_migrations(path, "triage", TRIAGE_MIGRATIONS)

    assert verdict_effects.mirror_current_verdict(path, "PARENT")

    assert set(reader.get_item_detail("PARENT")["tags"]) == {"topic:x", "label:dont_read"}
    assert db.get_label_verdict(path, "PARENT")["comment"] == "before sync"
    db.delete_label_verdict(path, "PARENT")
    verdict_effects.mirror_current_verdict(path, "PARENT")
    assert reader.get_item_detail("PARENT")["tags"] == ["topic:x"]


@pytest.mark.parametrize("priority", ["could_read", "must_read"])
@pytest.mark.parametrize("lane", ["online", "offline", "offline_tag_origin"])
def test_tag_removal_reconciliation_cannot_delete_a_concurrent_user_label(tmp_path, monkeypatch, priority, lane):
    path, zdb, item, reader, writer, _ = _setup(tmp_path, monkeypatch, "online")
    db.insert_or_update_label_verdict(path, item_key="PARENT", original_derived_priority="zotero_label",
                                     user_priority="must_read", comment="")
    zotero.zotero_set_label_tag("PARENT", None)
    delete = db.delete_label_verdict

    def relabel_before_delete(*args, **kwargs):
        if lane == "online":
            db.insert_or_update_label_verdict(path, item_key="PARENT", original_derived_priority="unknown",
                                             user_priority=priority, comment="")
        else:
            revision = db.sync_current_fields(path)[("PARENT", "verdict")]["revision"]
            mutation = _mutation("PARENT", "verdict", priority, revision,
                                 model_priority="zotero_label" if lane == "offline_tag_origin" else "should_read")
            result = db.apply_sync_mutation(path, mutation)
            assert result["status"] == "applied"
        return delete(*args, **kwargs)

    monkeypatch.setattr(db, "delete_label_verdict", relabel_before_delete)

    assert user_labels.reconcile_label_verdicts([], zdb, path).removed == 0
    assert db.get_label_verdict(path, "PARENT")["user_priority"] == priority
    assert db.get_label_verdict(path, "PARENT")["original_derived_priority"] != "zotero_label"


def test_mirror_serializes_against_a_concurrent_local_assignment(tmp_path, monkeypatch):
    path, zdb, item, reader, writer, retract = _setup(tmp_path, monkeypatch, "online")
    entered, release = Event(), Event()
    dispatch = writer._dispatch_change

    def pause(conn, change, columns):
        entered.set()
        assert release.wait(5)
        dispatch(conn, change, columns)

    monkeypatch.setattr(writer, "_dispatch_change", pause)
    with ThreadPoolExecutor(max_workers=2) as pool:
        deleting = pool.submit(retract)
        try:
            assert entered.wait(5)
            assigning = pool.submit(db.insert_or_update_label_verdict, path, item_key="PARENT",
                                    original_derived_priority="unknown", user_priority="could_read", comment="new")
            with pytest.raises(TimeoutError):
                assigning.result(timeout=0.05)
        finally:
            release.set()
        deleting.result(timeout=5)
        assigning.result(timeout=5)
    verdict_effects.mirror_current_verdict(path, "PARENT")
    assert set(reader.get_item_detail("PARENT")["tags"]) == {"topic:x", "label:could_read"}


def test_unconfigured_zotero_keeps_deletion_pending_until_reconciliation(tmp_path, monkeypatch):
    path, zdb, item, reader, writer, retract = _setup(tmp_path, monkeypatch, "online")

    def unavailable():
        raise APIError(error="zotero_unavailable", message="not configured", status_code=503)

    with monkeypatch.context() as patch:
        patch.setattr(zotero, "get_zotero_reader_or_raise", unavailable)
        assert retract() == {"deleted": True}
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM label_mirror_receipts").fetchone()[0] == 0
    assert user_labels.reconcile_label_verdicts([_sample()], zdb, path).synced == 0
    assert reader.get_item_detail("PARENT")["tags"] == ["topic:x"]


def test_offline_mirror_value_error_is_not_reported_as_a_rejected_mutation(tmp_path, monkeypatch):
    path, zdb, item, reader, writer, retract = _setup(tmp_path, monkeypatch, "offline")

    def invalid_mirror(*args):
        raise ValueError("invalid mirror state")

    monkeypatch.setattr(verdict_effects, "zotero_set_label_tag", invalid_mirror)
    with pytest.raises(ValueError, match="invalid mirror state"):
        retract()
    assert db.get_label_verdict(path, "PARENT") is None
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT json_extract(result_json, '$.status') FROM sync_mutations").fetchone()[0] == "applied"
        assert conn.execute("SELECT COUNT(*) FROM label_mirror_receipts").fetchone()[0] == 0


def test_standalone_reconciliation_upgrades_the_store_without_creating_a_writer(tmp_path, monkeypatch):
    from zotero_summarizer.storage.migrations import TRIAGE_MIGRATIONS, run_migrations

    _, zdb, item, reader, writer, _ = _setup(tmp_path, monkeypatch, "online")
    path = tmp_path / "v4.db"
    run_migrations(path, "triage", TRIAGE_MIGRATIONS[:4])
    db.insert_or_update_label_verdict(path, item_key="PARENT", original_derived_priority="unknown",
                                     user_priority="must_read", comment="")
    db.delete_label_verdict(path, "PARENT")

    def unavailable():
        raise APIError(error="zotero_unavailable", message="standalone export", status_code=503)

    monkeypatch.setattr(zotero, "get_zotero_reader_or_raise", unavailable)

    assert user_labels.reconcile_label_verdicts([_sample()], zdb, path).synced == 0
    assert "label:must_read" in reader.get_item_detail("PARENT")["tags"]
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM label_mirror_receipts").fetchone()[0] == 0
