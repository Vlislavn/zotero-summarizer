"""Tests for the append-only classifier run log (FAIR provenance)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zotero_summarizer.services import run_log


def test_make_run_id_includes_classifier_and_utc_timestamp():
    rid = run_log.make_run_id("tabpfn")
    assert rid.endswith("_tabpfn")
    # Format YYYYMMDD_HHMMSS_<name>
    head, _ = rid.rsplit("_", 1)
    assert len(head) == 15  # 8 digits + underscore + 6 digits
    assert "_" in head


def test_append_and_load_preserves_order(tmp_path: Path):
    log_path = tmp_path / "runs.jsonl"
    run_log.append_run(log_path, {"run_id": "20260101_000000_a", "classifier": "a", "auc": 0.5})
    run_log.append_run(log_path, {"run_id": "20260102_000000_b", "classifier": "b", "auc": 0.7})
    loaded = run_log.load_runs(log_path)
    assert len(loaded) == 2
    assert loaded[0]["classifier"] == "a"
    assert loaded[1]["classifier"] == "b"


def test_load_runs_skips_malformed_lines(tmp_path: Path):
    log_path = tmp_path / "runs.jsonl"
    log_path.write_text(
        '{"run_id": "ok", "classifier": "x"}\n'
        'not json at all\n'
        '\n'
        '{"run_id": "ok2", "classifier": "y"}\n',
        encoding="utf-8",
    )
    loaded = run_log.load_runs(log_path)
    assert len(loaded) == 2


def test_load_runs_returns_empty_for_missing_file(tmp_path: Path):
    assert run_log.load_runs(tmp_path / "absent.jsonl") == []


def test_latest_per_classifier_picks_newest_by_run_id(tmp_path: Path):
    runs = [
        {"run_id": "20260101_000000_tabpfn", "classifier": "tabpfn", "auc": 0.5},
        {"run_id": "20260102_000000_tabpfn", "classifier": "tabpfn", "auc": 0.8},
        {"run_id": "20260105_120000_lightgbm", "classifier": "lightgbm", "auc": 0.7},
    ]
    latest = run_log.latest_per_classifier(runs)
    assert latest["tabpfn"]["auc"] == 0.8
    assert latest["lightgbm"]["auc"] == 0.7
    assert set(latest) == {"tabpfn", "lightgbm"}


def test_file_sha256_is_stable_for_same_content(tmp_path: Path):
    p = tmp_path / "in.csv"
    p.write_text("hello\n")
    h1 = run_log.file_sha256(p)
    h2 = run_log.file_sha256(p)
    assert h1 == h2
    assert len(h1) == 12

    p.write_text("hello\nmodified\n")
    h3 = run_log.file_sha256(p)
    assert h3 != h1


def test_file_sha256_returns_empty_for_missing_path(tmp_path: Path):
    assert run_log.file_sha256(tmp_path / "absent") == ""


def test_short_git_commit_returns_string_or_empty():
    """Inside a repo this should return a short hash; outside, empty. Either is fine."""
    commit = run_log.short_git_commit()
    # We don't care which, as long as it's a string and doesn't crash.
    assert isinstance(commit, str)
