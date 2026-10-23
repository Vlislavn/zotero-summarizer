"""Owned resources and delegating observations, never a mirror/materialization engine."""
from copy import deepcopy
from dataclasses import replace
from functools import wraps
from html import escape
import json
from pathlib import Path

from tests._direct_write_fixtures import native, sql_rows
from tests._zotero_fixtures import add_library_item, build_zotero_db, mark_trashed
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.runtime import get_context
from zotero_summarizer.storage import review_note_projection

MARKER = "zs:note_type=user_note"


def expected_html(body):
    """Independent literal protocol oracle; never called to deliver a note."""
    paragraphs = "".join(f"<p>{escape(part.strip())}</p>" for part in body.split("\n\n") if part.strip()) or "<p></p>"
    return (f"<!-- {MARKER};version=1 --><h2>📝 My notes</h2>{paragraphs}"
            f"<p><em>{MARKER};version=1</em></p>")


def payload(change):
    value = change["payload_json"]
    return json.loads(value) if isinstance(value, str) else value


def selected(change):
    return change["change_type"] == "upsert_note" and payload(change).get("marker") == MARKER


class CompositeNotes:
    def __init__(self, settings, directory, monkeypatch):
        self.app = settings.triage_db_path
        self.db = build_zotero_db(directory)
        for key in ("P1", "P2"):
            add_library_item(self.db, item_key=key, title=f"Owned {key}")
        context = get_context()
        assert context.settings is settings
        monkeypatch.setattr(context, "settings", replace(settings, zotero_data_dir=self.db.parent))
        self.writer, self.reader = ZoteroWriter(self.db.parent), ZoteroReader(self.db.parent)
        self.target = self.writer.target_identity()
        monkeypatch.setattr(context.state, "zotero_writer", self.writer)
        monkeypatch.setattr(context.state, "zotero_reader", self.reader)
        self.writes, self.attempts, self.dispatches = [], [], []
        self.backups, self.creations, self.materializations, self.ack_views = [], [], [], []
        self.entry_hook = self.dispatch_hook = None
        self.quiet_native = native(self.db)
        self._install(monkeypatch)

    def _install(self, monkeypatch):
        real_backup, real_apply = ZoteroWriter.backup_database, ZoteroWriter.apply_changes
        real_dispatch = ZoteroWriter._dispatch_change
        real_materialize = ZoteroWriter.apply_feed_materialization
        real_ack = review_note_projection.acknowledge

        def backup(writer, *, expected_target=None):
            return self._backup(real_backup, writer, expected_target)

        def apply(writer, changes, create_backup=True, *, precreated_backup=None, expected_target=None):
            return self._apply(real_apply, writer, changes, create_backup,
                               precreated_backup=precreated_backup, expected_target=expected_target)

        def dispatch(writer, conn, change, cols):
            return self._dispatch(real_dispatch, writer, conn, change, cols)

        @wraps(real_materialize)
        def materialize(writer, **kwargs):
            return self._materialize(real_materialize, writer, kwargs)

        def acknowledge(conn, desired):
            return self._acknowledge(real_ack, conn, desired)

        monkeypatch.setattr(ZoteroWriter, "is_connector_running", lambda writer: self._closed(writer))
        monkeypatch.setattr(ZoteroWriter, "backup_database", backup)
        monkeypatch.setattr(ZoteroWriter, "apply_changes", apply)
        monkeypatch.setattr(ZoteroWriter, "_dispatch_change", dispatch)
        monkeypatch.setattr(ZoteroWriter, "apply_feed_materialization", materialize)
        monkeypatch.setattr(review_note_projection, "acknowledge", acknowledge)

    def _owned(self, writer):
        assert writer.db_path.resolve(strict=True) == self.db.resolve(strict=True)
        assert writer.target_identity() == self.target

    def _closed(self, writer):
        self._owned(writer)
        return False

    def _backup(self, real, writer, target):
        self._owned(writer)
        before = native(self.db)
        reference = real(writer) if target is None else real(writer, expected_target=target)
        assert Path(reference).parent == self.db.parent and Path(reference).is_file()
        self.backups.append({"path": reference, "target": target, "before": before, "writer": writer})
        return reference

    def history(self):
        columns = [row[1] for row in sql_rows(self.app, "PRAGMA table_info(pending_changes)")]
        return [dict(zip(columns, row)) for row in sql_rows(self.app, "SELECT * FROM pending_changes ORDER BY id")]

    def notes(self, key):
        return sql_rows(self.db,
            "SELECT n.itemID,n.note,i.version,p.version,i.itemTypeID,i.libraryID FROM itemNotes n "
            "JOIN items i ON i.itemID=n.itemID JOIN items p ON p.itemID=n.parentItemID "
            "WHERE p.key=? AND n.title='My notes' AND i.itemID NOT IN (SELECT itemID FROM deletedItems) "
            "ORDER BY n.itemID", (key,))

    def _entry(self, writer, changes, reference, target):
        change, = changes
        assert selected(change) and type(change["id"]) is int and change["id"] > 0
        row = next(row for row in self.history() if row["id"] == change["id"])
        assert row == change and row["status"] == "applying"
        origin, = json.loads(row["audit_json"])
        assert (origin["event"], origin["actor"], origin["source"]) == (
            "mirror_requested", "system", "mirror_user_note")
        assert origin["target"] == target.model_dump() == self.target.model_dump()
        assert origin["backup_path"] == row["backup_path"] == reference
        assert origin["recovery"] == "backup_only"
        prepared = writer._prepared_backup
        assert prepared is not None and prepared[0] == reference and prepared[2] == target
        backup, = [entry for entry in self.backups if entry["path"] == reference]
        assert backup["target"] == target
        attempt = {"row": deepcopy(row), "payload": deepcopy(payload(change)), "origin": origin,
                   "before": native(self.db), "notes_before": self.notes(row["item_key"]),
                   "result": None, "error": None, "dispatched": [], "writer": writer}
        self.attempts.append(attempt)
        self.writes.append((row["item_key"], origin["requested"]["body"]))
        return attempt

    def _apply(self, real, writer, changes, create_backup=True, *, precreated_backup=None, expected_target=None):
        self._owned(writer)
        if not any(selected(change) for change in changes):
            return real(writer, changes, create_backup, precreated_backup=precreated_backup,
                        expected_target=expected_target)
        assert create_backup is False
        attempt = self._entry(writer, changes, precreated_backup, expected_target)
        try:
            if self.entry_hook is not None:
                self.entry_hook(*self.writes[-1])
            result = real(writer, changes, create_backup, precreated_backup=precreated_backup,
                          expected_target=expected_target)
            attempt["result"] = deepcopy(result)
            assert writer._prepared_backup is None and result["backup_path"] == precreated_backup
            return result
        except Exception as exc:
            attempt["error"] = exc
            raise
        finally:
            attempt["after"] = native(self.db)
            attempt["notes_after"] = self.notes(attempt["row"]["item_key"])

    def _dispatch(self, real, writer, conn, change, cols):
        self._owned(writer)
        if selected(change):
            attempt = next(entry for entry in reversed(self.attempts) if entry["row"]["id"] == change["id"])
            assert payload(change) == attempt["payload"]
            attempt["dispatched"].append(deepcopy(change))
            self.dispatches.append((writer, deepcopy(change)))
            if self.dispatch_hook is not None:
                self.dispatch_hook(change["item_key"], attempt["origin"]["requested"]["body"])
        return real(writer, conn, change, cols)

    def _acknowledge(self, real, conn, desired):
        matching = [row for row in self.history() if row["item_key"] == desired["target_key"] and
                    all(json.loads(row["audit_json"])[0]["requested"][field] == desired[field]
                        for field in ("revision", "body", "target_key"))]
        row, = matching
        assert row["status"] == "applied"
        assert json.loads(row["audit_json"])[0]["target"] == self.target.model_dump()
        assert [event["event"] for event in json.loads(row["audit_json"])] == ["mirror_requested", "mirror_applied"]
        self.ack_views.append(deepcopy(row))
        return real(conn, desired)

    def _materialize(self, real, writer, kwargs):
        self._owned(writer)
        key = kwargs["new_item_key"]
        assert sql_rows(self.app, "SELECT planned_zotero_key FROM processed_feed_items WHERE planned_zotero_key=?",
                        (key,)) == [(key,)]
        before = self.item(key)
        result = real(writer, **kwargs)
        assert result["item_key"] == key and self.item(key)
        if not before:
            assert "create_item" in result["applied_steps"]
            self.creations.append(key)
        self.materializations.append({"writer": writer, "input": deepcopy(kwargs), "result": deepcopy(result)})
        return result

    def item(self, key):
        return sql_rows(self.db,
            "SELECT i.itemID,i.key,i.libraryID,i.itemTypeID FROM items i JOIN libraries l USING(libraryID) "
            "WHERE i.key=? AND l.type='user' AND i.itemTypeID=22 "
            "AND i.itemID NOT IN (SELECT itemID FROM deletedItems)", (key,))

    def make_unavailable(self):
        for key in ("P1", "P2"):
            item, = self.item(key)
            mark_trashed(self.db, item_id=item[0])
            assert self.reader.get_item_detail(key) is None
        self.quiet_native = native(self.db)

    def assert_quiet(self):
        assert self.writes == self.attempts == self.dispatches == self.backups == self.history() == []
        assert self.creations == self.materializations == self.ack_views == []
        assert native(self.db) == self.quiet_native

    def _assert_backup(self, reference, before):
        assert Path(reference).is_file() and native(reference) == before
        assert sql_rows(reference, "PRAGMA integrity_check") == [("ok",)]
        recorded, = [entry for entry in self.backups if entry["path"] == reference]
        assert recorded["before"] == before

    def assert_attempts(self, expected):
        assert self.writes == expected and len(self.attempts) == len(expected)
        assert len(self.backups) == len(self.attempts) + len(self.materializations)
        rows = self.history()
        assert len(rows) == len(expected)
        for entry, (key, body) in zip(self.attempts, expected):
            row, = [row for row in rows if row["id"] == entry["row"]["id"]]
            assert row["item_key"] == key
            assert entry["payload"] == {"marker": MARKER, "note_title": "My notes", "note_html": expected_html(body)}
            self._assert_backup(row["backup_path"], entry["before"])
            self._assert_outcome(entry, row, body)
        assert len({row["id"] for row in rows}) == len(rows)

    def _assert_outcome(self, entry, row, body):
        events = json.loads(row["audit_json"])
        if entry["error"] is not None:
            assert entry["result"] is None and entry["dispatched"] == []
            assert row["status"] == "applying" and row["applied_at"] is None
            assert [event["event"] for event in events] == ["mirror_requested"]
            assert entry["after"] == entry["before"]
        elif entry["result"]["failed"]:
            failed, = entry["result"]["failed"]
            assert failed["id"] == row["id"] and failed["expected_io"] is True
            assert entry["result"]["applied_ids"] == [] and len(entry["dispatched"]) == 1
            assert row["status"] == "failed" and row["applied_at"] is None
            assert [event["event"] for event in events] == ["mirror_requested", "mirror_failed"]
            assert events[-1]["expected_io"] is True and entry["after"] == entry["before"]
        else:
            assert entry["result"]["applied_ids"] == [row["id"]] and len(entry["dispatched"]) == 1
            assert row["status"] == "applied" and row["applied_at"]
            assert [event["event"] for event in events] == ["mirror_requested", "mirror_applied", "mirror_acknowledged"]
            self._assert_native(entry, body)
        if row["status"] != "applied":
            assert self.ack_views == []
            acknowledgements = sql_rows(self.app,
                "SELECT acknowledged FROM review_note_mirror_intents WHERE target_key=?", (row["item_key"],))
            assert acknowledgements and all(value == (0,) for value in acknowledgements)

    def _assert_native(self, entry, body):
        note, = entry["notes_after"]
        assert note[1] == f'<div class="zotero-note znv1">{expected_html(body)}</div>'
        assert note[4:] == (28, self.target.library_id)
        previous = entry["notes_before"]
        assert note[2] == (previous[0][2] + 1 if previous else 1)
        if previous:
            assert note[0] == previous[0][0]
        parent_before, = [clock for clock in entry["before"]["clocks"] if clock[0] == entry["row"]["item_key"]]
        assert note[3] == parent_before[1] + 1

    def assert_created(self, key):
        record, = [record for record in self.materializations if record["result"]["item_key"] == key]
        assert self.item(key) and record["result"]["backup_path"]
        backup, = [entry for entry in self.backups if entry["path"] == record["result"]["backup_path"]]
        self._assert_backup(backup["path"], backup["before"])
        assert sql_rows(self.app, "SELECT materialized_zotero_key,planned_zotero_key,zotero_sync_status,decision "
                        "FROM processed_feed_items WHERE planned_zotero_key=?", (key,)) == [(key, key, "synced", "selected")]
        assert (record["input"]["feed_payload"]["title"],) in sql_rows(self.db,
            "SELECT v.value FROM itemData d JOIN itemDataValues v USING(valueID) JOIN fields f USING(fieldID) "
            "JOIN items i USING(itemID) WHERE i.key=? AND f.fieldName='title'", (key,))
        assert (record["input"]["inbox_collection_name"],) in sql_rows(self.db,
            "SELECT c.collectionName FROM collectionItems ci JOIN collections c USING(collectionID) "
            "JOIN items i USING(itemID) WHERE i.key=? AND c.libraryID=?", (key, self.target.library_id))
        triage, = sql_rows(self.db, "SELECT n.note FROM itemNotes n JOIN items p ON p.itemID=n.parentItemID "
                          "WHERE p.key=? AND n.title=?", (key, record["input"]["note_title"]))
        assert record["input"]["note_html"].strip() in triage[0]
        names = {row[0] for row in sql_rows(self.db, "SELECT t.name FROM itemTags it JOIN tags t USING(tagID) "
                                        "JOIN items i USING(itemID) WHERE i.key=?", (key,))}
        assert set(record["input"]["tags"]) | {record["input"]["provenance_tag"]} <= names

    def local(self):
        return {table: sql_rows(self.app, f"SELECT * FROM {table} ORDER BY 1")
                for table in ("review_questions", "review_notes", "feed_key_aliases")}

    def checkpoint(self):
        return {"native": native(self.db), "backups": self.backup_files(), "history": self.history(),
                "local": self.local(), "counts": (len(self.attempts), len(self.dispatches), len(self.backups))}

    def backup_files(self):
        return {str(file): file.read_bytes() for file in self.db.parent.glob("zotero.sqlite.backup_*")}

    def assert_ack_retry(self, before):
        assert native(self.db) == before["native"] and self.backup_files() == before["backups"]
        assert self.local() == before["local"]
        assert (len(self.attempts), len(self.dispatches), len(self.backups)) == before["counts"] == (1, 1, 1)
        old, = before["history"]
        new, = self.history()
        assert old["id"] > 0 and old["status"] == new["status"] == "applied"
        assert {key: value for key, value in old.items() if key != "audit_json"} == {
            key: value for key, value in new.items() if key != "audit_json"}
        prefix, audit = json.loads(old["audit_json"]), json.loads(new["audit_json"])
        assert [event["event"] for event in prefix] == ["mirror_requested", "mirror_applied"]
        assert audit[:-1] == prefix and audit[-1]["event"] == "mirror_acknowledged"
        assert (audit[-1]["actor"], audit[-1]["source"]) == ("system", "mirror_user_note")
        assert self.ack_views == [old, old]

    def assert_parent_change(self, expected, local_before):
        self.assert_attempts(expected)
        assert self.creations == self.materializations == [] and self.local() == local_before
        assert len(self.history()) == 2 and self.history()[0]["id"] != self.history()[1]["id"]
        assert [row["item_key"] for row in self.history()] == ["P1", "P2"]
        for key, body in expected:
            note, = self.notes(key)
            assert note[1] == f'<div class="zotero-note znv1">{expected_html(body)}</div>'
        assert self.reader.db_path.resolve() == self.writer.db_path.resolve() == self.db.resolve()
