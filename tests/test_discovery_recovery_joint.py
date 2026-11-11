"""Discovery must not erase the retained Add recovery contract."""
import sqlite3

import pytest

from tests.test_discovery_server import STABLE, journey
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations.zotero_write import ZoteroWriter, ZoteroWriteError
from zotero_summarizer.services.search import discovery, materialize, session
from zotero_summarizer.services.triage import feeds
from zotero_summarizer.storage import repositories


def native_snapshot(path):
    with sqlite3.connect(path) as conn:
        return list(conn.iterdump())


@pytest.mark.parametrize("native_committed", [False, True])
def test_discovery_explicit_plan_recovery_never_creates_under_stale_context(journey, monkeypatch, native_committed):
    metadata = discovery.discovery_metadata(STABLE)
    response = journey.client.post(f"/api/library/related/{STABLE}/discover", json={
        "context_fingerprint": metadata["context_fingerprint"],
    })
    assert response.status_code == 200
    result = response.json()
    candidate = result["candidates"][0]["candidate_id"]
    monkeypatch.setattr(materialize, "settings", lambda: journey.paths)
    monkeypatch.setattr(ZoteroWriter, "is_connector_running", lambda self: False)
    monkeypatch.setattr(feeds, "_generate_zotero_key", lambda used: "ADDED001")
    save, apply = session.save, ZoteroWriter.apply_feed_materialization

    def failed_final_save(research):
        if any(row.materialized_zotero_key for row in research.candidates):
            raise OSError("Controlled post-native final-save failure")
        save(research)

    def failed_before_native(self, **kwargs):
        raise ZoteroWriteError("Controlled pre-native refusal")

    if native_committed:
        monkeypatch.setattr(session, "save", failed_final_save)
        with pytest.raises(OSError):
            materialize.materialize_candidate(result["id"], candidate, None)
    else:
        monkeypatch.setattr(ZoteroWriter, "apply_feed_materialization", failed_before_native)
        with pytest.raises(APIError) as error:
            materialize.materialize_candidate(result["id"], candidate, None)
        assert error.value.status_code == 503
        repositories.upsert_review_note(journey.paths.triage_db_path, STABLE, "Changed seed context")
    retained = session.load(result["id"])
    assert retained.candidates[0].planned_zotero_key == "ADDED001"
    assert retained.candidates[0].materialized_zotero_key == ""
    assert discovery.discovery_view(retained)["discovery"]["freshness"] == "stale"
    before = native_snapshot(journey.native_db)
    monkeypatch.setattr(session, "save", save)
    monkeypatch.setattr(ZoteroWriter, "apply_feed_materialization", apply)
    if native_committed:
        acknowledgement = materialize.materialize_candidate(result["id"], candidate, None)
        assert acknowledgement["zotero_key"] == "ADDED001"
        assert session.load(result["id"]).candidates[0].materialized_zotero_key == "ADDED001"
    else:
        with pytest.raises(APIError) as error:
            materialize.materialize_candidate(result["id"], candidate, None)
        assert error.value.status_code == 409
        assert session.load(result["id"]).candidates[0].materialized_zotero_key == ""
    assert native_snapshot(journey.native_db) == before
    assert discovery.discovery_view(session.load(result["id"]))["discovery"]["freshness"] == "stale"
