"""Audit publication failures preserve resumable state; JSON writers stage independently."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import Barrier

import pytest

from zotero_summarizer.services._common import write_json_atomic
from zotero_summarizer.services.golden import relabel_audit as audit


def _create(path):
    candidate = audit.AuditCandidate(
        item_key="PAPER", title="Résumé", authors="A", venue="V", abstract="Text",
        days_since_added=120, age_bucket="90-180", original_priority="should_read",
        original_inferred_relevance=4.0,
    )
    audit.write_session(path, [candidate], sample_size=1, seed=42)


@pytest.mark.parametrize("operation", ["create", "response", "trickle", "json"])
@pytest.mark.parametrize("boundary", ["write", "replace"])
def test_failed_publication_preserves_session(tmp_path, monkeypatch, operation, boundary):
    path = tmp_path / "audit.json"
    _create(path)
    before = path.read_bytes()
    operations = {
        "create": lambda: _create(path),
        "response": lambda: audit.record_response(path, "PAPER", "must_read"),
        "trickle": lambda: audit.next_audit_for_today(
            path, now=datetime(2026, 9, 5, tzinfo=timezone.utc),
        ),
        "json": lambda: write_json_atomic(path, {"title": "Résumé"}),
    }
    write_text = Path.write_text

    def fail_write(target, text, *args, **kwargs):
        write_text(target, text[:5], *args, **kwargs)
        raise OSError("injected disk failure")

    def fail_replace(*args, **kwargs):
        raise OSError("injected disk failure")

    with monkeypatch.context() as patch:
        if boundary == "write":
            patch.setattr(Path, "write_text", fail_write)
        else:
            patch.setattr(os, "replace", fail_replace)
        with pytest.raises(OSError, match="injected disk failure"):
            operations[operation]()
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]

    operations[operation]()
    saved = json.loads(path.read_text(encoding="utf-8"))
    if operation == "response":
        assert saved["responses"]["PAPER"]["new_priority"] == "must_read"
    elif operation == "trickle":
        assert saved["last_trickle_emitted_at"]
        assert saved["responses"] == {}
    elif operation == "create":
        assert saved["candidates"][0]["title"] == "Résumé"
    else:
        assert saved == {"title": "Résumé"}
    assert list(tmp_path.iterdir()) == [path]


def test_failed_initial_session_leaves_no_live_file(tmp_path, monkeypatch):
    path = tmp_path / "nested" / "audit.json"

    def fail_replace(*args, **kwargs):
        raise OSError("injected publication failure")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_replace)
        with pytest.raises(OSError, match="injected publication failure"):
            _create(path)
    assert list(path.parent.iterdir()) == []
    _create(path)
    assert audit.read_session(path)["sample_size"] == 1


def test_json_writers_use_independent_staging_files(tmp_path, monkeypatch):
    barrier = Barrier(2)
    target = tmp_path / "state.json"
    write_text = Path.write_text

    def overlapping_write(path, text, *args, **kwargs):
        result = write_text(path, text, *args, **kwargs)
        barrier.wait(timeout=5)
        assert path.read_text(encoding="utf-8") == text
        return result

    monkeypatch.setattr(Path, "write_text", overlapping_write)
    payloads = [{"value": "one"}, {"value": "two"}]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda payload: write_json_atomic(target, payload), payloads))
    assert json.loads(target.read_text()) in payloads
    assert list(tmp_path.iterdir()) == [target]
