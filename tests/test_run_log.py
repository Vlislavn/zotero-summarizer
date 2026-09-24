"""Tests for the append-only classifier run log (FAIR provenance)."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from zotero_summarizer.services import run_log


def test_make_run_id_includes_classifier_and_utc_timestamp():
    rid = run_log.make_run_id("tabpfn")
    assert rid.endswith("_tabpfn")
    stamp, suffix = rid[:15], rid[16:]
    assert datetime.strptime(stamp, "%Y%m%d_%H%M%S")
    token, classifier = suffix.split("_", 1)
    assert len(token) == 32 and int(token, 16) >= 0
    assert classifier == "tabpfn"


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


def test_latest_per_classifier_picks_last_record(tmp_path: Path):
    runs = [
        {"run_id": "20260101_000000_tabpfn", "classifier": "tabpfn", "auc": 0.5},
        {"run_id": "20260102_000000_tabpfn", "classifier": "tabpfn", "auc": 0.8},
        {"run_id": "20260105_120000_lightgbm", "classifier": "lightgbm", "auc": 0.7},
    ]
    latest = run_log.latest_per_classifier(runs)
    assert latest["tabpfn"]["auc"] == 0.8
    assert latest["lightgbm"]["auc"] == 0.7
    assert set(latest) == {"tabpfn", "lightgbm"}


def test_run_ids_do_not_collide_with_a_frozen_clock():
    frozen = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: run_log.make_run_id("logreg", now=frozen), range(100)))
    assert len(set(ids)) == 100


def test_latest_run_is_last_append_not_largest_identifier():
    earlier = {"run_id": "z", "classifier": "logreg", "auc": 0.5}
    later = {"run_id": "a", "classifier": "logreg", "auc": 0.8}
    assert run_log.latest_per_classifier([earlier, later])["logreg"] is later


def test_same_second_cli_reports_preserve_both_runs(tmp_path):
    from zotero_summarizer.cli._helpers import _persist_run_log
    from zotero_summarizer.settings import Settings

    settings = Settings.load(project_root=tmp_path)
    frozen = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    entries = []
    for auc in (0.5, 0.8):
        entry = {
            "run_id": run_log.make_run_id("logreg", now=frozen),
            "classifier": "logreg", "timestamp": frozen.isoformat(),
            "input_csv": "fixture.csv", "cv": {"auc": auc},
        }
        _persist_run_log(settings, entry)
        entries.append(entry)
    assert entries[0]["report_path"] != entries[1]["report_path"]
    assert "0.5" in Path(entries[0]["report_path"]).read_text()
    assert "0.8" in Path(entries[1]["report_path"]).read_text()
    assert len(run_log.load_runs(settings.data_dir / "classifier-runs.jsonl")) == 2


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
