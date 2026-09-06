"""A060: the shared Zotero label mirror must represent an absent verdict."""

import sqlite3

import pytest

from tests._zotero_fixtures import add_library_item, add_tag_to_item, build_zotero_db
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter, ZoteroWriteError
from zotero_summarizer.services.zotero import zotero


def _library(tmp_path, monkeypatch, tags):
    db = build_zotero_db(tmp_path / "zotero")
    item = add_library_item(db, item_key="PARENT", title="Parent")
    for tag in tags:
        add_tag_to_item(db, item_id=item, tag_name=tag)
    reader, writer = ZoteroReader(db.parent), ZoteroWriter(db.parent)
    monkeypatch.setattr(zotero, "get_zotero_reader_or_raise", lambda: reader)
    monkeypatch.setattr(zotero, "get_zotero_writer_or_raise", lambda: writer)
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)
    return db, reader, writer


@pytest.mark.parametrize("labels", [
    ["label:must_read"],
    ["label:should_read", "label:could_read", "label:dont_read"],
    ["Label:Must_Read"],
    ["label:must_read", "Label:Must_Read"],
    [],
])
def test_clear_label_preserves_other_tags_and_is_idempotent(tmp_path, monkeypatch, labels):
    unrelated = {"topic:x", "🧠", "zs:rel/must_read", "label:custom"}
    db, reader, writer = _library(tmp_path, monkeypatch, [*labels, *sorted(unrelated)])
    other = add_library_item(db, item_key="OTHER", title="Other")
    for tag in labels:
        add_tag_to_item(db, item_id=other, tag_name=tag)

    zotero.zotero_set_label_tag("PARENT", None)

    assert set(reader.get_item_detail("PARENT")["tags"]) == unrelated
    assert set(reader.get_item_detail("OTHER")["tags"]) == set(labels)
    backups = list(db.parent.glob(writer._BACKUP_GLOB))
    assert len(backups) == bool(labels)
    if labels:
        with sqlite3.connect(backups[0]) as conn:
            assert conn.execute("SELECT COUNT(*) FROM itemTags").fetchone()[0] == 2 * len(labels) + len(unrelated)
    zotero.zotero_set_label_tag("PARENT", None)
    assert list(db.parent.glob(writer._BACKUP_GLOB)) == backups
    assert set(reader.get_item_detail("PARENT")["tags"]) == unrelated


@pytest.mark.parametrize("failure", ["connector", "backup", "write"])
def test_failed_clear_preserves_label_and_propagates(tmp_path, monkeypatch, failure):
    db, reader, writer = _library(tmp_path, monkeypatch, ["label:must_read"])
    if failure == "connector":
        monkeypatch.setattr(writer, "is_connector_running", lambda: True)
    elif failure == "backup":
        def fail_backup():
            raise ZoteroWriteError("backup unavailable")
        monkeypatch.setattr(writer, "backup_database", fail_backup)
    else:
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TRIGGER refuse_tag_delete BEFORE DELETE ON itemTags BEGIN SELECT RAISE(ABORT, 'write refused'); END")

    with pytest.raises(ZoteroWriteError, match="Zotero is open|backup unavailable|write refused"):
        zotero.zotero_set_label_tag("PARENT", None)

    assert reader.get_item_detail("PARENT")["tags"] == ["label:must_read"]
    assert bool(list(db.parent.glob(writer._BACKUP_GLOB))) == (failure == "write")


@pytest.mark.parametrize("priority", ["", "bogus"])
def test_only_none_clears_the_label_not_invalid_priorities(tmp_path, monkeypatch, priority):
    db, reader, writer = _library(tmp_path, monkeypatch, ["label:must_read"])

    with pytest.raises(ValueError, match="unknown reading priority"):
        zotero.zotero_set_label_tag("PARENT", priority)

    assert reader.get_item_detail("PARENT")["tags"] == ["label:must_read"]
    assert list(db.parent.glob(writer._BACKUP_GLOB)) == []
