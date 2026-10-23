"""Synthetic real producer/wrapper regressions: IO is optional, defects are not."""
from contextlib import closing
import json
import sqlite3
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tests._direct_write_fixtures import native, sql_rows
from tests._zotero_fixtures import add_library_item, build_zotero_db
from tests.test_review_questions import _app, _database
from zotero_summarizer.integrations.zotero_read import ZoteroReader, ZoteroReadError
from zotero_summarizer.integrations.zotero_write import ZoteroWriter, ZoteroWriteError
from zotero_summarizer.runtime import get_context
from zotero_summarizer.services.golden import verdict_effects
from zotero_summarizer.services.library import review_questions
from zotero_summarizer.services.zotero import mirror_notes, pending, zotero
from zotero_summarizer.storage import repositories, review_note_projection


def test_classification_keeps_native_exception_args_and_defaults_unknown():
    for error in (ZoteroWriteError, ZoteroReadError):
        for args in [(), ("one",), ("one", "two")]:
            exc = error(*args)
            assert exc.args == args and exc.expected_io is False
            assert error(*args, expected_io="true").expected_io is False
            assert error(*args, expected_io=True).expected_io is True


@pytest.fixture
def real_boundary(tmp_path, monkeypatch, request):
    config = _database(tmp_path)
    db = build_zotero_db(tmp_path / "synthetic-zotero")
    add_library_item(db, item_key="ITEM0001", title="Synthetic paper")
    reader, writer = ZoteroReader(db.parent), ZoteroWriter(db.parent)
    state = get_context().state
    monkeypatch.setattr(state, "zotero_reader", reader)
    monkeypatch.setattr(state, "zotero_writer", writer)
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)
    payload = {"submission_id": str(uuid4()), "item_key": "ITEM0001", "question": "Question <one>?", "origin": "typed"}
    repositories.upsert_review_note(config.triage_db_path, "ITEM0001", "Manual & note")
    if getattr(request, "param", True):
        review_questions.save_question(payload)
    return config.triage_db_path, db, reader, writer, payload


def _staged_revision(path):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("SELECT revision FROM review_note_mirror_intents WHERE item_key='ITEM0001'").fetchone()[0]


def _assert_durable_pending(env):
    path, _, _, _, payload = env
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT submission_id, item_key, question FROM review_questions").fetchall() == [
            (payload["submission_id"], payload["item_key"], payload["question"]),
        ]
        assert conn.execute("SELECT note FROM review_notes WHERE item_key='ITEM0001'").fetchone() == ("Manual & note",)
        assert review_note_projection.snapshot(conn, "ITEM0001")["pending"] is True
        assert conn.execute("SELECT acknowledged FROM review_note_mirror_intents").fetchone() == (0,)


def _writer_defect(env, monkeypatch, kind):
    _, db, reader, writer, _ = env
    if kind == "schema":
        read = reader.get_item_detail

        def read_then_lose_schema(key):
            detail = read(key)
            assert detail is not None
            with sqlite3.connect(db) as conn:
                conn.execute("DROP TABLE itemNotes")
            return detail
        monkeypatch.setattr(reader, "get_item_detail", read_then_lose_schema)
    else:
        error = {"type": TypeError, "value": ValueError, "unknown": ZoteroWriteError}[kind]

        def invalid_dispatch(*args):
            raise error("synthetic producer defect")
        monkeypatch.setattr(writer, "_dispatch_change", invalid_dispatch)


@pytest.mark.parametrize("kind", ["schema", "type", "value", "unknown"])
def test_real_writer_defect_survives_result_and_service_wrappers(real_boundary, monkeypatch, kind):
    path, db, _, writer, _ = real_boundary
    _writer_defect(real_boundary, monkeypatch, kind)
    monkeypatch.setattr(review_note_projection, "acknowledge", lambda *_: pytest.fail("defect acknowledged"))
    try:
        with pytest.raises(ZoteroWriteError) as raised:
            verdict_effects.mirror_review_note(path, "ITEM0001")
    finally:
        _assert_durable_pending(real_boundary)
    assert raised.value.expected_io is False
    if kind == "schema":
        assert isinstance(raised.value.__cause__, sqlite3.OperationalError)
        assert raised.value.__cause__.sqlite_errorcode == sqlite3.SQLITE_ERROR
    else:
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT COUNT(*) FROM itemNotes").fetchone() == (0,)
    assert len(list(db.parent.glob(writer._BACKUP_GLOB))) == 1


def test_real_target_reader_schema_failure_is_not_optional(real_boundary, monkeypatch):
    path, db, _, writer, _ = real_boundary
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE itemData")
    monkeypatch.setattr(review_note_projection, "acknowledge", lambda *_: pytest.fail("reader failure acknowledged"))
    try:
        with pytest.raises(ZoteroReadError) as raised:
            verdict_effects.mirror_review_note(path, "ITEM0001")
    finally:
        _assert_durable_pending(real_boundary)
    assert raised.value.expected_io is False
    assert raised.value.__cause__.sqlite_errorcode == sqlite3.SQLITE_ERROR
    assert list(db.parent.glob(writer._BACKUP_GLOB)) == []


@pytest.mark.parametrize("kind", ["schema", "type", "value"])
@pytest.mark.parametrize("real_boundary", [False], indirect=True)
def test_real_writer_defect_after_http_save_prevents_qa(real_boundary, monkeypatch, kind):
    path, _, _, _, payload = real_boundary
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM review_questions").fetchone() == (0,)
    _writer_defect(real_boundary, monkeypatch, kind)
    qa_calls = []
    monkeypatch.setattr("zotero_summarizer.api.routes.library.qa.ask_paper",
                        lambda *_a, **_k: qa_calls.append("qa") or {"answer": None, "abstained": True})
    with TestClient(_app(), raise_server_exceptions=False) as client:
        response = client.post("/api/library/ask", json=payload)
    _assert_durable_pending(real_boundary)
    assert response.status_code == 503
    assert response.json()["error"] == "zotero_write_failed"
    assert qa_calls == []


def test_malformed_generated_note_is_terminal_not_optional(real_boundary, monkeypatch):
    path, db, _, writer, _ = real_boundary
    before = native(db)
    backup_probe = Mock(wraps=writer.backup_database)
    apply_probe = Mock(wraps=writer.apply_changes)
    monkeypatch.setattr(writer, "backup_database", backup_probe)
    monkeypatch.setattr(writer, "apply_changes", apply_probe)
    monkeypatch.setattr(mirror_notes, "build_user_note_html", lambda _: "<p>missing ownership footer</p>")
    try:
        with pytest.raises(ValidationError) as raised:
            verdict_effects.mirror_review_note(path, "ITEM0001")
    finally:
        _assert_durable_pending(real_boundary)
    assert type(raised.value) is ValidationError
    error, = raised.value.errors()
    assert error["loc"] == ("note_html",) and error["type"] == "value_error"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM itemNotes").fetchone() == (0,)
    backup_probe.assert_not_called()
    apply_probe.assert_not_called()
    assert list(db.parent.glob(writer._BACKUP_GLOB)) == []
    assert sql_rows(path, "SELECT * FROM pending_changes") == []
    assert native(db) == before


def test_healthy_actual_backup_owned_upsert_ack_and_noop(real_boundary):
    path, db, reader, writer, _ = real_boundary
    revision = _staged_revision(path)
    first = verdict_effects.mirror_review_note(path, "ITEM0001")
    assert first == {"note_written": True, "note_error": None, "note_pending": False,
                     "projection_revision": revision, "target_key": "ITEM0001"}
    notes = reader.get_item_notes("ITEM0001")
    assert len(notes) == 1 and "Manual &amp; note" in notes[0]["note"]
    assert "Question &lt;one&gt;?" in notes[0]["note"] and pending.USER_NOTE_MARKER in notes[0]["note"]
    backups = list(db.parent.glob(writer._BACKUP_GLOB))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM itemNotes").fetchone() == (0,)
    again = verdict_effects.mirror_review_note(path, "ITEM0001")
    assert again["note_written"] is False and again["note_pending"] is False
    assert list(db.parent.glob(writer._BACKUP_GLOB)) == backups
    with sqlite3.connect(db) as conn:
        note_id = conn.execute("SELECT itemID FROM itemNotes").fetchone()[0]
    repositories.upsert_review_note(path, "ITEM0001", "Updated")
    assert verdict_effects.mirror_review_note(path, "ITEM0001")["note_pending"] is False
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT itemID FROM itemNotes").fetchall() == [(note_id,)]


@pytest.mark.parametrize("kind", ["connector", "readonly", "busy"])
def test_expected_real_external_io_remains_optional(real_boundary, monkeypatch, kind):
    path, db, _, writer, _ = real_boundary
    connect = sqlite3.connect
    revision = _staged_revision(path)
    before, target = native(db), writer.target_identity()
    backup_probe = Mock(wraps=writer.backup_database)
    apply_probe = Mock(wraps=writer.apply_changes)
    monkeypatch.setattr(writer, "backup_database", backup_probe)
    monkeypatch.setattr(writer, "apply_changes", apply_probe)
    with closing(connect(db, timeout=0)) as locked:
        if kind == "connector":
            monkeypatch.setattr(writer, "is_connector_running", lambda: True)
        else:
            def io_connection(filename, *args, **kwargs):
                if str(filename) == str(db):
                    if kind == "readonly":
                        return connect(f"{db.as_uri()}?mode=ro", uri=True, timeout=0)
                    return connect(filename, timeout=0)
                return connect(filename, *args, **kwargs)
            monkeypatch.setattr(sqlite3, "connect", io_connection)
            retry = writer._retry_on_lock
            monkeypatch.setattr(writer, "_retry_on_lock", lambda fn, **kw: retry(fn, max_retries=0, delay_secs=0))
            if kind == "busy":
                backup = writer.backup_database

                def backup_then_lock(*, expected_target=None):
                    result = backup(expected_target=expected_target)
                    locked.execute("BEGIN IMMEDIATE")
                    return result
                monkeypatch.setattr(writer, "backup_database", backup_then_lock)
        receipt = verdict_effects.mirror_review_note(path, "ITEM0001")
    expected_error = "ZoteroWriteError" if kind == "connector" else "zotero_write_uncertain"
    assert receipt == {"note_written": False, "note_error": expected_error, "note_pending": True,
                       "projection_revision": revision, "target_key": "ITEM0001"}
    _assert_durable_pending(real_boundary)
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM itemNotes").fetchone() == (0,)
    assert native(db) == before
    history = sql_rows(path, "SELECT * FROM pending_changes ORDER BY id")
    backups = list(db.parent.glob(writer._BACKUP_GLOB))
    if kind == "connector":
        assert history == [] and backups == []
        backup_probe.assert_not_called()
        apply_probe.assert_not_called()
        return
    row, = sql_rows(path, "SELECT id,status,backup_path,audit_json,applied_at FROM pending_changes")
    change_id, status, backup_ref, audit_json, applied_at = row
    assert type(change_id) is int and change_id > 0
    assert status == "applying" and applied_at is None
    audit = json.loads(audit_json)
    assert [event["event"] for event in audit] == ["mirror_requested"]
    assert audit[0]["target"] == target.model_dump()
    assert (audit[0]["actor"], audit[0]["source"]) == ("system", "mirror_user_note")
    assert audit[0]["backup_path"] == backup_ref and audit[0]["recovery"] == "backup_only"
    assert len(backups) == 1 and str(backups[0]) == backup_ref
    assert native(backups[0]) == before
    assert sql_rows(backups[0], "PRAGMA integrity_check") == [("ok",)]
    backup_probe.assert_called_once_with(expected_target=target)
    apply_probe.assert_called_once()
    assert apply_probe.call_args.args[0][0]["id"] == change_id and apply_probe.call_args.args[1] is False
    assert apply_probe.call_args.kwargs == {"precreated_backup": backup_ref, "expected_target": target}
    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(writer, "backup_database", backup_probe)
    files = {file.name: file.read_bytes() for file in db.parent.iterdir() if file.is_file()}
    assert verdict_effects.mirror_review_note(path, "ITEM0001") == receipt
    backup_probe.assert_called_once_with(expected_target=target)
    apply_probe.assert_called_once()
    assert sql_rows(path, "SELECT * FROM pending_changes ORDER BY id") == history
    assert native(db) == before
    assert {file.name: file.read_bytes() for file in db.parent.iterdir() if file.is_file()} == files
    _assert_durable_pending(real_boundary)


def test_real_reader_cannot_open_io_remains_optional(real_boundary):
    path, db, reader, writer, _ = real_boundary
    revision = _staged_revision(path)
    before = native(db)
    db.rename(db.with_suffix(".unavailable"))
    files = {file.name: file.read_bytes() for file in db.parent.iterdir() if file.is_file()}
    no_delivery = Mock(side_effect=AssertionError("target resolution reached downstream IO"))
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(reader, "get_item_detail", no_delivery)
        patcher.setattr(writer, "backup_database", no_delivery)
        patcher.setattr(writer, "apply_changes", no_delivery)
        receipt = verdict_effects.mirror_review_note(path, "ITEM0001")

    assert receipt == {"note_written": False, "note_error": "FileNotFoundError", "note_pending": True,
                       "projection_revision": revision, "target_key": "ITEM0001"}
    _assert_durable_pending(real_boundary)
    assert list(db.parent.glob(writer._BACKUP_GLOB)) == []
    no_delivery.assert_not_called()
    assert sql_rows(path, "SELECT * FROM pending_changes") == []
    assert not db.exists() and native(db.with_suffix(".unavailable")) == before
    assert {file.name: file.read_bytes() for file in db.parent.iterdir() if file.is_file()} == files


@pytest.mark.parametrize("metadata", [None, False, "true"])
def test_unknown_or_malformed_failure_record_fails_closed(real_boundary, monkeypatch, metadata):
    path, db, _, writer, _ = real_boundary
    apply = writer.apply_changes
    before, revision = native(db), _staged_revision(path)

    def unclassified_dispatch(*args):
        raise ZoteroWriteError("unclassified producer failure")

    def apply_with_metadata(changes, create_backup=True, *, precreated_backup=None, expected_target=None):
        change, = changes
        assert type(change["id"]) is int and change["id"] > 0
        assert create_backup is False
        prepared = writer._prepared_backup
        assert prepared is not None
        assert prepared[0] == precreated_backup == change["backup_path"]
        assert prepared[2] == expected_target == writer.target_identity()
        with closing(sqlite3.connect(path)) as conn:
            assert conn.execute(
                "SELECT id, status, backup_path FROM pending_changes"
            ).fetchall() == [(change["id"], "applying", precreated_backup)]
        result = apply(changes, create_backup, precreated_backup=precreated_backup, expected_target=expected_target)
        assert writer._prepared_backup is None
        assert result["backup_path"] == precreated_backup
        assert result["applied_ids"] == []
        failure, = result["failed"]
        assert type(failure["id"]) is int and failure["id"] == change["id"]
        assert failure["error"] == "unclassified producer failure"
        assert failure["expected_io"] is False
        if metadata is None:
            del failure["expected_io"]
        elif metadata is not False:
            failure["expected_io"] = metadata
        return result

    monkeypatch.setattr(writer, "_dispatch_change", unclassified_dispatch)
    monkeypatch.setattr(writer, "apply_changes", apply_with_metadata)
    backup_probe = Mock(wraps=writer.backup_database)
    apply_probe = Mock(wraps=writer.apply_changes)
    monkeypatch.setattr(writer, "backup_database", backup_probe)
    monkeypatch.setattr(writer, "apply_changes", apply_probe)
    with pytest.raises(ZoteroWriteError if metadata is False else ValueError) as raised:
        verdict_effects.mirror_review_note(path, "ITEM0001")
    assert type(raised.value) is (ZoteroWriteError if metadata is False else ValueError)
    if metadata is False:
        assert raised.value.expected_io is False
    _assert_durable_pending(real_boundary)
    assert native(db) == before and before["notes"] == []
    row, = sql_rows(path, "SELECT id,status,backup_path,audit_json,applied_at FROM pending_changes")
    change_id, status, backup_ref, audit_json, applied_at = row
    assert type(change_id) is int and change_id > 0
    assert status == ("failed" if metadata is False else "applying") and applied_at is None
    audit = json.loads(audit_json)
    assert [event["event"] for event in audit] == (
        ["mirror_requested", "mirror_failed"] if metadata is False else ["mirror_requested"])
    assert audit[0]["target"] == writer.target_identity().model_dump()
    if metadata is False:
        assert audit[-1]["expected_io"] is False
        assert (audit[-1]["actor"], audit[-1]["source"]) == ("system", "mirror_user_note")
    backup, = db.parent.glob(writer._BACKUP_GLOB)
    assert str(backup) == backup_ref and native(backup) == before
    assert sql_rows(backup, "PRAGMA integrity_check") == [("ok",)]
    backup_probe.assert_called_once_with(expected_target=writer.target_identity())
    apply_probe.assert_called_once()
    if metadata is not False:
        history = sql_rows(path, "SELECT * FROM pending_changes ORDER BY id")
        files = {file.name: file.read_bytes() for file in db.parent.iterdir() if file.is_file()}
        assert verdict_effects.mirror_review_note(path, "ITEM0001") == {
            "note_written": False, "note_error": "zotero_write_uncertain", "note_pending": True,
            "projection_revision": revision, "target_key": "ITEM0001"}
        backup_probe.assert_called_once_with(expected_target=writer.target_identity())
        apply_probe.assert_called_once()
        assert sql_rows(path, "SELECT * FROM pending_changes ORDER BY id") == history
        assert native(db) == before
        assert {file.name: file.read_bytes() for file in db.parent.iterdir() if file.is_file()} == files
        _assert_durable_pending(real_boundary)


def test_explicit_io_row_classification_survives_both_real_wrappers(real_boundary, monkeypatch):
    path, _, _, writer, _ = real_boundary

    def io_dispatch(*args):
        raise ZoteroWriteError("explicit synthetic IO boundary", expected_io=True)

    monkeypatch.setattr(writer, "_dispatch_change", io_dispatch)
    monkeypatch.setattr(review_note_projection, "acknowledge", lambda *_: pytest.fail("failed row acknowledged"))
    receipt = verdict_effects.mirror_review_note(path, "ITEM0001")

    assert receipt["note_written"] is False and receipt["note_pending"] is True
    assert receipt["note_error"] == "ZoteroWriteError"
    _assert_durable_pending(real_boundary)


def test_invalid_queue_row_still_isolates_and_reports_without_undoing_healthy_rows(real_boundary):
    _, _, reader, writer, _ = real_boundary
    result = writer.apply_changes([
        {"id": 1, "item_key": "ITEM0001", "change_type": "add_note", "payload_json": {"note_html": "<p>one</p>"}},
        {"id": 2, "item_key": "ITEM0001", "change_type": "unknown", "payload_json": {}},
        {"id": 3, "item_key": "ITEM0001", "change_type": "add_note", "payload_json": {"note_html": "<p>three</p>"}},
    ])
    assert result["applied_ids"] == [1, 3]
    assert len(result["failed"]) == 1 and result["failed"][0]["id"] == 2
    assert len(reader.get_item_notes("ITEM0001")) == 2
    assert result["failed"][0]["expected_io"] is False


def test_native_user_note_missing_marker_fails_before_effects(real_boundary):
    path, db, _, writer, _ = real_boundary
    before, target = native(db), writer.target_identity()
    assert before["notes"] == [] and sql_rows(path, "SELECT * FROM pending_changes") == []
    backup = writer.backup_database(expected_target=target)
    assert writer._prepared_backup[0] == backup and writer._prepared_backup[2] == target
    # Raw adapter input ID only; no admitted application receipt is seeded.
    result = writer.apply_changes([{
        "id": 213, "item_key": "ITEM0001", "change_type": "upsert_note", "payload_json": {
            "marker": pending.USER_NOTE_MARKER, "note_title": "My notes",
            "note_html": "<p>missing ownership footer</p>",
        },
    }], False, precreated_backup=backup, expected_target=target)
    assert result["applied_ids"] == [] and result["backup_path"] == backup
    failure, = result["failed"]
    assert type(failure["id"]) is int and failure["id"] == 213
    assert failure["expected_io"] is False
    assert failure["error"] == "Upsert note requires a marker present in its HTML"
    assert writer._prepared_backup is None
    assert native(db) == before and native(backup) == before
    assert sql_rows(backup, "PRAGMA integrity_check") == [("ok",)]
    assert [str(file) for file in db.parent.glob(writer._BACKUP_GLOB)] == [backup]
    assert sql_rows(path, "SELECT * FROM pending_changes") == []
    _assert_durable_pending(real_boundary)
