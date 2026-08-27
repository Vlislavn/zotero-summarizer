from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from zotero_summarizer.api.routes.sync import _PushRequest
from zotero_summarizer.services.golden import label_verdicts
from zotero_summarizer.services.sync import service
from zotero_summarizer.storage import repositories
from zotero_summarizer.storage.migrations import TRIAGE_MIGRATIONS, run_migrations


def _db(tmp_path):
    path = tmp_path / "triage.db"
    run_migrations(path, "triage", TRIAGE_MIGRATIONS)
    return path


def _mutation(item_key, field, value, base, **extra):
    return {
        "mutation_id": str(uuid4()), "device_id": "ipad-1", "item_key": item_key,
        "field": field, "operation": "set", "value": value, "comment": "",
        "model_priority": "should_read", "base_revision": base,
        "resolves_mutation_id": None,
        "created_at": datetime.now(timezone.utc).isoformat(), **extra,
    }


def _set_server_verdict(db, value):
    repositories.insert_or_update_label_verdict(
        db, item_key="P1", original_derived_priority="should_read",
        user_priority=value, comment="server",
    )


def test_ordered_offline_changes_merge_other_fields_and_replay_once(tmp_path, monkeypatch):
    db = _db(tmp_path)
    _set_server_verdict(db, "should_read")
    base = repositories.sync_current_fields(db)[("P1", "verdict")]["revision"]
    events = []
    monkeypatch.setattr(label_verdicts.interaction_log, "_log_label_transition", lambda **kw: events.append(kw))
    first = _mutation("P1", "verdict", "could_read", base)
    second = _mutation("P1", "verdict", "dont_read", base)

    applied = service.push(db, [first, second])

    assert [row["status"] for row in applied["results"]] == ["applied", "applied"]
    assert repositories.get_label_verdict(db, "P1")["user_priority"] == "dont_read"
    assert [(row["previous_user_priority"], row["new_user_priority"]) for row in events] == [
        ("should_read", "could_read"), ("could_read", "dont_read"),
    ]
    change_count = applied["cursor"]

    replay = service.push(db, [first, second])

    assert [row["status"] for row in replay["results"]] == [
        "already_applied", "already_applied",
    ]
    assert replay["cursor"] == change_count
    assert len(events) == 2

    note = _mutation("P1", "review_note", "offline note", 0)
    merged = service.push(db, [note])
    assert merged["results"][0]["status"] == "applied"
    assert repositories.get_review_note(db, "P1") == "offline note"
    assert repositories.get_label_verdict(db, "P1")["user_priority"] == "dont_read"


def test_same_field_conflict_and_resolution_are_explicit_and_audited(tmp_path, monkeypatch):
    db = _db(tmp_path)
    _set_server_verdict(db, "should_read")
    offline_base = repositories.sync_current_fields(db)[("P1", "verdict")]["revision"]
    _set_server_verdict(db, "must_read")
    events = []
    monkeypatch.setattr(label_verdicts.interaction_log, "_log_label_transition", lambda **kw: events.append(kw))
    offline = _mutation("P1", "verdict", "dont_read", offline_base)

    conflict = service.push(db, [offline])["results"][0]

    assert conflict["status"] == "conflict"
    assert conflict["canonical"]["value"] == "must_read"
    assert repositories.get_label_verdict(db, "P1")["user_priority"] == "must_read"
    resolution = _mutation(
        "P1", "verdict", "dont_read", conflict["conflict_revision"],
        resolves_mutation_id=offline["mutation_id"],
    )

    resolved = service.push(db, [resolution])["results"][0]

    assert resolved["status"] == "applied"
    assert events[0]["source"] == "sync_conflict_resolution"
    conn = sqlite3.connect(str(db))
    try:
        audit = conn.execute(
            "SELECT resolves_mutation_id, result_json FROM sync_mutations WHERE mutation_id = ?",
            (resolution["mutation_id"],),
        ).fetchone()
    finally:
        conn.close()
    assert audit[0] == offline["mutation_id"]
    rejected = service.push(db, [_mutation("P1", "verdict", "urgent", resolved["applied_revision"])])
    assert rejected["results"][0]["status"] == "rejected"


def test_pull_has_resumable_cursor_and_compact_offline_context(tmp_path, monkeypatch):
    db = _db(tmp_path)
    _set_server_verdict(db, "should_read")
    repositories.upsert_review_note(db, "P1", "my note")
    monkeypatch.setattr(service.reading_queue, "build_reading_queue", lambda **_kw: {
        "items": [{"item_key": "P1", "title": "Paper", "authors": ["A"],
                   "year": "2026", "abstract_preview": "abstract", "reading_priority": "should_read"}],
    })
    monkeypatch.setattr(service.deep_review, "_read_all", lambda: {
        "P1": {"digest": {"tldr": "short digest", "key_findings": ["one"]}},
    })

    initial = service.pull(db, 0)
    paper = initial["papers"][0]
    assert initial["protocol"] == 1
    assert paper["verdict"]["user_priority"] == "should_read"
    assert paper["review_note"] == "my note"
    assert paper["review_digest"]["tldr"] == "short digest"
    repositories.upsert_review_note(db, "P1", "new note")

    delta = service.pull(db, initial["cursor"])
    assert [(row["field"], row["value"]) for row in delta["changes"]] == [
        ("review_note", "new note"),
    ]
    assert service.pull(db, delta["cursor"])["changes"] == []


def test_sync_protocol_is_required():
    with pytest.raises(ValidationError):
        _PushRequest(mutations=[])
