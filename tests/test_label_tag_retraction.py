"""A060: the shared Zotero label mirror must represent an absent verdict."""

import sqlite3

import pytest

from tests._zotero_fixtures import add_library_item, add_tag_to_item, build_zotero_db
from tests.test_review_questions import _database
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter, ZoteroWriteError
from zotero_summarizer.services.zotero import zotero
from zotero_summarizer.services.zotero.pending import build_label_tag_change
from zotero_summarizer.services.golden.verdict_effects import mirror_current_verdict
from zotero_summarizer.storage import repositories


def _library(tmp_path, monkeypatch, tags):
    settings = _database(tmp_path)
    repositories.insert_or_update_label_verdict(settings.triage_db_path, item_key="PARENT",
        original_derived_priority="unknown", user_priority="must_read", comment="")
    repositories.delete_label_verdict(settings.triage_db_path, "PARENT")
    db = build_zotero_db(tmp_path / "zotero")
    item = add_library_item(db, item_key="PARENT", title="Parent")
    for tag in tags:
        add_tag_to_item(db, item_id=item, tag_name=tag)
    reader, writer = ZoteroReader(db.parent), ZoteroWriter(db.parent)
    monkeypatch.setattr(zotero, "get_zotero_reader_or_raise", lambda: reader)
    monkeypatch.setattr(zotero, "get_zotero_writer_or_raise", lambda: writer)
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)
    return db, reader, writer, settings.triage_db_path


@pytest.mark.parametrize("labels", [
    ["label:must_read"],
    ["label:should_read", "label:could_read", "label:dont_read"],
    ["Label:Must_Read"],
    ["label:must_read", "Label:Must_Read"],
    [],
])
def test_clear_label_preserves_other_tags_and_is_idempotent(tmp_path, monkeypatch, labels):
    unrelated = {"topic:x", "🧠", "zs:rel/must_read", "label:custom"}
    db, reader, writer, queue_db = _library(tmp_path, monkeypatch, [*labels, *sorted(unrelated)])
    other = add_library_item(db, item_key="OTHER", title="Other")
    for tag in labels:
        add_tag_to_item(db, item_id=other, tag_name=tag)

    mirror_current_verdict(queue_db, "PARENT")

    assert set(reader.get_item_detail("PARENT")["tags"]) == unrelated
    assert set(reader.get_item_detail("OTHER")["tags"]) == set(labels)
    backups = list(db.parent.glob(writer._BACKUP_GLOB))
    assert len(backups) == bool(labels)
    if labels:
        with sqlite3.connect(backups[0]) as conn:
            assert conn.execute("SELECT COUNT(*) FROM itemTags").fetchone()[0] == 2 * len(labels) + len(unrelated)
    mirror_current_verdict(queue_db, "PARENT")
    assert list(db.parent.glob(writer._BACKUP_GLOB)) == backups
    assert set(reader.get_item_detail("PARENT")["tags"]) == unrelated


@pytest.mark.parametrize("failure", ["connector", "backup", "write"])
def test_failed_clear_preserves_label_and_propagates(tmp_path, monkeypatch, failure):
    db, reader, writer, queue_db = _library(tmp_path, monkeypatch, ["label:must_read"])
    if failure == "connector":
        monkeypatch.setattr(writer, "is_connector_running", lambda: True)
    elif failure == "backup":
        def fail_backup(**kwargs):
            raise ZoteroWriteError("backup unavailable")
        monkeypatch.setattr(writer, "backup_database", fail_backup)
    else:
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TRIGGER refuse_tag_delete BEFORE DELETE ON itemTags BEGIN SELECT RAISE(ABORT, 'write refused'); END")

    with pytest.raises(ZoteroWriteError, match="Zotero is open|backup unavailable|write refused"):
        mirror_current_verdict(queue_db, "PARENT")

    assert reader.get_item_detail("PARENT")["tags"] == ["label:must_read"]
    assert bool(list(db.parent.glob(writer._BACKUP_GLOB))) == (failure == "write")


@pytest.mark.parametrize("priority", ["", "bogus"])
def test_only_none_clears_the_label_not_invalid_priorities(tmp_path, monkeypatch, priority):
    db, reader, writer, _ = _library(tmp_path, monkeypatch, ["label:must_read"])

    with pytest.raises(ValueError, match="unknown reading priority"):
        build_label_tag_change(reader.get_item_detail("PARENT")["tags"], priority)

    assert reader.get_item_detail("PARENT")["tags"] == ["label:must_read"]
    assert list(db.parent.glob(writer._BACKUP_GLOB)) == []
