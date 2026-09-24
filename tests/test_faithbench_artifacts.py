"""Frozen benchmark artifacts must survive rebuilds and interrupted publication."""
import pytest

from zotero_summarizer.services.faithbench._corpus import (
    freeze_paper_text,
    load_frozen_text,
    paper_text_path,
    sha256_text,
)


@pytest.mark.parametrize("key", ["../escape", "/absolute", "A/B", "A\\B", "..", ""])
def test_paper_keys_cannot_escape_the_corpus(tmp_path, key):
    papers = tmp_path / "papers"
    with pytest.raises(ValueError):
        freeze_paper_text(papers, key, "untrusted text")
    assert not list(tmp_path.rglob("*.txt"))
    with pytest.raises(ValueError):
        load_frozen_text(papers, key, expected_sha256=sha256_text("untrusted text"))


def test_rebuild_preserves_both_frozen_versions(tmp_path):
    first = freeze_paper_text(tmp_path, "P1", "first extraction")
    second = freeze_paper_text(tmp_path, "P1", "second extraction")
    assert first != second
    assert load_frozen_text(tmp_path, "P1", expected_sha256=first) == "first extraction"
    assert load_frozen_text(tmp_path, "P1", expected_sha256=second) == "second extraction"
    before = {path.name: path.stat().st_mtime_ns for path in tmp_path.iterdir()}
    assert freeze_paper_text(tmp_path, "P1", "first extraction") == first
    assert {path.name: path.stat().st_mtime_ns for path in tmp_path.iterdir()} == before


def test_legacy_frozen_text_remains_readable_without_rewriting(tmp_path):
    legacy = tmp_path / "P1.txt"
    legacy.write_text("legacy extraction", encoding="utf-8")
    old_sha = sha256_text("legacy extraction")
    new_sha = freeze_paper_text(tmp_path, "P1", "new extraction")
    assert legacy.read_text(encoding="utf-8") == "legacy extraction"
    assert load_frozen_text(tmp_path, "P1", expected_sha256=old_sha) == "legacy extraction"
    assert load_frozen_text(tmp_path, "P1", expected_sha256=new_sha) == "new extraction"


def test_frozen_path_rejects_symlink_escape(tmp_path):
    papers = tmp_path / "papers"
    papers.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private text", encoding="utf-8")
    (papers / "P1.txt").symlink_to(outside)
    with pytest.raises(ValueError):
        paper_text_path(papers, "P1")
    with pytest.raises(ValueError):
        load_frozen_text(papers, "P1", expected_sha256=sha256_text("private text"))
    assert outside.read_text(encoding="utf-8") == "private text"


def test_failed_freeze_does_not_publish_or_damage_old_version(monkeypatch, tmp_path):
    from zotero_summarizer.services import _common

    old_sha = freeze_paper_text(tmp_path, "P1", "old text")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    def interrupted_publish(*args):
        raise OSError("injected publication failure")

    monkeypatch.setattr(_common.os, "replace", interrupted_publish)
    with pytest.raises(OSError, match="injected publication failure"):
        freeze_paper_text(tmp_path, "P1", "new text")
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
    assert load_frozen_text(tmp_path, "P1", expected_sha256=old_sha) == "old text"


def test_run_ids_stay_under_the_selected_run_root(tmp_path):
    from types import SimpleNamespace

    from zotero_summarizer.cli._faithbench import _run_paths

    settings = SimpleNamespace(faithbench_dir=tmp_path / "faithbench")
    for run_id in ("../escape", "/absolute", "A/B", "A\\B", ".", "..", ""):
        with pytest.raises(ValueError):
            _run_paths(settings, run_id)
    safe = _run_paths(settings, "20260906_test-run")
    assert safe.run_dir == settings.faithbench_dir / "runs" / "20260906_test-run"
    safe.run_dir.parent.mkdir(parents=True)
    (safe.run_dir.parent / "escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        _run_paths(settings, "escape")


def test_corrupt_hashed_text_does_not_fall_back_to_legacy(tmp_path):
    sha = freeze_paper_text(tmp_path, "P1", "original")
    (tmp_path / "P1.txt").write_text("original", encoding="utf-8")
    path = paper_text_path(tmp_path, "P1", text_sha256=sha)
    path.write_text("corruption", encoding="utf-8")
    with pytest.raises(ValueError, match="frozen text drift"):
        load_frozen_text(tmp_path, "P1", expected_sha256=sha)
    with pytest.raises(ValueError, match="frozen text drift"):
        freeze_paper_text(tmp_path, "P1", "original")
    assert path.read_text(encoding="utf-8") == "corruption"
