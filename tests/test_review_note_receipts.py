"""Three authored-only composite receipt regressions over owned real SQLite/native IO."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests._direct_write_fixtures import native, sql_rows
from tests.test_review_note_real_boundary import real_boundary  # noqa: F401
from zotero_summarizer.runtime import get_context
from zotero_summarizer.services.golden import verdict_effects
from zotero_summarizer.services.library import review_questions
from zotero_summarizer.storage import review_note_projection

pytestmark = pytest.mark.parametrize("real_boundary", [False], indirect=True)

_SUBMISSION = "20700000-0000-4000-8000-000000000001"
_BODY = "Manual & note\n\nSaved questions:\n1. Question <one>?"
_HTML = (
    "<!-- zs:note_type=user_note;version=1 --><h2>📝 My notes</h2>"
    "<p>Manual &amp; note</p><p>Saved questions:\n1. Question &lt;one&gt;?</p>"
    "<p><em>zs:note_type=user_note;version=1</em></p>"
)
_PAYLOAD = {"note_html": _HTML, "marker": "zs:note_type=user_note", "note_title": "My notes"}


@pytest.fixture
def receipt_boundary(real_boundary, monkeypatch):
    path, db, reader, writer, _unused_random_payload = real_boundary
    state = get_context().state
    monkeypatch.setattr(state, "zotero_reader", reader)
    monkeypatch.setattr(state, "zotero_writer", writer)
    saved = review_questions.save_question({
        "submission_id": _SUBMISSION, "item_key": "ITEM0001",
        "question": "Question <one>?", "origin": "typed", "preset_id": None,
    })
    revision = sql_rows(path, "SELECT MAX(revision) FROM sync_changes "
                        "WHERE item_key='ITEM0001' AND field IN ('review_note','review_question')")[0][0]
    assert type(revision) is int and revision > 0
    apply = Mock(wraps=writer.apply_changes)
    monkeypatch.setattr(writer, "apply_changes", apply)
    boundary = SimpleNamespace(
        app=path, db=db, writer=writer, apply=apply, revision=revision,
        target=writer.target_identity(), before=native(db), submitted_at=saved["submitted_at"],
        parent=sql_rows(db, "SELECT itemID FROM items WHERE key='ITEM0001'")[0][0],
    )
    _assert_local(boundary, acknowledged=0)
    return boundary


def _history(path):
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute("SELECT * FROM pending_changes ORDER BY id")]


def _assert_local(b, *, acknowledged):
    assert sql_rows(b.app, "SELECT item_key,note FROM review_notes") == [("ITEM0001", "Manual & note")]
    assert sql_rows(b.app, "SELECT submission_id,item_key,question,origin,preset_id,submitted_at "
                    "FROM review_questions") == [
        (_SUBMISSION, "ITEM0001", "Question <one>?", "typed", None, b.submitted_at),
    ]
    assert sql_rows(b.app, "SELECT item_key,revision,body,target_key,acknowledged "
                    "FROM review_note_mirror_intents") == [
        ("ITEM0001", b.revision, _BODY, "ITEM0001", acknowledged),
    ]


def _assert_receipt(b, status, events):
    rows = _history(b.app)
    assert len(rows) == 1, "native delivery requires one independently visible mirror declaration"
    row = rows[0]
    assert type(row["id"]) is int and row["id"] > 0
    assert (row["item_key"], row["change_type"]) == ("ITEM0001", "upsert_note")
    assert json.loads(row["payload_json"]) == _PAYLOAD
    assert row["proposal_json"] is None and row["status"] == status
    assert row["error_message"] == ""
    audit = json.loads(row["audit_json"])
    assert [event["event"] for event in audit] == events
    assert all((event["actor"], event["source"]) == ("system", "mirror_user_note") for event in audit)
    assert all(isinstance(event["at"], str) and event["at"] for event in audit)
    assert [event["at"] for event in audit] == sorted(event["at"] for event in audit)
    first = audit[0]
    assert first["requested"] == {
        "lookup_item_key": "ITEM0001", "revision": b.revision,
        "body": _BODY, "target_key": "ITEM0001",
    }
    assert first["target"] == b.target.model_dump()
    assert first["recovery"] == "backup_only"
    assert "preview_sha256" not in first and "inverse" not in first
    assert isinstance(row["backup_path"], str) and row["backup_path"]
    backup = Path(row["backup_path"])
    assert backup.is_absolute() and backup.parent == b.db.parent and backup.is_file()
    assert first["backup_path"] == str(backup)
    assert sorted(b.db.parent.glob("zotero.sqlite.backup_*")) == [backup]
    if status == "applying":
        assert row["applied_at"] is None
    else:
        assert status == "applied" and isinstance(row["applied_at"], str) and row["applied_at"]
    return row


def _assert_native_note(b):
    after = native(b.db)
    assert len(after["notes"]) == 1
    child, parent, title, html = after["notes"][0]
    assert type(child) is int and child > 0 and child != b.parent
    assert (parent, title, html) == (b.parent, "My notes", '<div class="zotero-note znv1">' + _HTML + '</div>')
    assert sql_rows(b.db, "SELECT version,synced,libraryID FROM items WHERE itemID=?", (child,)) == [(1, 0, 1)]
    before_clock = next(row for row in b.before["clocks"] if row[0] == "ITEM0001")
    after_clock = next(row for row in after["clocks"] if row[0] == "ITEM0001")
    assert after_clock[1:3] == (before_clock[1] + 1, 0)
    assert all(isinstance(value, str) and value for value in after_clock[3:])
    assert {key: value for key, value in after.items() if key not in {"notes", "clocks"}} == {
        key: value for key, value in b.before.items() if key not in {"notes", "clocks"}
    }
    return after


def test_composite_native_success_has_positive_mirror_history(receipt_boundary):
    b = receipt_boundary

    result = verdict_effects.mirror_review_note(b.app, "ITEM0001")

    assert result == {"note_written": True, "note_error": None, "note_pending": False,
                      "projection_revision": b.revision, "target_key": "ITEM0001"}
    b.apply.assert_called_once()
    _assert_native_note(b)
    _assert_local(b, acknowledged=1)
    row = _assert_receipt(b, "applied", ["mirror_requested", "mirror_applied", "mirror_acknowledged"])
    changes = b.apply.call_args.args[0]
    assert len(changes) == 1 and changes[0]["id"] == row["id"]
    backup = Path(row["backup_path"])
    assert sql_rows(backup, "PRAGMA integrity_check") == [("ok",)]
    assert native(backup) == b.before


def test_composite_applying_claim_is_visible_before_native_dispatch(receipt_boundary, monkeypatch):
    b = receipt_boundary
    delegate = b.writer.apply_changes
    consume = Mock(wraps=b.writer._take_prepared_backup)
    monkeypatch.setattr(b.writer, "_take_prepared_backup", consume)
    observed = []

    def observe(changes, create_backup=True, **kwargs):
        row = _assert_receipt(b, "applying", ["mirror_requested"])
        assert len(changes) == 1 and changes[0]["id"] == row["id"]
        assert (changes[0]["item_key"], changes[0]["change_type"]) == ("ITEM0001", "upsert_note")
        payload = changes[0]["payload_json"]
        assert (json.loads(payload) if isinstance(payload, str) else payload) == _PAYLOAD
        assert create_backup is False
        assert kwargs["precreated_backup"] == row["backup_path"]
        assert kwargs["expected_target"] == b.target
        assert consume.call_count == 0
        _assert_local(b, acknowledged=0)
        assert native(b.db) == b.before
        observed.append((row["id"], row["backup_path"]))
        return delegate(changes, create_backup, **kwargs)

    monkeypatch.setattr(b.writer, "apply_changes", observe)

    result = verdict_effects.mirror_review_note(b.app, "ITEM0001")

    assert result["note_written"] is True and result["note_pending"] is False
    row = _assert_receipt(b, "applied", ["mirror_requested", "mirror_applied", "mirror_acknowledged"])
    assert observed == [(row["id"], row["backup_path"])]
    b.apply.assert_called_once()
    consume.assert_called_once_with(row["backup_path"], b.target)
    assert b.writer._prepared_backup is None
    _assert_native_note(b)
    _assert_local(b, acknowledged=1)


def test_composite_ack_failure_retries_ack_without_native_redelivery(receipt_boundary, monkeypatch):
    b = receipt_boundary
    acknowledge = review_note_projection.acknowledge
    before_ack = []

    def observe_ack(conn, desired):
        before_ack.append(_history(b.app))
        return acknowledge(conn, desired)

    monkeypatch.setattr(review_note_projection, "acknowledge", observe_ack)
    with closing(sqlite3.connect(b.app)) as conn, conn:
        conn.execute("CREATE TRIGGER reject_composite_ack BEFORE UPDATE OF acknowledged "
                     "ON review_note_mirror_intents WHEN NEW.acknowledged=1 "
                     "BEGIN SELECT RAISE(ABORT, 'owned ACK refusal'); END")

    first = verdict_effects.mirror_review_note(b.app, "ITEM0001")

    assert first == {"note_written": True, "note_error": "IntegrityError", "note_pending": True,
                     "projection_revision": b.revision, "target_key": "ITEM0001"}
    b.apply.assert_called_once()
    native_after_first = _assert_native_note(b)
    _assert_local(b, acknowledged=0)
    history_after_first = _history(b.app)
    backups_after_first = sorted(b.db.parent.glob("zotero.sqlite.backup_*"))
    assert len(backups_after_first) == 1
    with closing(sqlite3.connect(b.app)) as conn, conn:
        conn.execute("DROP TRIGGER reject_composite_ack")

    second = verdict_effects.mirror_review_note(b.app, "ITEM0001")
    native_after_second = native(b.db)

    assert b.apply.call_count == 1, "ACK-only continuation must not dispatch native twice"
    assert second == {"note_written": False, "note_error": None, "note_pending": False,
                      "projection_revision": b.revision, "target_key": "ITEM0001"}
    assert native_after_second == native_after_first
    assert sorted(b.db.parent.glob("zotero.sqlite.backup_*")) == backups_after_first
    assert len(history_after_first) == 1 and history_after_first[0]["status"] == "applied"
    before_audit = json.loads(history_after_first[0]["audit_json"])
    assert [event["event"] for event in before_audit] == ["mirror_requested", "mirror_applied"]
    assert before_ack == [history_after_first, history_after_first]
    row = _assert_receipt(b, "applied", ["mirror_requested", "mirror_applied", "mirror_acknowledged"])
    assert json.loads(row["audit_json"])[:2] == before_audit
    assert {key: value for key, value in row.items() if key != "audit_json"} == {
        key: value for key, value in history_after_first[0].items() if key != "audit_json"
    }
    assert Path(row["backup_path"]) == backups_after_first[0]
    assert sql_rows(backups_after_first[0], "PRAGMA integrity_check") == [("ok",)]
    assert native(backups_after_first[0]) == b.before
    _assert_local(b, acknowledged=1)
