"""Native Search Add must survive its session-store failure boundary."""
from types import SimpleNamespace
import sqlite3

import pytest

from tests._zotero_fixtures import build_zotero_db
from tests.test_search_materialize import _candidate, _session, _store


def _setup(monkeypatch, tmp_path):
    from zotero_summarizer.integrations.zotero_write import ZoteroWriter
    from zotero_summarizer.services.search import materialize, session
    from zotero_summarizer.services.triage import feeds

    db_path = build_zotero_db(tmp_path / "zotero")
    _store(monkeypatch, tmp_path / "sessions")
    monkeypatch.setattr(materialize, "settings", lambda: SimpleNamespace(zotero_data_dir=db_path.parent))
    monkeypatch.setattr(ZoteroWriter, "is_connector_running", lambda self: False)
    keys = iter(("ADDED001", "ADDED002", "ADDED003"))
    monkeypatch.setattr(feeds, "_generate_zotero_key", lambda used: next(keys))
    candidate = _candidate("Crash-boundary paper", doi="10.1/recovery")
    session.save(_session("recovery", candidate))
    return session, materialize, candidate.candidate_id, db_path


def _native_keys(db_path):
    with sqlite3.connect(db_path) as conn:
        return [row[0] for row in conn.execute(
            "SELECT i.key FROM items i JOIN itemTypes t USING(itemTypeID) "
            "WHERE i.libraryID=1 AND t.typeName='journalArticle' ORDER BY i.itemID"
        )]


@pytest.mark.parametrize("retry_collection", [None, "RESEARCH1"])
def test_native_commit_then_session_save_failure_does_not_duplicate(monkeypatch, tmp_path, retry_collection):
    session, materialize, candidate_id, db_path = _setup(monkeypatch, tmp_path)
    save = session.save

    def fail_finalization(research):
        if research.candidates[0].materialized_zotero_key:
            raise OSError("injected session finalization failure")
        save(research)

    monkeypatch.setattr(session, "save", fail_finalization)
    with pytest.raises(OSError, match="injected session finalization failure"):
        materialize.materialize_candidate("recovery", candidate_id, None)
    assert _native_keys(db_path) == ["ADDED001"]
    assert session.load("recovery").candidates[0].materialized_zotero_key == ""

    monkeypatch.setattr(session, "save", save)
    result = materialize.materialize_candidate("recovery", candidate_id, retry_collection)
    assert _native_keys(db_path) == ["ADDED001"]
    assert result == {"status": "already_added", "zotero_key": "ADDED001"}
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT c.collectionName FROM collectionItems ci JOIN collections c USING(collectionID) "
            "JOIN items i USING(itemID) WHERE i.key='ADDED001'"
        ).fetchall() == [("Inbox",)]
    assert session.load("recovery").candidates[0].materialized_zotero_key == "ADDED001"


def test_failed_prewrite_session_persistence_has_zero_native_effect(monkeypatch, tmp_path):
    session, materialize, candidate_id, db_path = _setup(monkeypatch, tmp_path)

    def fail_save(research):
        raise OSError("injected session persistence failure")

    monkeypatch.setattr(session, "save", fail_save)
    with pytest.raises(OSError, match="injected session persistence failure"):
        materialize.materialize_candidate("recovery", candidate_id, None)
    assert _native_keys(db_path) == []


@pytest.mark.parametrize("coverage_key", ["ADDED001", "KNOWN002"])
def test_delivered_add_validates_enriched_coverage_before_fast_return(monkeypatch, tmp_path, coverage_key):
    from tests._zotero_fixtures import add_library_item
    from zotero_summarizer.api.errors import APIError
    from zotero_summarizer.services.search._models import Candidate
    from zotero_summarizer.services.search.dedup import to_version_families

    session, materialize, candidate_id, db_path = _setup(monkeypatch, tmp_path)
    materialize.materialize_candidate("recovery", candidate_id, None)
    if coverage_key != "ADDED001":
        add_library_item(db_path, item_key=coverage_key, title="Related library record")
    current = session.load("recovery")
    richer = Candidate(title="Richer publication", doi=current.candidates[0].doi,
                       abstract="Enriched metadata", in_library=True, existing_zotero_key=coverage_key)
    current.candidates = to_version_families([*current.candidates, richer])
    session.save_merge(current)
    path = tmp_path / "sessions" / "recovery.json"
    saved_before = path.read_bytes()
    with sqlite3.connect(db_path) as conn:
        native_before = list(conn.iterdump())
    backups_before = sorted(db_path.parent.glob("zotero.sqlite.backup_*"))
    monkeypatch.setattr(materialize, "ZoteroWriter", lambda *args: pytest.fail("delivered Add reached writer"))
    if coverage_key != "ADDED001":
        with pytest.raises(APIError) as error:
            materialize.materialize_candidate("recovery", candidate_id, None)
        assert error.value.status_code == 409 and error.value.error == "search_write_conflict"
    else:
        assert materialize.materialize_candidate("recovery", candidate_id, None) == {
            "status": "already_added", "zotero_key": "ADDED001",
        }
    assert path.read_bytes() == saved_before
    with sqlite3.connect(db_path) as conn:
        assert list(conn.iterdump()) == native_before
    assert sorted(db_path.parent.glob("zotero.sqlite.backup_*")) == backups_before
