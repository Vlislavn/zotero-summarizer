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


def test_native_commit_then_session_save_failure_does_not_duplicate(monkeypatch, tmp_path):
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
    result = materialize.materialize_candidate("recovery", candidate_id, None)
    assert _native_keys(db_path) == ["ADDED001"]
    assert result["zotero_key"] == "ADDED001"
    assert session.load("recovery").candidates[0].materialized_zotero_key == "ADDED001"


def test_failed_prewrite_session_persistence_has_zero_native_effect(monkeypatch, tmp_path):
    session, materialize, candidate_id, db_path = _setup(monkeypatch, tmp_path)

    def fail_save(research):
        raise OSError("injected session persistence failure")

    monkeypatch.setattr(session, "save", fail_save)
    with pytest.raises(OSError, match="injected session persistence failure"):
        materialize.materialize_candidate("recovery", candidate_id, None)
    assert _native_keys(db_path) == []
