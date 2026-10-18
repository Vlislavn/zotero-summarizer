"""Owned native SQLite + actual generated publication/queue/confirmation contract."""
from contextlib import closing
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import asyncio
import json
import sqlite3

import pytest

from tests._zotero_fixtures import add_library_item
from tests._generated_write_fixtures import install_generated_writer
from tests.test_interpretation_binding import config
from tests.test_interpretation_producer import _base
from tests.test_interpretation_review_one import paper_io
from tests.test_review_cache import _isolate_cache
from tests.test_review_source_boundary import _Mapper
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.models import PendingChangeApplyRequest, PendingChangeMutationRequest
from zotero_summarizer.models.zotero_actions import GeneratedNoteProposal
from pydantic import ValidationError
from zotero_summarizer.services.library import _review_cache, deep_review
from zotero_summarizer.services.zotero import generated_notes, pending
from zotero_summarizer.storage import repositories


@pytest.fixture
def boundary(config, paper_io, tmp_path, monkeypatch):
    key = "LIBR0038"
    db, writer = install_generated_writer(tmp_path, monkeypatch)
    add_library_item(db, item_key=key, title="Owned paper")
    monkeypatch.setattr(deep_review, "_try_rebuild_render", Mock())
    monkeypatch.setattr(writer, "remove_items_from_collection", Mock(side_effect=AssertionError("No implicit collection write")))
    config.quality_review.chunk_strategy = "rank"
    generator = Mock()
    generator.pydantic_prompt.return_value = _base()
    ctx = {"reader": paper_io.reader, "config": config, "extractor": paper_io.extractor,
           "llm": generator, "llm_map": _Mapper("Generation context only."), "quality_enabled": True}
    deep_review._review_worker({"item_key": key, "pdf_path": str(paper_io.source)}, ctx, "")
    assert deep_review.status(key)["status"] == "ready"
    row, = repositories.get_pending_changes(item_key=key)
    presented, = asyncio.run(pending.list_pending_changes(item_key=key)).items
    return SimpleNamespace(key=key, row=row, presented=presented, writer=writer, db=db, io=paper_io,
                           entry=_review_cache._read_all(readonly=True)[key], ctx=ctx)


def approve(row, **kwargs):
    return asyncio.run(pending.apply_pending_changes(PendingChangeApplyRequest(
        change_ids=[row["id"]], previews={row["id"]: row["approval_preview"]["preview_sha256"]}, **kwargs,
    )))


def test_auto_review_stages_immutable_preview_with_zero_zotero_writes(boundary):
    b = boundary
    assert ZoteroReader(b.db.parent).get_item_notes(b.key) == []
    b.io.writer.assert_not_called()
    assert b.entry["zotero_note_written"] is False and b.entry["zotero_note_error"] is None
    preview = b.row["generated_preview"]
    assert preview["source"] == "deep_review" and preview["item_key"] == b.key
    assert preview["change_type"] == "upsert_note"
    assert json.loads(b.row["payload_json"]) == preview["payload"]
    assert [event["event"] for event in b.row["audit"]] == ["staged"]
    assert b.row["audit"][0]["actor"] == "system"
    assert not list(b.db.parent.glob(b.writer._BACKUP_GLOB))
    assert generated_notes.stage_current_digest(b.key) == b.row["id"]
    assert len(repositories.get_pending_changes(item_key=b.key)) == 1


def test_only_same_preview_applies_once_with_actor_source_backup_audit(boundary):
    b = boundary
    before = b.writer._dispatch_change

    def dispatch(conn, change, cols):
        row, = repositories.get_pending_changes_by_ids([b.row["id"]])
        assert row["status"] == "applying" and row["backup_path"]
        assert row["audit"][-1]["actor"] == "current_user"
        with sqlite3.connect(row["backup_path"]) as backup:
            assert backup.execute("SELECT COUNT(*) FROM itemNotes").fetchone()[0] == 0
        before(conn, change, cols)

    b.writer._dispatch_change = dispatch
    result = approve(b.presented)
    assert result["applied"] == 1 and result["failed"] == 0
    assert "inbox_removed" not in result and "inbox_removed_error" not in result
    notes = ZoteroReader(b.db.parent).get_item_notes(b.key)
    assert len(notes) == 1
    applied, = repositories.get_pending_changes_by_ids([b.row["id"]])
    assert [event["event"] for event in applied["audit"]] == ["staged", "approved", "applied"]
    assert applied["audit"][1]["source"] == "pending_confirmation"
    assert applied["audit"][1]["preview_sha256"] == b.presented["approval_preview"]["preview_sha256"]
    assert applied["audit"][1]["generated_preview_sha256"] == b.row["generated_preview"]["preview_sha256"]
    assert applied["backup_path"] == result["backup_path"]
    assert approve(b.presented)["applied"] == 0
    assert ZoteroReader(b.db.parent).get_item_notes(b.key) == notes
    assert len(list(b.db.parent.glob(b.writer._BACKUP_GLOB))) == 1
    b.writer.remove_items_from_collection.assert_not_called()


@pytest.mark.parametrize("mutation", ["missing_approval", "wrong_digest", "inner_hash", "wrong_target", "new_review", "aba", "source_bytes"])
def test_changed_or_unapproved_preview_never_dispatches(boundary, mutation):
    b = boundary
    row = deepcopy(b.presented)
    if mutation == "missing_approval":
        with pytest.raises(ValidationError):
            PendingChangeApplyRequest(change_ids=[row["id"]])
    else:
        if mutation == "wrong_digest":
            row["approval_preview"]["preview_sha256"] = "0" * 64
        elif mutation == "inner_hash":
            row["approval_preview"]["preview_sha256"] = row["generated_preview"]["preview_sha256"]
        elif mutation == "wrong_target":
            _review_cache._write_one("LIBR0039", b.entry)
            generated_notes.stage_current_digest("LIBR0039")
            other, = asyncio.run(pending.list_pending_changes(item_key="LIBR0039")).items
            row["approval_preview"]["preview_sha256"] = other["approval_preview"]["preview_sha256"]
        elif mutation in {"new_review", "aba"}:
            changed = deepcopy(b.entry)
            changed["digest"]["tldr"] += " A changed publication."
            _review_cache._write_one(b.key, changed)
            generated_notes.stage_current_digest(b.key)
            if mutation == "aba":
                _review_cache._write_one(b.key, b.entry)
                generated_notes.stage_current_digest(b.key)
                _entry, revision = _review_cache.publication_snapshot(b.key)
                assert revision != row["generated_preview"]["review_revision"]
        elif mutation == "source_bytes":
            b.io.source.write_bytes(b"R" * b.io.source.stat().st_size)
        request = PendingChangeApplyRequest(change_ids=[row["id"]], previews={
            row["id"]: row["approval_preview"]["preview_sha256"],
        })
        with pytest.raises(APIError) as error:
            asyncio.run(pending.apply_pending_changes(request))
        assert error.value.status_code == 409
    assert ZoteroReader(b.db.parent).get_item_notes(b.key) == []
    assert not list(b.db.parent.glob(b.writer._BACKUP_GLOB))


def test_confirmed_generated_failure_requires_explicit_current_retry(boundary, monkeypatch):
    b = boundary
    with monkeypatch.context() as patch:
        patch.setattr(b.writer, "_dispatch_change", Mock(side_effect=ValueError("Owned confirmed failure")))
        failed = approve(b.presented)
    assert failed["applied"] == 0 and failed["failed"] == 1
    assert failed["failed_items"][0]["id"] == b.row["id"]
    assert failed["failed_items"][0]["error"] == "Owned confirmed failure"
    assert ZoteroReader(b.db.parent).get_item_notes(b.key) == []
    current, = asyncio.run(pending.list_pending_changes(status="failed", item_key=b.key)).items
    with pytest.raises(APIError) as error:
        approve(current)
    assert error.value.status_code == 409
    assert approve(current, retry=True)["applied"] == 1
    saved, = repositories.get_pending_changes_by_ids([b.row["id"]])
    assert saved["status"] == "applied" and saved["error_message"] == ""
    assert [event["event"] for event in saved["audit"]] == ["staged", "approved", "failed", "approved", "applied"]
    assert len(ZoteroReader(b.db.parent).get_item_notes(b.key)) == 1
    b.writer.remove_items_from_collection.assert_not_called()


@pytest.mark.parametrize("field,value", [("change_type", "set_field"), ("source", "paper_text"),
                                         ("body_sha256", "0" * 64)])
def test_closed_proposal_refuses_other_actions_sources_or_body_digests(boundary, field, value):
    proposal = json.loads(boundary.row["proposal_json"])
    proposal[field] = value
    with pytest.raises(ValidationError):
        GeneratedNoteProposal.model_validate(proposal)


def test_generated_target_and_payload_cannot_be_edited_or_deleted(boundary):
    row = boundary.row
    assert not repositories.update_pending_change_payload(row["id"], {"note_html": "replacement"})
    for sql in (
        "UPDATE pending_changes SET item_key='OTHER' WHERE id=?",
        "UPDATE pending_changes SET payload_json='{}' WHERE id=?",
        "UPDATE pending_changes SET proposal_json=NULL WHERE id=?",
        "DELETE FROM pending_changes WHERE id=?",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            with closing(repositories._get_conn()) as conn:
                conn.execute(sql, (row["id"],))
    assert repositories.get_pending_changes_by_ids([row["id"]])[0] == row


def test_uncertain_delivery_claim_is_not_replayed_or_upgraded(boundary, monkeypatch):
    b = boundary
    attempt = Mock(side_effect=RuntimeError("Owned uncertain external outcome"))
    monkeypatch.setattr(b.writer, "apply_changes", attempt)
    with pytest.raises(RuntimeError):
        approve(b.presented)
    uncertain, = repositories.get_pending_changes_by_ids([b.row["id"]])
    assert uncertain["status"] == "applying" and uncertain["backup_path"]
    assert uncertain["audit"][-1]["event"] == "approved"
    for retry in (False, True):
        with pytest.raises(APIError) as error:
            approve(b.presented, retry=retry)
        assert error.value.status_code == 409
    assert attempt.call_count == 1


def test_rejection_is_audited_without_native_mutation(boundary):
    b = boundary
    result = asyncio.run(pending.reject_pending_changes(PendingChangeMutationRequest(change_ids=[b.row["id"]])))
    assert result == {"updated": 1}
    row, = repositories.get_pending_changes_by_ids([b.row["id"]])
    assert row["status"] == "rejected" and row["audit"][-1]["actor"] == "current_user"
    assert row["audit"][-1]["source"] == "pending_rejection"
    with pytest.raises(APIError):
        approve(b.presented)
    assert ZoteroReader(b.db.parent).get_item_notes(b.key) == []


def test_no_published_digest_cannot_request_a_native_write(boundary):
    b = boundary
    assert generated_notes.stage_current_digest("ABSENT01") is None
    assert ZoteroReader(b.db.parent).get_item_notes(b.key) == []
    assert not list(b.db.parent.glob(b.writer._BACKUP_GLOB))


def test_readonly_cache_creates_no_lock_or_other_artifact(tmp_path, monkeypatch):
    path = tmp_path / "empty.json"
    monkeypatch.setattr(_review_cache, "_cache_path", lambda: path)
    assert _review_cache._read_all(readonly=True) == {}
    assert list(tmp_path.iterdir()) == []
