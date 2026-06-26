"""Zotero data-dir detection: candidates returned, db_exists ordering, never
writes. Detection is a read-only probe behind GET /api/setup/detect-zotero."""
from __future__ import annotations

from pathlib import Path

from zotero_summarizer.runtime import AppContext, set_context
from zotero_summarizer.services.setup import detect_zotero_data_dirs
from zotero_summarizer.services.setup import detect as detect_mod
from zotero_summarizer.settings import Settings


def _seed_settings(tmp_path: Path) -> Settings:
    settings = Settings.load(project_root=tmp_path)
    set_context(AppContext(settings=settings))
    return settings


def test_includes_configured_dir_tagged_env(tmp_path: Path, monkeypatch):
    # Point the configured Zotero dir at a tmp dir with a real DB.
    zot = tmp_path / "MyZotero"
    zot.mkdir()
    (zot / "zotero.sqlite").write_bytes(b"")
    (zot / "storage").mkdir()
    monkeypatch.setenv("ZOTERO_DATA_DIR", str(zot))
    _seed_settings(tmp_path)
    # Avoid scanning the real home dir for the platform probe.
    monkeypatch.setattr(detect_mod, "_platform_candidate_dirs", lambda: [])

    rows = detect_zotero_data_dirs()
    env_rows = [r for r in rows if r.source == "env"]
    assert len(env_rows) == 1
    assert env_rows[0].data_dir == str(zot)
    assert env_rows[0].db_exists is True
    assert env_rows[0].storage_exists is True
    assert env_rows[0].db_path == str(zot / "zotero.sqlite")


def test_db_exists_candidates_are_ordered_first(tmp_path: Path, monkeypatch):
    # Configured dir has NO db; a probe candidate DOES — the probe must sort first.
    no_db = tmp_path / "empty"
    no_db.mkdir()
    monkeypatch.setenv("ZOTERO_DATA_DIR", str(no_db))
    _seed_settings(tmp_path)

    with_db = tmp_path / "real"
    with_db.mkdir()
    (with_db / "zotero.sqlite").write_bytes(b"")
    monkeypatch.setattr(detect_mod, "_platform_candidate_dirs", lambda: [with_db])

    rows = detect_zotero_data_dirs()
    assert rows[0].db_exists is True
    assert rows[0].data_dir == str(with_db)
    # The no-db configured dir is present but ordered after.
    assert any(r.data_dir == str(no_db) and not r.db_exists for r in rows)


def test_dedup_keeps_env_when_probe_collides(tmp_path: Path, monkeypatch):
    zot = tmp_path / "Zotero"
    zot.mkdir()
    (zot / "zotero.sqlite").write_bytes(b"")
    monkeypatch.setenv("ZOTERO_DATA_DIR", str(zot))
    _seed_settings(tmp_path)
    # Probe yields the SAME dir — it must not produce a duplicate row, and the
    # surviving row keeps source="env" (the user's explicit configuration).
    monkeypatch.setattr(detect_mod, "_platform_candidate_dirs", lambda: [zot])

    rows = detect_zotero_data_dirs()
    matching = [r for r in rows if r.data_dir == str(zot)]
    assert len(matching) == 1
    assert matching[0].source == "env"


def test_detect_never_writes(tmp_path: Path, monkeypatch):
    zot = tmp_path / "Zotero"
    zot.mkdir()
    monkeypatch.setenv("ZOTERO_DATA_DIR", str(zot))
    _seed_settings(tmp_path)
    monkeypatch.setattr(detect_mod, "_platform_candidate_dirs", lambda: [tmp_path / "nope"])

    before = sorted(p.name for p in tmp_path.rglob("*"))
    detect_zotero_data_dirs()
    after = sorted(p.name for p in tmp_path.rglob("*"))
    assert before == after  # no file/dir created by detection
