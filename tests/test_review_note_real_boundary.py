"""Synthetic real producer/wrapper regressions: IO is optional, defects are not."""
from contextlib import closing
import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests._zotero_fixtures import add_library_item, build_zotero_db
from tests.test_review_questions import _app, _database
from zotero_summarizer.integrations.zotero_read import ZoteroReader, ZoteroReadError
from zotero_summarizer.integrations.zotero_write import ZoteroWriter, ZoteroWriteError
from zotero_summarizer.services.golden import verdict_effects
from zotero_summarizer.services.library import review_questions
from zotero_summarizer.services.zotero import pending, zotero
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
    monkeypatch.setattr(verdict_effects, "get_zotero_reader_or_raise", lambda: reader)
    monkeypatch.setattr(zotero, "get_zotero_writer_or_raise", lambda: writer)
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
    path, db, _, _, _ = real_boundary
    monkeypatch.setattr(pending, "build_user_note_html", lambda _: "<p>missing ownership footer</p>")
    try:
        with pytest.raises(ZoteroWriteError) as raised:
            verdict_effects.mirror_review_note(path, "ITEM0001")
    finally:
        _assert_durable_pending(real_boundary)
    assert raised.value.expected_io is False
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM itemNotes").fetchone() == (0,)


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

                def backup_then_lock():
                    result = backup()
                    locked.execute("BEGIN IMMEDIATE")
                    return result
                monkeypatch.setattr(writer, "backup_database", backup_then_lock)
        receipt = verdict_effects.mirror_review_note(path, "ITEM0001")
    assert receipt == {"note_written": False, "note_error": "ZoteroWriteError", "note_pending": True,
                       "projection_revision": revision, "target_key": "ITEM0001"}
    _assert_durable_pending(real_boundary)
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM itemNotes").fetchone() == (0,)


def test_real_reader_cannot_open_io_remains_optional(real_boundary):
    path, db, _, writer, _ = real_boundary
    revision = _staged_revision(path)
    db.rename(db.with_suffix(".unavailable"))

    receipt = verdict_effects.mirror_review_note(path, "ITEM0001")

    assert receipt == {"note_written": False, "note_error": "ZoteroReadError", "note_pending": True,
                       "projection_revision": revision, "target_key": "ITEM0001"}
    _assert_durable_pending(real_boundary)
    assert list(db.parent.glob(writer._BACKUP_GLOB)) == []


@pytest.mark.parametrize("metadata", [None, False, "true"])
def test_unknown_or_malformed_failure_record_fails_closed(real_boundary, monkeypatch, metadata):
    path, _, _, writer, _ = real_boundary
    failure = {"id": 0, "error": "unclassified producer failure"}
    if metadata is not None:
        failure["expected_io"] = metadata
    monkeypatch.setattr(writer, "apply_changes", lambda *_a: {"failed": [failure]})
    with pytest.raises(ZoteroWriteError) as raised:
        verdict_effects.mirror_review_note(path, "ITEM0001")
    assert raised.value.expected_io is False
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
