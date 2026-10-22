"""Synthetic acceptance of the frozen-input boundary; no installed-host data."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest

from zotero_summarizer.services.golden.feedback_analysis import freeze_inputs, verify_inputs
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import migrations, repositories

CUTOFF = datetime(2026, 10, 8, tzinfo=timezone.utc)
PROVENANCE = {
    "code": {"commit": "synthetic"}, "config": {"sha256": "declared"},
    "environment": {"python": "test"}, "seed": 42,
    "inference_identity": {"status": "unknown"},
}


def make_settings(tmp_path):
    return Settings.load(project_root=tmp_path)


def create_current_db(settings):
    migrations.run_migrations(settings.triage_db_path, "triage", migrations.TRIAGE_MIGRATIONS)


def freeze(settings, name="staging", **kwargs):
    return freeze_inputs(settings, settings.feedback_analysis_dir / name,
                         cutoff=CUTOFF, provenance=PROVENANCE, **kwargs)


def file_receipt(path):
    return (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mode)


@pytest.mark.parametrize("populated", [False, True])
def test_current_numbered_store_is_admitted_and_replays_without_live_sources(tmp_path, populated):
    settings = make_settings(tmp_path)
    create_current_db(settings)
    with closing(sqlite3.connect(settings.triage_db_path)) as live:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("PRAGMA wal_autocheckpoint=0")
        main_before_commit = file_receipt(settings.triage_db_path)
        if populated:
            live.execute("INSERT INTO label_verdicts(item_key, original_derived_priority, user_priority, "
                         "comment, created_at) VALUES ('synthetic', 'unknown', 'should_read', '', '2026-10-08')")
            live.commit()
        settings.golden_csv_path.write_bytes(b"item_key,notes\nsynthetic,fixture\n")
        settings.interaction_log_path.write_bytes(b'{"event":"fixture"}\n{"torn":')
        settings.classifier_runs_log_path.write_bytes(b'{"run_id":"fixture"}\n')
        sources = [settings.triage_db_path, settings.golden_csv_path, settings.interaction_log_path,
                   settings.classifier_runs_log_path]
        wal = settings.triage_db_path.with_name(settings.triage_db_path.name + "-wal")
        if populated:
            assert wal.stat().st_size > 32
            assert file_receipt(settings.triage_db_path) == main_before_commit
        if wal.exists():
            sources.append(wal)
        before = {path: file_receipt(path) for path in sources}
        schema = live.execute("SELECT * FROM sqlite_master").fetchall()

        manifest = freeze(settings)

        assert {path: file_receipt(path) for path in sources} == before
        assert live.execute("SELECT * FROM sqlite_master").fetchall() == schema
        assert live.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        metadata = manifest["sources"]["triage_db"]["sqlite"]
        assert metadata["integrity_check"] == "ok"
        assert metadata["numbered_schema_versions"] == {
            "status": "available", "values": {"triage": migrations.SCHEMA_VERSION}}
        assert metadata["sync_changes"] == {
            "status": "available", "min_revision": 1 if populated else None,
            "max_revision": 1 if populated else None}
        bundle = settings.feedback_analysis_dir / "staging"
        retained = bundle / manifest["sources"]["triage_db"]["path"]
        with closing(sqlite3.connect(retained)) as snapshot:
            assert snapshot.execute("SELECT COUNT(*) FROM label_verdicts").fetchone()[0] == int(populated)
        assert (bundle / "inputs/interaction-events.jsonl").read_bytes() == b'{"event":"fixture"}\n{"torn":'
        assert manifest["cutoff"] == "2026-10-08T00:00:00Z"
        assert manifest["provenance"] == PROVENANCE
        assert manifest["alignment"]["cross_store_atomic"] is False
        again = freeze(settings, "second")
        assert again["content_digest"] == manifest["content_digest"]
        assert again["sources"]["triage_db"]["sha256"] == manifest["sources"]["triage_db"]["sha256"]

    for path in sources:
        path.unlink(missing_ok=True)
    assert verify_inputs(bundle) == manifest
    assert len(manifest["content_digest"]) == 64
    assert settings.feedback_analysis_dir == settings.data_dir / "feedback-analysis"
    assert settings.classifier_runs_log_path == settings.data_dir / "classifier-runs.jsonl"
    assert {p.name for p in bundle.iterdir()} == {"inputs", "input-manifest.json"}
    assert all(p.stat().st_mode & 0o077 == 0 for p in bundle.rglob("*"))


def test_old_store_records_unavailable_history_and_missing_optional_inputs(tmp_path):
    settings = make_settings(tmp_path)
    settings.data_dir.mkdir()
    with closing(sqlite3.connect(settings.triage_db_path)) as conn:
        repositories.apply_schema(conn)
        conn.commit()

    manifest = freeze(settings)

    metadata = manifest["sources"]["triage_db"]["sqlite"]
    assert metadata["numbered_schema_versions"] == {"status": "unavailable", "values": None}
    assert metadata["sync_changes"] == {"status": "unavailable", "min_revision": None, "max_revision": None}
    for name in ("golden_csv", "interaction_log", "classifier_runs_log"):
        source = manifest["sources"][name]
        assert (source["status"], source["path"], source["sha256"], source["byte_size"]) == (
            "missing", None, None, 0)
    assert verify_inputs(settings.feedback_analysis_dir / "staging") == manifest


@pytest.mark.parametrize("problem", ["missing", "corrupt", "wrong_schema"])
def test_invalid_mandatory_db_never_publishes_manifest(tmp_path, problem):
    settings = make_settings(tmp_path)
    settings.data_dir.mkdir()
    if problem == "corrupt":
        settings.triage_db_path.write_bytes(b"not sqlite")
    elif problem == "wrong_schema":
        with closing(sqlite3.connect(settings.triage_db_path)) as conn:
            conn.execute("CREATE TABLE irrelevant (id INTEGER)")
    existed = settings.triage_db_path.exists()

    with pytest.raises((FileNotFoundError, sqlite3.DatabaseError, ValueError)):
        freeze(settings)

    assert settings.triage_db_path.exists() is existed
    assert not (settings.feedback_analysis_dir / "staging").exists()


def test_existing_bundle_is_never_overwritten(tmp_path):
    settings = make_settings(tmp_path)
    create_current_db(settings)
    manifest = freeze(settings)
    bundle = settings.feedback_analysis_dir / "staging"
    before = {p.relative_to(bundle): p.read_bytes() for p in bundle.rglob("*") if p.is_file()}

    with pytest.raises(FileExistsError):
        freeze(settings)

    assert {p.relative_to(bundle): p.read_bytes() for p in bundle.rglob("*") if p.is_file()} == before
    assert verify_inputs(bundle) == manifest


@pytest.mark.parametrize("change", ["bytes", "deleted", "escape", "symlink", "digest", "partial"])
def test_verification_refuses_tampering_and_partial_bundles(tmp_path, change):
    settings = make_settings(tmp_path)
    create_current_db(settings)
    settings.interaction_log_path.write_bytes(b"fixture\n")
    manifest = freeze(settings)
    bundle = settings.feedback_analysis_dir / "staging"
    retained = bundle / manifest["sources"]["interaction_log"]["path"]
    if change == "bytes":
        retained.write_bytes(b"changed\n")
    elif change == "deleted":
        retained.unlink()
    elif change == "symlink":
        retained.unlink()
        retained.symlink_to(settings.interaction_log_path)
    elif change == "escape":
        manifest["sources"]["interaction_log"]["path"] = "../../interaction-events.jsonl"
    elif change == "digest":
        manifest["content_digest"] = "0" * 64
    else:
        (bundle / "input-manifest.json").unlink()
    if change in {"escape", "digest"}:
        (bundle / "input-manifest.json").write_text(json.dumps(manifest))

    with pytest.raises((ValueError, FileNotFoundError)):
        verify_inputs(bundle)
