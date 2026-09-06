"""HTTP key normalization and ownership of explicit app verdicts."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_verdict_mirror_retraction import _setup
from zotero_summarizer.api.errors import install_error_handlers
from zotero_summarizer.api.routes import golden
from zotero_summarizer.services.golden import label_verdicts, user_labels, verdict_effects
from zotero_summarizer.services.zotero import zotero
from zotero_summarizer.storage import repositories as db


def _app():
    app = FastAPI()
    app.include_router(golden.router)
    install_error_handlers(app)
    return app


@pytest.mark.parametrize("key", ["", "   ", "\t\n", None, 12, True, [], {}])
def test_invalid_verdict_key_is_rejected_before_io(monkeypatch, key):
    calls = []

    def unexpected():
        calls.append("read")
        raise AssertionError("invalid input reached provenance I/O")

    monkeypatch.setattr(golden, "_load_all", unexpected)
    with TestClient(_app(), raise_server_exceptions=False) as client:
        response = client.post("/api/golden/verdict", json={"item_key": key, "user_priority": "must_read"})

    assert response.status_code == 422
    assert calls == []


@pytest.mark.parametrize("key", ["PAPER001", "feed:guid:stable", "note:PAPER001:7"])
def test_padded_key_is_identical_for_provenance_storage_events_and_effects(tmp_path, monkeypatch, key):
    path = tmp_path / "triage.db"
    with db.with_db_path(path):
        db.init_db()
    monkeypatch.setattr(golden, "_db_path", lambda: path)
    monkeypatch.setattr(golden, "_load_all", lambda: [SimpleNamespace(item_key=key, derived_priority="should_read")])
    monkeypatch.setattr(label_verdicts, "log_committed_transition", lambda **kw: None)
    events, effects = [], []
    monkeypatch.setattr(golden, "log_verdict_event", lambda *args: events.append(args))
    monkeypatch.setattr(verdict_effects, "apply_verdict_effects", lambda *args: effects.append(args) or {})
    comment = "  keep my rationale  "
    with TestClient(_app()) as client:
        response = client.post("/api/golden/verdict", json={
            "item_key": f" \t{key}\n ", "user_priority": "must_read", "comment": comment,
        })

    assert response.status_code == 200
    stored = db.get_label_verdict(path, key)
    assert stored["original_derived_priority"] == "should_read"
    assert stored["comment"] == comment
    assert events == [(key, "should_read", "must_read", comment)]
    assert effects == [(path, key, "must_read", comment)]
    assert golden.VerdictRequest(item_key=f" {key} ", user_priority="must_read").item_key == key


@pytest.mark.parametrize("original", ["zotero_label", "could_read"])
def test_online_confirmation_without_csv_provenance_takes_app_ownership(tmp_path, monkeypatch, original):
    path, zdb, item, reader, writer, _ = _setup(tmp_path, monkeypatch, "online")
    db.insert_or_update_label_verdict(path, item_key="PARENT", original_derived_priority=original,
                                     user_priority="must_read", comment="")
    revision = db.sync_current_fields(path)[("PARENT", "verdict")]["revision"]
    zotero.zotero_set_label_tag("PARENT", None)
    monkeypatch.setattr(writer, "is_connector_running", lambda: True)
    monkeypatch.setattr(golden, "_load_all", lambda: [])
    monkeypatch.setattr(verdict_effects, "append_training_row", lambda *args: None)
    events = []
    monkeypatch.setattr(golden, "log_verdict_event", lambda *args: events.append(args))

    result = asyncio.run(golden.submit_verdict(golden.VerdictRequest(item_key="PARENT", user_priority="must_read")))

    assert result["label_error"]  # No Zotero write can hide an ownership error.
    assert user_labels.reconcile_label_verdicts([], zdb, path).removed == 0
    stored = db.get_label_verdict(path, "PARENT")
    expected = "unknown" if original == "zotero_label" else original
    assert stored["original_derived_priority"] == expected
    assert events == [("PARENT", expected, "must_read", "")]
    if original == "zotero_label":
        assert db.sync_current_fields(path)[("PARENT", "verdict")]["revision"] > revision
