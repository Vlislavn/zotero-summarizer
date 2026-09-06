"""HTTP batch boundaries preserve immutable mutations and real conflicts."""
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_offline_sync import _db, _mutation
from zotero_summarizer.api.errors import install_error_handlers
from zotero_summarizer.api.routes import sync
from zotero_summarizer.storage import repositories


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(sync, "_db_path", lambda: db)
    monkeypatch.setattr(sync.service.verdict_effects, "mirror_review_note", lambda *_: {})
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(sync.router)
    with TestClient(app) as http:
        yield http, db


def _push(http, mutations, predecessors=()):
    return http.post("/api/sync/push", json={
        "protocol": 1, "mutations": mutations, "predecessors": list(predecessors),
    })


def test_101_same_field_edits_continue_without_replaying_old_effects(client, monkeypatch):
    http, db = client
    effects = []
    monkeypatch.setattr(sync.service.verdict_effects, "mirror_review_note", lambda *_: effects.append(1))
    rows = [_mutation("P1", "review_note", f"draft {i}", 0) for i in range(101)]
    assert _push(http, rows).status_code == 422
    first = _push(http, rows[:100])
    assert first.status_code == 200
    assert {r["status"] for r in first.json()["results"]} == {"applied"}
    last = _push(http, rows[100:], [rows[99]["mutation_id"]])
    assert last.status_code == 200
    assert last.json()["results"][0]["status"] == "applied"
    assert repositories.get_review_note(db, "P1") == "draft 100"
    assert len(effects) == 101
    replay = _push(http, rows[100:], [rows[99]["mutation_id"]])
    assert replay.json()["results"][0]["status"] == "already_applied"


@pytest.mark.parametrize("intervening", [False, True])
def test_receipt_cannot_rebase_another_device_or_overwrite_intervening_edit(client, intervening):
    http, db = client
    first = _mutation("P1", "review_note", "first", 0)
    revision = _push(http, [first]).json()["results"][0]["applied_revision"]
    if intervening:
        remote = _mutation("P1", "review_note", "remote", revision, device_id="other")
        assert _push(http, [remote]).json()["results"][0]["status"] == "applied"
    next_edit = _mutation("P1", "review_note", "stale", 0,
                          device_id=first["device_id"] if intervening else "other")
    result = _push(http, [next_edit], [first["mutation_id"]])
    assert result.json()["results"][0]["status"] == "conflict"
    assert repositories.get_review_note(db, "P1") == ("remote" if intervening else "first")


def test_unknown_receipt_is_not_a_successful_continuation(client):
    http, db = client
    response = _push(http, [_mutation("P1", "review_note", "draft", 0)], [str(uuid4())])
    assert response.status_code == 422
    assert repositories.get_review_note(db, "P1") is None


def test_conflict_receipt_cannot_authorize_a_continuation(client):
    http, db = client
    _push(http, [_mutation("P1", "review_note", "server", 0)])
    stale = _mutation("P1", "review_note", "stale", 0)
    assert _push(http, [stale]).json()["results"][0]["status"] == "conflict"
    response = _push(http, [_mutation("P2", "review_note", "new", 0)], [stale["mutation_id"]])
    assert response.status_code == 422
    assert repositories.get_review_note(db, "P2") is None
