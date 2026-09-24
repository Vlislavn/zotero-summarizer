"""Focused snapshot-history and actionable-conflict status contracts."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from zotero_summarizer.services.sync import service
from zotero_summarizer.storage import repositories
from zotero_summarizer.storage.migrations import TRIAGE_MIGRATIONS, run_migrations


def _database(tmp_path: Path) -> Path:
    path = tmp_path / "triage.db"
    run_migrations(path, "triage", TRIAGE_MIGRATIONS)
    return path


def _mutation(mutation_id: str, value: str, base: int, **extra):
    return {
        "mutation_id": mutation_id,
        "device_id": "device-a",
        "item_key": "PAPER001",
        "field": "verdict",
        "operation": "set",
        "value": value,
        "comment": "",
        "model_priority": "should_read",
        "base_revision": base,
        "resolves_mutation_id": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }


def test_snapshot_pull_returns_cursor_without_historical_note_deltas(tmp_path, monkeypatch):
    db = _database(tmp_path)
    for index in range(30):
        repositories.upsert_review_note(db, "PAPER001", f"old revision {index}: " + "x" * 3000)
    current_note = "current note " + "y" * 40000
    repositories.upsert_review_note(db, "PAPER001", current_note)
    monkeypatch.setattr(
        service.reading_queue, "build_reading_queue", lambda **_kw: {"items": []},
    )
    monkeypatch.setattr(service.deep_review, "current_reviews", lambda: {})

    payload = service.pull(db, 0)

    assert payload["cursor"] == repositories.sync_status(db)["cursor"]
    assert payload["changes"] == []
    assert payload["papers"][0]["review_note"] == current_note
    assert len(json.dumps(payload)) < 50000


def test_sync_status_counts_only_unresolved_conflicts(tmp_path):
    db = _database(tmp_path)
    applied = repositories.apply_sync_mutation(
        db, _mutation("initial", "must_read", 0),
    )
    conflict = repositories.apply_sync_mutation(
        db, _mutation("conflict", "could_read", 0),
    )
    assert applied["status"] == "applied"
    assert conflict["status"] == "conflict"
    assert service.status(db)["conflicts"] == 1

    resolved = repositories.apply_sync_mutation(
        db,
        _mutation(
            "resolution", "could_read", conflict["conflict_revision"],
            resolves_mutation_id="conflict",
        ),
    )

    assert resolved["status"] == "applied"
    assert service.status(db)["conflicts"] == 0
