"""Authored-only composite admission controls; owned SQLite/native delegates, UNRUN."""
from contextlib import closing, contextmanager
from itertools import zip_longest
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock, PropertyMock

import pytest

from tests._direct_write_fixtures import native, seed_direct, sql_rows
from tests._zotero_fixtures import add_library_item, build_zotero_db
from tests.test_review_note_receipts import _assert_local, _assert_native_note
from tests.test_review_questions import _database
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.runtime import get_context
from zotero_summarizer.services.golden import verdict_effects
from zotero_summarizer.services.library import review_questions
from zotero_summarizer.services.zotero import zotero
from zotero_summarizer.storage import pending_actions, repositories, review_note_projection

pytestmark = pytest.mark.parametrize("admission_boundary", [False], indirect=True)


def _select_native(monkeypatch, reader, writer):
    state = get_context().state
    monkeypatch.setattr(state, "zotero_reader", reader)
    monkeypatch.setattr(state, "zotero_writer", writer)
    # Baseline-only imported getter; the actual getters use the runtime objects above.
    monkeypatch.setattr(verdict_effects, "get_zotero_reader_or_raise", lambda: reader, raising=False)


@pytest.fixture
def admission_boundary(tmp_path, monkeypatch, request):
    assert request.param is False
    config = _database(tmp_path)
    db = build_zotero_db(tmp_path / "synthetic-zotero")
    parent = add_library_item(db, item_key="ITEM0001", title="Synthetic paper")
    reader, writer = ZoteroReader(db.parent), ZoteroWriter(db.parent)
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)
    _select_native(monkeypatch, reader, writer)
    repositories.upsert_review_note(config.triage_db_path, "ITEM0001", "Manual & note")
    saved = review_questions.save_question({
        "submission_id": "20700000-0000-4000-8000-000000000001", "item_key": "ITEM0001",
        "question": "Question <one>?", "origin": "typed", "preset_id": None,
    })
    revision = sql_rows(config.triage_db_path, "SELECT MAX(revision) FROM sync_changes "
                        "WHERE item_key='ITEM0001' AND field IN ('review_note','review_question')")[0][0]
    assert type(revision) is int and revision > 0
    b = SimpleNamespace(app=config.triage_db_path, db=db, reader=reader, writer=writer,
                        parent=parent, revision=revision, submitted_at=saved["submitted_at"],
                        target=writer.target_identity(), before=native(db))
    _assert_local(b, acknowledged=0)
    return b


def _peer(b, monkeypatch, name):
    db = build_zotero_db(b.db.parent.parent / name)
    with closing(sqlite3.connect(f"{b.db.as_uri()}?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(db)) as destination:
            source.backup(destination)
    writer, reader = ZoteroWriter(db.parent), ZoteroReader(db.parent)
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)
    target = writer.target_identity()
    assert target.database_path != b.target.database_path
    assert (target.device, target.inode) != (b.target.device, b.target.inode)
    assert reader.db_path == writer.db_path == db
    assert reader.get_item_detail("ITEM0001") == b.reader.get_item_detail("ITEM0001")
    assert native(db) == native(b.db)
    return SimpleNamespace(db=db, reader=reader, writer=writer, target=target)


def _watch_io(writer, monkeypatch):
    backup, apply = Mock(wraps=writer.backup_database), Mock(wraps=writer.apply_changes)
    monkeypatch.setattr(writer, "backup_database", backup)
    monkeypatch.setattr(writer, "apply_changes", apply)
    return backup, apply


def _backup_state(db):
    return {str(path): path.read_bytes() for path in sorted(db.parent.glob("zotero.sqlite.backup_*"))}


@contextmanager
def _saved_app(path):
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as current:
        with closing(sqlite3.connect(":memory:")) as saved:
            current.backup(saved)
            yield saved


def _assert_app_unchanged(path, saved):
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as current:
        for table in ("pending_changes", "review_notes", "review_questions",
                      "review_note_mirror_intents", "sync_changes"):
            statement = f"SELECT * FROM {table} ORDER BY rowid"
            for actual, original in zip_longest(current.execute(statement), saved.execute(statement)):
                assert actual == original, table


def _capped_summary(b, older_id, target):
    with repositories.with_db_path(b.app):
        rows = repositories.get_pending_changes("applying", 5000, item_key="ITEM0001")
    return (len(rows), any(row["id"] == older_id for row in rows),
            all(row["audit"][0]["target"] == target.model_dump() for row in rows))


@pytest.mark.parametrize("source", ["direct_tags", "direct_priority", "direct_collections"])
def test_uncapped_same_target_applying_blocks_composite(admission_boundary, monkeypatch, source):
    b = admission_boundary
    peer = _peer(b, monkeypatch, "uncapped-owned-target")
    with repositories.with_db_path(b.app):
        older = seed_direct(b, "applying", source=source, key="ITEM0001")
        newer = seed_direct(peer, "applying", source=source, key="ITEM0001")
    assert pending_actions.direct_request(older)["target"] == b.target.model_dump()
    assert pending_actions.direct_request(newer)["target"] == peer.target.model_dump()
    columns = ",".join(row[1] for row in sql_rows(b.app, "PRAGMA table_info(pending_changes)")
                       if row[1] != "id")
    with closing(sqlite3.connect(b.app)) as conn, conn:
        conn.execute("UPDATE pending_changes SET created_at='2000-01-01' WHERE id=?", (older["id"],))
        conn.execute("UPDATE pending_changes SET created_at='2001-01-01' WHERE id=?", (newer["id"],))
        # Synthetic history stress data, not 5,000 genuinely admitted native requests.
        conn.executemany(f"INSERT INTO pending_changes ({columns}) SELECT {columns} "
                         "FROM pending_changes WHERE id=?", ((newer["id"],) for _ in range(4999)))
    assert sql_rows(b.app, "SELECT COUNT(*) FROM pending_changes") == [(5001,)]
    assert _capped_summary(b, older["id"], peer.target) == (5000, False, True)
    assert sql_rows(b.app, "SELECT id FROM pending_changes WHERE status='applying' "
                    "AND item_key='ITEM0001' AND json_extract(audit_json,'$[0].target.database_path')=? "
                    "ORDER BY id", (b.target.database_path,)) == [(older["id"],)]
    watches = _watch_io(b.writer, monkeypatch) + _watch_io(peer.writer, monkeypatch)
    ack = Mock(wraps=review_note_projection.acknowledge)
    monkeypatch.setattr(review_note_projection, "acknowledge", ack)
    before, peer_before = native(b.db), native(peer.db)
    paths, peer_paths = _backup_state(b.db), _backup_state(peer.db)
    with _saved_app(b.app) as saved:
        result = verdict_effects.mirror_review_note(b.app, "ITEM0001")

        assert result == {"note_written": False, "note_error": "zotero_write_uncertain", "note_pending": True,
                          "projection_revision": b.revision, "target_key": "ITEM0001"}
        for watch in watches:
            watch.assert_not_called()
        ack.assert_not_called()
        _assert_app_unchanged(b.app, saved)
    assert native(b.db) == before and native(peer.db) == peer_before
    assert _backup_state(b.db) == paths and _backup_state(peer.db) == peer_paths
    _assert_local(b, acknowledged=0)


@pytest.mark.parametrize("blocker", ["blocker-otheritem", "blocker-othertarget"])
def test_independent_applying_allows_composite(admission_boundary, monkeypatch, blocker):
    b = admission_boundary
    add_library_item(b.db, item_key="ITEM0002", title="Independent paper")
    peer = _peer(b, monkeypatch, "independent-owned-target")
    selected, key = (b, "ITEM0002") if blocker == "blocker-otheritem" else (peer, "ITEM0001")
    with repositories.with_db_path(b.app):
        row = seed_direct(selected, "applying", source="direct_tags", key=key)
    assert row["item_key"] == key
    assert pending_actions.direct_request(row)["target"] == selected.writer.target_identity().model_dump()
    saved_row = sql_rows(b.app, "SELECT * FROM pending_changes WHERE id=?", (row["id"],))
    journal = sql_rows(b.app, "SELECT * FROM sync_changes ORDER BY revision")
    other_item = sql_rows(b.db, "SELECT * FROM items WHERE key='ITEM0002'")
    b.before, peer_before = native(b.db), native(peer.db)
    primary_io, peer_io = _watch_io(b.writer, monkeypatch), _watch_io(peer.writer, monkeypatch)
    paths, peer_paths = _backup_state(b.db), _backup_state(peer.db)
    ack = Mock(wraps=review_note_projection.acknowledge)
    monkeypatch.setattr(review_note_projection, "acknowledge", ack)

    result = verdict_effects.mirror_review_note(b.app, "ITEM0001")

    assert result == {"note_written": True, "note_error": None, "note_pending": False,
                      "projection_revision": b.revision, "target_key": "ITEM0001"}
    for watch in primary_io:
        watch.assert_called_once()
    for watch in peer_io:
        watch.assert_not_called()
    ack.assert_called_once()
    _assert_native_note(b)
    _assert_local(b, acknowledged=1)
    assert sql_rows(b.db, "SELECT * FROM items WHERE key='ITEM0002'") == other_item
    assert sql_rows(b.app, "SELECT * FROM pending_changes WHERE id=?", (row["id"],)) == saved_row
    assert sql_rows(b.app, "SELECT * FROM sync_changes ORDER BY revision") == journal
    assert native(peer.db) == peer_before and _backup_state(peer.db) == peer_paths
    after_paths = _backup_state(b.db)
    assert set(paths) < set(after_paths) and len(after_paths) == len(paths) + 1
    assert all(after_paths[path] == content for path, content in paths.items())
    reference = next(path for path in after_paths if path not in paths)
    assert native(reference) == b.before
    assert sql_rows(reference, "PRAGMA integrity_check") == [("ok",)]


def test_same_intent_ack_retry_cannot_retarget_native_library(admission_boundary, monkeypatch):
    b = admission_boundary
    primary_io = _watch_io(b.writer, monkeypatch)
    ack = Mock(wraps=review_note_projection.acknowledge)
    monkeypatch.setattr(review_note_projection, "acknowledge", ack)
    with closing(sqlite3.connect(b.app)) as conn, conn:
        conn.execute("CREATE TRIGGER reject_209_ack BEFORE UPDATE OF acknowledged "
                     "ON review_note_mirror_intents WHEN NEW.acknowledged=1 "
                     "BEGIN SELECT RAISE(ABORT, 'owned ACK refusal'); END")

    first = verdict_effects.mirror_review_note(b.app, "ITEM0001")

    assert first == {"note_written": True, "note_error": "IntegrityError", "note_pending": True,
                     "projection_revision": b.revision, "target_key": "ITEM0001"}
    for watch in primary_io:
        watch.assert_called_once()
    ack.assert_called_once()
    native_a = _assert_native_note(b)
    _assert_local(b, acknowledged=0)
    peer = _peer(b, monkeypatch, "retarget-owned-library")
    peer_io = _watch_io(peer.writer, monkeypatch)
    native_b = native(peer.db)
    with closing(sqlite3.connect(b.app)) as conn, conn:
        conn.execute("DROP TRIGGER reject_209_ack")
    history_a = sql_rows(b.app, "SELECT id,status FROM pending_changes ORDER BY id")
    paths, peer_paths = _backup_state(b.db), _backup_state(peer.db)
    _select_native(monkeypatch, peer.reader, peer.writer)
    for watch in primary_io:
        watch.reset_mock()
    ack.reset_mock()
    with _saved_app(b.app) as saved:
        second = verdict_effects.mirror_review_note(b.app, "ITEM0001")

        assert second == {"note_written": False, "note_error": "target_changed", "note_pending": True,
                          "projection_revision": b.revision, "target_key": "ITEM0001"}
        for watch in primary_io + peer_io:
            watch.assert_not_called()
        ack.assert_not_called()
        _assert_app_unchanged(b.app, saved)
    assert native(b.db) == native_a and native(peer.db) == native_b
    assert _backup_state(b.db) == paths and _backup_state(peer.db) == peer_paths
    _assert_local(b, acknowledged=0)
    # Kept after retarget so missing baseline receipts do not mask the policy discriminator.
    assert len(history_a) == 1 and history_a[0][0] > 0 and history_a[0][1] == "applied"


def test_legacy_domain_ack_needs_no_configured_zotero(admission_boundary, monkeypatch):
    b = admission_boundary
    with review_note_projection.current_for_mirror(b.app, "ITEM0001") as (conn, desired):
        assert desired["pending"] is True and desired["revision"] == b.revision
        assert review_note_projection.acknowledge(conn, desired) is True
    _assert_local(b, acknowledged=1)
    assert sql_rows(b.app, "SELECT COUNT(*) FROM pending_changes") == [(0,)]
    assert native(b.db) == b.before and _backup_state(b.db) == {}
    context = get_context()
    probe = Mock(side_effect=AssertionError("legacy no-op probed native/configuration"))
    ack = Mock(wraps=review_note_projection.acknowledge)
    paths = _backup_state(b.db)
    with _saved_app(b.app) as saved, monkeypatch.context() as trap:
        trap.setattr(review_note_projection, "acknowledge", ack)
        trap.setattr(type(context), "settings", PropertyMock(side_effect=probe), raising=False)
        for name in ("zotero_reader", "zotero_writer", "zotero_error", "app_state"):
            trap.setattr(type(context.state), name, PropertyMock(side_effect=probe))
        for owner, name in ((verdict_effects, "get_zotero_reader_or_raise"),
                            (zotero, "get_zotero_reader_or_raise"),
                            (zotero, "get_zotero_writer_or_raise"),
                            (b.reader, "_connect"), (b.writer, "target_identity"),
                            (b.writer, "backup_database"), (b.writer, "apply_changes")):
            trap.setattr(owner, name, probe)

        result = verdict_effects.mirror_review_note(b.app, "ITEM0001")

        assert result == {"note_written": False, "note_error": None, "note_pending": False,
                          "projection_revision": b.revision, "target_key": "ITEM0001"}
        probe.assert_not_called()
        ack.assert_not_called()
        _assert_app_unchanged(b.app, saved)
    assert native(b.db) == b.before and _backup_state(b.db) == paths
    _assert_local(b, acknowledged=1)
