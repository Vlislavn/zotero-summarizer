"""Native reduced-capability ACKs, not inference about a prior failed attempt."""
import sqlite3

import pytest

from tests.test_discovery_server import STABLE, journey
from tests.test_discovery_recovery_joint import native_snapshot
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations import _zotero_write_items as native
from zotero_summarizer.integrations.zotero_write import ZoteroWriter, ZoteroWriteError
from zotero_summarizer.services.search import discovery, materialize, session
from zotero_summarizer.storage import repositories


@pytest.fixture
def retained(journey, monkeypatch):
    metadata = discovery.discovery_metadata(STABLE)
    response = journey.client.post(f"/api/library/related/{STABLE}/discover", json={
        "context_fingerprint": metadata["context_fingerprint"],
    })
    assert response.status_code == 200
    research = session.load(response.json()["id"])
    research.candidates[0].planned_zotero_key = "ADDED001"
    session.save(research)
    repositories.upsert_review_note(journey.paths.triage_db_path, STABLE, "Changed seed context")
    monkeypatch.setattr(materialize, "settings", lambda: journey.paths)
    monkeypatch.setattr(ZoteroWriter, "is_connector_running", lambda self: False)
    return research


@pytest.mark.parametrize("receipt", [None, {}, {"item_key": "ADDED001"},
    {"item_key": "WRONG001", "applied_steps": [], "backup_path": None},
    {"item_key": "ADDED001", "applied_steps": ["create"], "backup_path": None},
    {"item_key": "ADDED001", "applied_steps": (), "backup_path": None},
    {"item_key": "ADDED001", "applied_steps": [], "backup_path": "unexpected"},
    {"item_key": None, "applied_steps": [], "backup_path": None},
])
def test_malformed_or_missing_native_ack_cannot_finalize(retained, journey, monkeypatch, receipt):
    calls = []
    def reply(self, **kwargs):
        calls.append(kwargs)
        return receipt
    monkeypatch.setattr(ZoteroWriter, "apply_feed_materialization", reply)
    with pytest.raises(APIError) as error:
        materialize.materialize_candidate(retained.id, retained.candidates[0].candidate_id, None)
    assert error.value.status_code == 409
    assert len(calls) == 1 and calls[0]["existing_only"] is True
    assert session.load(retained.id).candidates[0].materialized_zotero_key == ""


def arguments(key):
    return dict(new_item_key=key, feed_payload={"title": "Must never create"},
                inbox_collection_name="Inbox", tags=[], note_title="", note_html="", existing_only=True)


def test_existing_only_ack_is_available_while_connector_is_running(journey, monkeypatch):
    writer = ZoteroWriter(journey.paths.zotero_data_dir)
    monkeypatch.setattr(writer, "is_connector_running", lambda: True)
    before = native_snapshot(journey.native_db)
    assert writer.apply_feed_materialization(**arguments("SEED0001")) == {
        "item_key": "SEED0001", "applied_steps": [], "backup_path": None}
    assert native_snapshot(journey.native_db) == before
    ordinary = {**arguments("SEED0001"), "existing_only": False}
    with pytest.raises(ZoteroWriteError, match="running"):
        writer.apply_feed_materialization(**ordinary)
    assert native_snapshot(journey.native_db) == before


@pytest.mark.parametrize("key,found", [("SEED0001", True), ("MISSING1", False),
                                      ("FOREIGN1", False), ("GROUP001", False)])
def test_existing_only_is_enforced_at_native_execution(journey, monkeypatch, key, found):
    with sqlite3.connect(journey.native_db) as conn:
        conn.executemany("INSERT INTO libraries(libraryID,type) VALUES (?,?)", [(91, "feed"), (92, "group")])
        conn.executemany("INSERT INTO items(itemID,itemTypeID,libraryID,key) VALUES (?,?,?,?)",
                         [(91, 2, 91, "FOREIGN1"), (92, 2, 92, "GROUP001")])
    before = native_snapshot(journey.native_db)
    writer = ZoteroWriter(journey.paths.zotero_data_dir)
    def forbidden(*args, **kwargs):
        pytest.fail("Existing-only mode entered a native mutation/backup/pruning path")
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)
    for name in ("backup_database", "_prune_backups", "_materialize_in_conn"):
        monkeypatch.setattr(writer, name, forbidden)
    connect, commands = sqlite3.connect, []
    class GuardConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            assert sql.strip().upper().startswith(("SELECT", "PRAGMA QUERY_ONLY=")), sql
            commands.append(sql)
            return super().execute(sql, *args, **kwargs)
    def readonly(path, **kwargs):
        assert path == journey.native_db.resolve().as_uri() + "?mode=ro"
        assert kwargs["uri"] is True
        return connect(path, **kwargs, factory=GuardConnection)
    with monkeypatch.context() as scoped:
        scoped.setattr(native.sqlite3, "connect", readonly)
        receipt = writer.apply_feed_materialization(**arguments(key))
    assert receipt == {"item_key": key if found else None, "applied_steps": [], "backup_path": None}
    assert commands[0] == "PRAGMA query_only=ON"
    assert native_snapshot(journey.native_db) == before


@pytest.mark.parametrize("freshness", ["stale", "unverified"])
def test_unplanned_refusal_precedes_writer_construction(retained, monkeypatch, freshness):
    retained.candidates[0].planned_zotero_key = ""
    session.save(retained)
    monkeypatch.setattr(discovery, "_freshness", lambda research: freshness)
    def forbidden(*args, **kwargs):
        pytest.fail("Unplanned stale/unverified candidate constructed a writer")
    monkeypatch.setattr(materialize, "ZoteroWriter", forbidden)
    with pytest.raises(APIError) as error:
        materialize.materialize_candidate(retained.id, retained.candidates[0].candidate_id, None)
    assert error.value.status_code == 409
    assert session.load(retained.id).candidates[0].materialized_zotero_key == ""


def test_existing_only_schema_failure_does_not_finalize(retained, journey, monkeypatch):
    with sqlite3.connect(journey.native_db) as conn:
        conn.execute("ALTER TABLE libraries RENAME TO unavailable_libraries")
    with pytest.raises(APIError) as error:
        materialize.materialize_candidate(retained.id, retained.candidates[0].candidate_id, None)
    assert error.value.status_code == 503
    assert session.load(retained.id).candidates[0].materialized_zotero_key == ""


def test_missing_database_never_created_for_existing_only(journey, monkeypatch):
    writer = ZoteroWriter(journey.paths.zotero_data_dir)
    writer.db_path = journey.native_db.with_name("absent.sqlite")
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)
    with pytest.raises(ZoteroWriteError):
        writer.apply_feed_materialization(**arguments("ADDED001"))
    assert not writer.db_path.exists()


def test_raised_writer_is_not_finalized_by_exception_time_readback(retained, journey, monkeypatch):
    original = ZoteroWriter.apply_feed_materialization
    repositories.upsert_review_note(journey.paths.triage_db_path, STABLE, "")
    research = session.load(retained.id)
    research.discovery["context_fingerprint"] = discovery._inputs(STABLE)["fingerprint"]
    session.save(research)
    def raised(self, **kwargs):
        original(self, **kwargs)
        raise ZoteroWriteError("Controlled failure after native return")
    monkeypatch.setattr(ZoteroWriter, "apply_feed_materialization", raised)
    for _ in range(2):
        with pytest.raises(APIError) as error:
            materialize.materialize_candidate(research.id, research.candidates[0].candidate_id, None)
        assert error.value.status_code == 503
        assert session.load(research.id).candidates[0].materialized_zotero_key == ""
    before = native_snapshot(journey.native_db)
    monkeypatch.setattr(ZoteroWriter, "apply_feed_materialization", original)
    receipt = materialize.materialize_candidate(research.id, research.candidates[0].candidate_id, None)
    assert receipt == {"status": "already_added", "zotero_key": "ADDED001"}
    assert native_snapshot(journey.native_db) == before
    assert discovery._freshness(session.load(research.id)) == "stale"


def test_existing_only_reads_committed_wal_not_immutable_main(journey, monkeypatch):
    writer = ZoteroWriter(journey.paths.zotero_data_dir)
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)
    pin = sqlite3.connect(journey.native_db)
    try:
        pin.execute("PRAGMA journal_mode=WAL")
        pin.execute("BEGIN")
        pin.execute("SELECT COUNT(*) FROM items").fetchone()
        with sqlite3.connect(journey.native_db) as conn:
            conn.execute("INSERT INTO items(itemID,itemTypeID,libraryID,key) VALUES (91,2,1,'WAL00001')")
        assert journey.native_db.with_name("zotero.sqlite-wal").stat().st_size > 0
        assert writer.apply_feed_materialization(**arguments("WAL00001"))["item_key"] == "WAL00001"
    finally:
        pin.close()
