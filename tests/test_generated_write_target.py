"""152 target binding and bounded backup lifecycle on owned native SQLite files."""
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock
import json
import sqlite3

import pytest
from pydantic import ValidationError

from tests._zotero_fixtures import add_library_item, build_zotero_db
from tests.test_generated_write_review import approve, boundary
from tests.test_interpretation_binding import config
from tests.test_interpretation_review_one import paper_io
from tests.test_review_cache import _isolate_cache
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriteError, ZoteroWriter
from zotero_summarizer.services.library import _review_cache, deep_review
from zotero_summarizer.services.zotero import generated_notes, pending, zotero
from zotero_summarizer.models import PendingChangeApplyRequest, PendingChangeMutationRequest
from zotero_summarizer.storage import repositories
import asyncio


def test_switching_native_database_with_same_key_cannot_reuse_approval(boundary, tmp_path, monkeypatch):
    b = boundary
    original_source = b.io.source.read_bytes()
    other = build_zotero_db(tmp_path / "other-zotero")
    add_library_item(other, item_key=b.key, title="Different owned database")
    writer = ZoteroWriter(other.parent)
    backup, apply = Mock(wraps=writer.backup_database), Mock(wraps=writer.apply_changes)
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)
    monkeypatch.setattr(writer, "backup_database", backup)
    monkeypatch.setattr(writer, "apply_changes", apply)
    monkeypatch.setattr(zotero, "get_zotero_writer_or_raise", lambda: writer)

    with pytest.raises(APIError) as error:
        approve(b.presented)

    assert error.value.status_code == 409
    backup.assert_not_called()
    apply.assert_not_called()
    assert not list(other.parent.glob(writer._BACKUP_GLOB))
    assert ZoteroReader(other.parent).get_item_notes(b.key) == []
    assert ZoteroReader(b.db.parent).get_item_notes(b.key) == []
    assert b.io.source.read_bytes() == original_source
    monkeypatch.setattr(zotero, "get_zotero_writer_or_raise", lambda: b.writer)
    assert approve(b.presented)["applied"] == 1  # refusal is target-specific, not a blanket hold


@pytest.mark.parametrize("change", ["file", "user_library"])
def test_native_target_replacement_conflicts_before_backup(boundary, tmp_path, monkeypatch, change):
    b = boundary
    if change == "file":
        replacement = build_zotero_db(tmp_path / "replacement")
        add_library_item(replacement, item_key=b.key, title="Replacement")
        prior = b.db.stat()
        replacement.replace(b.db)
        assert (b.db.stat().st_dev, b.db.stat().st_ino) != (prior.st_dev, prior.st_ino)
    else:
        with sqlite3.connect(b.db) as conn:
            conn.execute("UPDATE libraries SET libraryID=101 WHERE type='user'")
            conn.execute("UPDATE items SET libraryID=101 WHERE key=?", (b.key,))
    backup = Mock(wraps=b.writer.backup_database)
    monkeypatch.setattr(b.writer, "backup_database", backup)
    with pytest.raises(APIError) as error:
        approve(b.presented)
    assert error.value.status_code == 409
    backup.assert_not_called()
    assert ZoteroReader(b.db.parent).get_item_notes(b.key) == []


def test_unrelated_native_edits_do_not_invalidate_bound_target(boundary):
    b = boundary
    before = b.writer.target_identity()
    add_library_item(b.db, item_key="UNRELATED", title="An unrelated native edit")
    assert b.writer.target_identity() == before
    assert b.row["generated_preview"]["target"] == before.model_dump()
    assert approve(b.presented)["applied"] == 1


def test_generated_only_confirmations_use_existing_backup_retention(boundary):
    b = boundary
    paths, revisions = [], set()
    for index in range(b.writer._BACKUP_KEEP + 2):
        if index:
            _review_cache._write_one(b.key, deepcopy(b.entry))
            change_id = generated_notes.stage_current_digest(b.key)
            row, = asyncio.run(pending.list_pending_changes(item_key=b.key)).items
            assert row["id"] == change_id
        else:
            row = b.presented
        revisions.add(row["generated_preview"]["review_revision"])
        result = approve(row)
        assert result["applied"] == 1
        saved, = repositories.get_pending_changes_by_ids([row["id"]])
        assert saved["backup_path"] == saved["audit"][1]["backup_path"] == result["backup_path"]
        paths.append(result["backup_path"])
    assert len(revisions) == len(paths) == len(set(paths)) == b.writer._BACKUP_KEEP + 2
    retained = {str(path) for path in b.db.parent.glob(b.writer._BACKUP_GLOB)}
    assert retained == set(paths[-b.writer._BACKUP_KEEP:])
    assert all(not Path(path).exists() for path in paths[:-b.writer._BACKUP_KEEP])
    assert len(ZoteroReader(b.db.parent).get_item_notes(b.key)) == 1


def test_target_replaced_after_backup_is_blocked_at_adapter_boundary(boundary, tmp_path, monkeypatch):
    b = boundary
    replacement = build_zotero_db(tmp_path / "predispatch-replacement")
    add_library_item(replacement, item_key=b.key, title="Replacement after backup")
    original_backup = b.writer.backup_database

    def backup_then_replace(**kwargs):
        path = original_backup(**kwargs)
        replacement.replace(b.db)
        return path

    dispatch = Mock(wraps=b.writer._dispatch_change)
    monkeypatch.setattr(b.writer, "backup_database", backup_then_replace)
    monkeypatch.setattr(b.writer, "_dispatch_change", dispatch)
    with pytest.raises(ZoteroWriteError):
        approve(b.presented)
    dispatch.assert_not_called()
    uncertain, = repositories.get_pending_changes_by_ids([b.row["id"]])
    assert uncertain["status"] == "applying" and uncertain["backup_path"]
    with sqlite3.connect(uncertain["backup_path"]) as conn:
        assert conn.execute("SELECT value FROM itemDataValues WHERE value='Owned paper'").fetchone() == ("Owned paper",)
    assert ZoteroReader(b.db.parent).get_item_notes(b.key) == []


@pytest.mark.parametrize("reference", ["foreign", "unissued", "replaced", "conflicting_flags"])
def test_precreated_backup_must_be_owned_scoped_unchanged_and_unambiguous(boundary, tmp_path, reference):
    b = boundary
    target = b.writer.target_identity()
    if reference == "foreign":
        other = build_zotero_db(tmp_path / "foreign-backup")
        other_writer = ZoteroWriter(other.parent)
        path = other_writer.backup_database(expected_target=other_writer.target_identity())
    elif reference == "unissued":
        path = b.writer.backup_database()  # ordinary backup is not a prepared generated-write reference
    else:
        path = b.writer.backup_database(expected_target=target)
        if reference == "replaced":
            replacement = tmp_path / "copied-backup.sqlite"
            replacement.write_bytes(Path(path).read_bytes())
            replacement.replace(path)
    before = set(b.db.parent.glob(b.writer._BACKUP_GLOB))
    with pytest.raises((ValueError, ZoteroWriteError)):
        b.writer.apply_changes([b.row], reference == "conflicting_flags",
                               precreated_backup=path, expected_target=target)
    assert set(b.db.parent.glob(b.writer._BACKUP_GLOB)) == before
    assert ZoteroReader(b.db.parent).get_item_notes(b.key) == []


def test_new_builder_requires_a_bound_native_target(boundary):
    b = boundary
    with pytest.raises(ValidationError):
        generated_notes._proposal(b.key, b.entry, b.row["generated_preview"]["review_revision"], None)


def test_legacy_unbound_proposal_can_be_displayed_and_rejected_but_not_approved(boundary):
    b = boundary
    proposal = json.loads(b.row["proposal_json"])
    proposal.pop("target", None)
    with closing(repositories._get_conn()) as conn:
        cursor = conn.execute("""INSERT INTO pending_changes
            (item_key,item_title,change_type,payload_json,proposal_json) VALUES (?,?,?,?,?)""",
            (b.key, "Unbound legacy proposal", b.row["change_type"], b.row["payload_json"], json.dumps(proposal)))
        legacy_id = cursor.lastrowid
        conn.commit()
    row, = repositories.get_pending_changes_by_ids([legacy_id])
    assert row["generated_preview"]["target"] is None
    displayed = asyncio.run(pending.list_pending_changes()).items
    assert next(item for item in displayed if item["id"] == legacy_id)["approval_preview"] is None
    with pytest.raises(APIError) as error:
        asyncio.run(pending.apply_pending_changes(PendingChangeApplyRequest(
            change_ids=[legacy_id], previews={legacy_id: b.presented["approval_preview"]["preview_sha256"]},
        )))
    assert error.value.status_code == 409
    result = asyncio.run(pending.reject_pending_changes(PendingChangeMutationRequest(change_ids=[legacy_id])))
    assert result == {"updated": 1}
    assert ZoteroReader(b.db.parent).get_item_notes(b.key) == []
    assert not list(b.db.parent.glob(b.writer._BACKUP_GLOB))


def test_missing_writer_preserves_published_review_with_no_new_action(boundary, monkeypatch, caplog):
    b = boundary
    old_ids = {row["id"] for row in repositories.get_pending_changes(item_key=b.key)}
    _entry, prior_revision = _review_cache.publication_snapshot(b.key)

    def unavailable():
        raise APIError(error="zotero_unavailable", message="Owned absent writer", status_code=503)

    monkeypatch.setattr(zotero, "get_zotero_writer_or_raise", unavailable)
    with caplog.at_level("WARNING", logger=generated_notes.__name__):
        deep_review._review_worker({"item_key": b.key, "pdf_path": str(b.io.source)}, b.ctx, "")
    entry, revision = _review_cache.publication_snapshot(b.key)
    assert deep_review.status(b.key)["status"] == "ready"
    assert entry["digest"] == b.entry["digest"] and revision != prior_revision
    assert {row["id"] for row in repositories.get_pending_changes(item_key=b.key)} == old_ids
    diagnostics = [record.diagnostic for record in caplog.records if hasattr(record, "diagnostic")]
    assert diagnostics == ["zotero_unavailable"]
    assert ZoteroReader(b.db.parent).get_item_notes(b.key) == []
