"""Proposed verdict/label receipt predicates; source-only, runtime RED/GREEN UNRUN.

Raw committed action rows do not establish closed system-history classification,
Pending approval, native-target consent, crash safety or whole-issue acceptance.
"""
from copy import deepcopy
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from tests._direct_write_fixtures import native, sql_rows
from tests._zotero_fixtures import (
    add_collection_link, add_library_item, add_tag_to_item, build_zotero_db,
)
from tests.test_review_questions import _database
from zotero_summarizer.domain import LABEL_TAG_PREFIX, VERDICT_SOURCE_USER
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.runtime import AppContext, RuntimeState, set_context
from zotero_summarizer.services.golden import verdict_effects
from zotero_summarizer.services.zotero import zotero
from zotero_summarizer.services.zotero._notes import VERDICT_NOTE_MARKER
from zotero_summarizer.storage import repositories

KEY = "V222AAAA"
OTHER = "V222BBBB"
COMMENT = "Human rationale <keep> & compare."
LABEL = f"{LABEL_TAG_PREFIX}must_read"
OLD_LABEL = f"{LABEL_TAG_PREFIX}could_read"
# Independent literal machine-I/O oracle, not the production formatter's output.
VERDICT_HTML = (
    f"<!-- {VERDICT_NOTE_MARKER};version=1 -->"
    "<h2>🔥 Must Read</h2><p>Human rationale &lt;keep&gt; &amp; compare.</p>"
    f"<p><em>{VERDICT_NOTE_MARKER};version=1</em></p>"
)
NATIVE_HTML = '<div class="zotero-note znv1">' + VERDICT_HTML + "</div>"
ACTIONS = {
    "upsert_note": {"note_html": VERDICT_HTML, "marker": VERDICT_NOTE_MARKER,
                    "note_title": "Verdict: must_read"},
    "tag_changes": {"add_tags": [LABEL], "remove_tags": [OLD_LABEL]},
}


def _history(path):
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=0)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute("SELECT * FROM pending_changes ORDER BY id")]


def _intent(path):
    return {
        "labels": sql_rows(path, "SELECT * FROM label_verdicts ORDER BY id"),
        "revisions": sql_rows(path, "SELECT * FROM sync_changes ORDER BY revision"),
        "deletions": sql_rows(path, "SELECT * FROM label_mirror_receipts ORDER BY revision"),
    }


def _payload(value):
    return json.loads(value) if isinstance(value, str) else value


@pytest.fixture
def verdict_resources(tmp_path, monkeypatch):
    settings = _database(tmp_path)
    db = build_zotero_db(settings.data_dir / "verdict-zotero")
    settings = replace(settings, zotero_data_dir=db.parent)
    parent = add_library_item(db, item_key=KEY, title="Owned verdict paper",
                              doi="10.222/owned", abstract="Preserve this metadata")
    other = add_library_item(db, item_key=OTHER, title="Unrelated native control")
    manual = add_library_item(db, item_key="V222NOTE", title="Independent manual note",
                              item_type="note")
    for item, tag, kind in [(parent, OLD_LABEL, 0), (parent, "topic:keep", 1),
                            (parent, "label:custom", 0), (other, OLD_LABEL, 0)]:
        add_tag_to_item(db, item_id=item, tag_name=tag, tag_type=kind)
    add_collection_link(db, item_id=parent, collection_id=90)
    add_collection_link(db, item_id=other, collection_id=91)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("INSERT INTO itemNotes(itemID,parentItemID,note,title) VALUES (?,?,?,?)",
                     (manual, parent, '<div class="zotero-note znv1"><p>Manual control</p></div>',
                      "Independent manual note"))
        conn.execute("UPDATE items SET version=17,synced=1,dateAdded='2002-01-01 01:02:03',"
                     "dateModified='2002-02-02 02:03:04',clientDateModified='2002-03-03 03:04:05'")
        conn.commit()
    reader = ZoteroReader(settings.zotero_data_dir)
    writer = ZoteroWriter(settings.zotero_data_dir)
    set_context(AppContext(settings=settings, state=RuntimeState(
        zotero_reader=reader, zotero_writer=writer,
    )))
    b = SimpleNamespace(db=db, queue_db=settings.triage_db_path, settings=settings,
                        parent=parent, manual=manual, reader=reader, writer=writer,
                        applies=[], dispatches=[],
                        backups=[], observation_errors=[])
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)  # Network isolation only.
    actual_apply, actual_dispatch = writer.apply_changes, writer._dispatch_change
    actual_backup = writer.backup_database

    def apply(changes, *args, **kwargs):
        entry = {"changes": deepcopy(changes), "args": args, "kwargs": kwargs}
        b.applies.append(entry)
        result = actual_apply(changes, *args, **kwargs)
        entry["result"] = deepcopy(result)
        return result

    def dispatch(conn, change, columns):
        entry = {"change": deepcopy(change), "completed_backups": [
            call["returned"] for call in b.backups if "returned" in call]}
        try:
            entry["committed_history"] = _history(b.queue_db)
        except sqlite3.Error as exc:
            # Observation must not prevent the old native effect; fail after it.
            entry["observation_error"] = repr(exc)
            b.observation_errors.append(repr(exc))
        b.dispatches.append(entry)
        return actual_dispatch(conn, change, columns)

    def backup(*args, **kwargs):
        entry = {"args": args, "kwargs": kwargs}
        b.backups.append(entry)
        reference = actual_backup(*args, **kwargs)
        entry["returned"] = reference
        return reference

    monkeypatch.setattr(writer, "apply_changes", apply)
    monkeypatch.setattr(writer, "_dispatch_change", dispatch)
    monkeypatch.setattr(writer, "backup_database", backup)
    with repositories.with_db_path(b.queue_db):
        yield b


def _assert_native_effect(b, before, after):
    assert b.reader.db_path.samefile(b.writer.db_path)
    assert b.writer.db_path.samefile(b.db)
    assert before["notes"] == [(
        b.manual, b.parent, "Independent manual note",
        '<div class="zotero-note znv1"><p>Manual control</p></div>',
    )]
    assert set(before["tags"]) == {
        (KEY, 1, OLD_LABEL, 0, 0), (KEY, 1, "topic:keep", 1, 1),
        (KEY, 1, "label:custom", 0, 0), (OTHER, 1, OLD_LABEL, 0, 0),
    }
    assert set(before["notes"]).issubset(after["notes"])
    added = [row for row in after["notes"] if row not in before["notes"]]
    assert len(added) == 1
    assert added[0][1:] == (b.parent, "Verdict: must_read", NATIVE_HTML)
    assert sql_rows(b.db,
        "SELECT parent.key,child.libraryID,t.typeName FROM itemNotes n "
        "JOIN items parent ON parent.itemID=n.parentItemID "
        "JOIN items child ON child.itemID=n.itemID "
        "JOIN itemTypes t ON t.itemTypeID=child.itemTypeID WHERE n.itemID=?",
        (added[0][0],)) == [(KEY, 1, "note")]
    assert set(after["tags"]) == (
        set(before["tags"]) - {(KEY, 1, OLD_LABEL, 0, 0)} | {(KEY, 1, LABEL, 0, 0)}
    )
    assert set(after["tag_records"]) == set(before["tag_records"]) | {(LABEL, 0)}
    for field in ("memberships", "fields", "deleted"):
        assert after[field] == before[field]
    for row in before["clocks"]:
        if row[0] != KEY:
            assert row in after["clocks"]


def _assert_backups(b, before):
    references = [call["returned"] for call in b.backups]
    assert references
    assert set(references) == {str(path) for path in b.db.parent.glob(b.writer._BACKUP_GLOB)}
    states = []
    for reference in references:
        path = Path(reference)
        assert path.parent == b.db.parent and not path.samefile(b.db)
        assert sql_rows(path, "PRAGMA integrity_check") == [("ok",)]
        states.append(native(path))
    assert any(state == before for state in states), "No actual backup retains native prestate"


def _assert_committed_receipts(b, before, after):
    assert b.observation_errors == []
    assert b.dispatches
    assert {entry["change"]["change_type"] for entry in b.dispatches} == set(ACTIONS)
    prior_ids = {row["id"] for row in before}
    terminal = {row["id"]: row for row in after}
    for entry in b.dispatches:
        change = entry["change"]
        identity = (change["item_key"], change["change_type"], _payload(change["payload_json"]))
        assert identity == (KEY, change["change_type"], ACTIONS[change["change_type"]])
        change_id = change["id"]
        # The proposed invariant is deliberately AFTER real note AND tag effects.
        assert type(change_id) is int and change_id > 0, (
            "Native verdict/label dispatch lacks a positive committed app action ID", entry, after
        )
        assert change_id not in prior_ids
        committed = {row["id"]: row for row in entry["committed_history"]}
        assert change_id in committed, ("Claim not independently committed before dispatch", entry)
        claim = committed[change_id]
        assert (claim["item_key"], claim["change_type"], _payload(claim["payload_json"])) == identity
        assert claim["status"] == "applying" and claim["applied_at"] is None
        assert isinstance(claim["backup_path"], str) and claim["backup_path"]
        assert claim["backup_path"] in entry["completed_backups"]
        assert change_id in terminal, ("No independently committed native terminal receipt", after)
        row = terminal[change_id]
        assert (row["item_key"], row["change_type"], _payload(row["payload_json"])) == identity
        assert row["status"] == "applied" and row["error_message"] == ""
        assert isinstance(row["applied_at"], str) and row["applied_at"]
        assert row["backup_path"] == claim["backup_path"]
        results = [call["result"] for call in b.applies if any(
            supplied["id"] == change_id and
            (supplied["item_key"], supplied["change_type"], _payload(supplied["payload_json"])) == identity
            for supplied in call["changes"])]
        assert results
        for result in results:
            assert change_id in result["applied_ids"] and result["failed"] == []
            assert result["backup_path"] == row["backup_path"]


def test_verdict_native_effect_has_committed_app_receipts(verdict_resources, record_property):
    b = verdict_resources
    repositories.insert_or_update_label_verdict(
        b.queue_db, item_key=KEY, original_derived_priority="could_read",
        user_priority="must_read", comment=COMMENT, source=VERDICT_SOURCE_USER,
    )
    intent_before, history_before, before = _intent(b.queue_db), _history(b.queue_db), native(b.db)

    result = verdict_effects.mirror_current_verdict(b.queue_db, KEY)

    after, intent_after, history_after = native(b.db), _intent(b.queue_db), _history(b.queue_db)
    record_property("verdict_receipt_observations", repr({
        "result": result, "before_native": before, "after_native": after,
        "intent_before": intent_before, "intent_after": intent_after,
        "history_before": history_before, "history_after": history_after,
        "applies": b.applies, "dispatches": b.dispatches, "backups": b.backups,
        "observation_errors": b.observation_errors,
    }))
    assert result == {"label_written": True, "note_written": True}
    _assert_native_effect(b, before, after)
    assert intent_after == intent_before
    assert sql_rows(b.queue_db,
        "SELECT item_key,user_priority,comment,source FROM label_verdicts WHERE item_key=?",
        (KEY,)) == [(KEY, "must_read", COMMENT, VERDICT_SOURCE_USER)]
    assert sql_rows(b.queue_db,
        "SELECT item_key,field,value,comment,source FROM sync_changes WHERE item_key=?",
        (KEY,)) == [(KEY, "verdict", "must_read", COMMENT, VERDICT_SOURCE_USER)]
    _assert_backups(b, before)
    _assert_committed_receipts(b, history_before, history_after)


def test_absent_verdict_has_no_native_effect(verdict_resources, monkeypatch, record_property):
    b = verdict_resources
    before, intent_before, history_before = native(b.db), _intent(b.queue_db), _history(b.queue_db)
    backups_before = set(b.db.parent.glob(b.writer._BACKUP_GLOB))
    lookups = []

    def forbidden_lookup(*args, **kwargs):
        lookups.append((args, kwargs))
        raise AssertionError("Absent verdict must not access native resources")

    for module, names in (
        (zotero, ("get_zotero_reader_or_raise", "get_zotero_writer_or_raise", "get_library_reader")),
        (verdict_effects, ("get_zotero_reader_or_raise", "get_library_reader")),
    ):
        for name in names:
            monkeypatch.setattr(module, name, forbidden_lookup)

    result = verdict_effects.mirror_current_verdict(b.queue_db, KEY)

    after, intent_after, history_after = native(b.db), _intent(b.queue_db), _history(b.queue_db)
    record_property("absent_verdict_observations", repr({
        "result": result, "before_native": before, "after_native": after,
        "intent_before": intent_before, "intent_after": intent_after,
        "history_before": history_before, "history_after": history_after,
        "applies": b.applies, "dispatches": b.dispatches, "backups": b.backups,
        "lookups": lookups,
    }))
    assert result is None
    assert intent_before["labels"] == intent_before["revisions"] == []
    assert after == before and intent_after == intent_before and history_after == history_before
    assert set(b.db.parent.glob(b.writer._BACKUP_GLOB)) == backups_before
    assert b.applies == b.dispatches == b.backups == lookups == []
    assert b.observation_errors == []
