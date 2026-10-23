"""Explicit server-choice receipts never echo or rewrite canonical content."""
import json
import sqlite3

import pytest
from pydantic import ValidationError

from tests.test_offline_sync import _db, _mutation
from zotero_summarizer.api.routes.sync import _PushRequest
from zotero_summarizer.services.sync import service
from zotero_summarizer.storage import repositories


@pytest.fixture
def store(tmp_path, monkeypatch):
    db = _db(tmp_path)
    effects = []
    for name in ("apply_verdict_effects", "mirror_review_note", "mirror_current_verdict"):
        monkeypatch.setattr(service.verdict_effects, name, lambda *a, **k: effects.append((a, k)))
    transitions = []
    monkeypatch.setattr(service.label_verdicts, "log_committed_transition", lambda **kw: transitions.append(kw))
    return db, effects, transitions


def _server_value(db, field, value):
    if field == "review_note":
        repositories.upsert_review_note(db, "P1", value)
    else:
        repositories.insert_or_update_label_verdict(db, item_key="P1", user_priority=value,
            original_derived_priority="should_read", comment="Server rationale " * 800)


def _conflict(db, field):
    canonical = "Canonical server note " * 2500 if field == "review_note" else "must_read"
    _server_value(db, field, canonical)
    original = _mutation("P1", field, "Short device note" if field == "review_note" else "could_read", 0)
    result = service.push(db, [original])["results"][0]
    assert result["status"] == "conflict"
    return original, result


def _choice(original, conflict):
    return _mutation("P1", original["field"], None, conflict["conflict_revision"],
        operation="keep_server", resolves_mutation_id=original["mutation_id"], comment=None)


def _changes(db):
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT * FROM sync_changes ORDER BY revision").fetchall()


@pytest.mark.parametrize("field", ["review_note", "verdict"])
def test_server_conflict_readout_can_exceed_outbound_limits(store, field):
    db, effects, transitions = store
    _original, result = _conflict(db, field)
    if field == "review_note":
        assert result["canonical"]["value"] == "Canonical server note " * 2500
        assert len(result["canonical"]["value"]) > 50_000
    else:
        assert result["canonical"]["comment"] == "Server rationale " * 800
        assert len(result["canonical"]["comment"]) > 10_000
    assert not effects and not transitions


def test_lost_online_ack_requires_comparison_without_reapplying_the_same_intent(store):
    db, effects, transitions = store
    repositories.insert_or_update_label_verdict(db, item_key="P1", user_priority="must_read",
        original_derived_priority="should_read", comment="Same intent")
    changes = _changes(db)
    retry = _mutation("P1", "verdict", "must_read", 0, comment="Same intent")
    conflict = service.push(db, [retry])["results"][0]
    assert conflict["status"] == "conflict"
    assert _changes(db) == changes
    choice = _choice(retry, conflict)
    assert service.push(db, [choice])["results"][0]["status"] == "applied"
    assert _changes(db) == changes
    assert not effects and not transitions


@pytest.mark.parametrize("field", ["verdict", "review_note"])
def test_keep_server_is_an_actual_scoped_noop_ack_without_echo_or_effects(store, field):
    db, effects, transitions = store
    original, conflict = _conflict(db, field)
    choice = _choice(original, conflict)
    wire = _PushRequest.model_validate({"protocol": 1, "mutations": [choice]}).model_dump(mode="json")
    before = repositories.sync_current_fields(db)[("P1", field)]
    changes = _changes(db)
    first = service.push(db, wire["mutations"])["results"][0]
    assert first["status"] == "applied" and first["canonical"] == conflict["canonical"]
    assert first["applied_revision"] == conflict["conflict_revision"]
    assert repositories.sync_current_fields(db)[("P1", field)] == before
    assert _changes(db) == changes
    repeat = service.push(db, wire["mutations"])["results"][0]
    assert repeat["status"] == "already_applied"
    assert not effects and not transitions
    with sqlite3.connect(db) as conn:
        request, result = conn.execute("SELECT request_json,result_json FROM sync_mutations WHERE mutation_id=?",
            (choice["mutation_id"],)).fetchone()
    assert json.loads(request)["value"] is None and json.loads(request)["comment"] is None
    assert json.loads(result)["status"] == "applied"


def test_keep_server_conflicts_again_when_the_compared_version_changed(store):
    db, effects, transitions = store
    original, conflict = _conflict(db, "review_note")
    _server_value(db, "review_note", "A newer server value")
    choice = _choice(original, conflict)
    result = service.push(db, [choice])["results"][0]
    assert result["status"] == "conflict"
    assert result["canonical"]["value"] == "A newer server value"
    assert repositories.get_review_note(db, "P1") == "A newer server value"
    assert not effects and not transitions


def test_keep_server_cannot_name_a_different_field_or_an_unseen_version(store):
    db, effects, transitions = store
    original, conflict = _conflict(db, "review_note")
    wrong = {**_choice(original, conflict), "field": "verdict"}
    assert service.push(db, [wrong])["results"][0]["status"] == "rejected"
    unseen = _choice(original, conflict)
    unseen["base_revision"] += 10
    assert service.push(db, [unseen])["results"][0]["status"] == "rejected"
    assert not effects and not transitions


@pytest.mark.parametrize("extra", [{"resolves_mutation_id": None}, {"value": "New content"}, {"comment": "New rationale"}])
def test_keep_server_wire_refuses_unbound_or_content_carrying_requests(store, extra):
    original, conflict = _conflict(store[0], "review_note")
    with pytest.raises(ValidationError):
        _PushRequest.model_validate({"protocol": 1, "mutations": [{**_choice(original, conflict), **extra}]})
