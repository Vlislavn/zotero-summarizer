"""A220: current verdict rationale wins across delayed delivery and UUID replay."""

import sqlite3

import pytest

from tests.test_offline_sync import _mutation
from tests.test_verdict_mirror_retraction import _setup
from zotero_summarizer.integrations.zotero_write import ZoteroWriteError
from zotero_summarizer.services.golden import verdict_effects
from zotero_summarizer.services.sync import service
from zotero_summarizer.storage import repositories as db


def _note(zdb):
    with sqlite3.connect(zdb) as conn:
        notes = conn.execute("SELECT note FROM itemNotes").fetchall()
    assert len(notes) == 1
    return notes[0][0]


@pytest.mark.parametrize("latest", ["new rationale", "", None])
def test_replayed_verdict_preserves_current_note(tmp_path, monkeypatch, latest):
    path, zdb, _, reader, _, _ = _setup(tmp_path, monkeypatch, "offline")
    monkeypatch.setattr(verdict_effects, "append_training_row", lambda *args: None)
    revision = db.sync_current_fields(path)[("PARENT", "verdict")]["revision"]
    old = _mutation("PARENT", "verdict", "could_read", revision, comment="old rationale")
    result = service.push(path, [old])["results"][0]
    new = _mutation("PARENT", "verdict", "dont_read" if latest is not None else None,
                    result["applied_revision"], operation="set" if latest is not None else "delete",
                    comment=latest or "")

    service.push(path, [new])
    assert "old rationale" not in _note(zdb)
    assert service.push(path, [old])["results"][0]["status"] == "already_applied"

    note = _note(zdb)
    assert "old rationale" not in note
    assert "Could Read" not in note
    if latest:
        assert latest in note and "Dont Read" in note
    tags = reader.get_item_detail("PARENT")["tags"]
    assert "label:could_read" not in tags
    assert ("label:dont_read" in tags) == (latest is not None)


def test_note_failure_is_retryable_after_local_commit(tmp_path, monkeypatch):
    path, zdb, _, _, writer, _ = _setup(tmp_path, monkeypatch, "offline")
    monkeypatch.setattr(verdict_effects, "append_training_row", lambda *args: None)
    revision = db.sync_current_fields(path)[("PARENT", "verdict")]["revision"]
    mutation = _mutation("PARENT", "verdict", "must_read", revision, comment="rationale")
    monkeypatch.setattr(writer, "is_connector_running", lambda: True)

    with pytest.raises(ZoteroWriteError):
        service.push(path, [mutation])

    assert db.get_label_verdict(path, "PARENT")["comment"] == "rationale"
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)
    assert service.push(path, [mutation])["results"][0]["status"] == "already_applied"
    assert "rationale" in _note(zdb)


@pytest.mark.parametrize("deleted", [False, True])
def test_newer_feed_alias_rationale_wins_over_library_replay(tmp_path, monkeypatch, deleted):
    path, zdb, _, _, _, _ = _setup(tmp_path, monkeypatch, "offline")
    monkeypatch.setattr(verdict_effects, "append_training_row", lambda *args: None)
    revision = db.sync_current_fields(path)[("PARENT", "verdict")]["revision"]
    old = _mutation("PARENT", "verdict", "could_read", revision, comment="old rationale")
    service.push(path, [old])
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO processed_feed_items(feed_library_id, feed_item_id, stable_feed_key, guid, title, decision, run_id, materialized_zotero_key) VALUES (2, 42, 'feed:guid:stable', 'g', 'Paper', 'auto_materialized', 'run', 'PARENT')")
        conn.execute("INSERT INTO feed_key_aliases(old_key, stable_feed_key) VALUES ('feed:42', 'feed:guid:stable')")
    db.insert_or_update_label_verdict(path, item_key="feed:guid:stable", original_derived_priority="unknown",
                                     user_priority="dont_read", comment="alias rationale")
    if deleted:
        db.delete_label_verdict(path, "feed:guid:stable")

    service.push(path, [old])

    assert "old rationale" not in _note(zdb)
    assert ("alias rationale" in _note(zdb)) == (not deleted)


def test_rationale_delivery_holds_current_intent_stable(tmp_path, monkeypatch):
    path, zdb, _, _, writer, _ = _setup(tmp_path, monkeypatch, "offline")
    db.insert_or_update_label_verdict(path, item_key="PARENT", original_derived_priority="unknown",
                                     user_priority="must_read", comment="current rationale")
    apply = writer.apply_changes
    delivered = []

    def check_lock(*args, **kwargs):
        with sqlite3.connect(path, timeout=0) as conn:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                conn.execute("BEGIN IMMEDIATE")
        delivered.append(True)
        return apply(*args, **kwargs)

    monkeypatch.setattr(writer, "apply_changes", check_lock)
    verdict_effects.mirror_current_verdict(path, "PARENT")
    assert delivered and "current rationale" in _note(zdb)
