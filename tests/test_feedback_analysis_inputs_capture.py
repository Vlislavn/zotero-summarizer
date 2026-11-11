"""Controlled concurrent synthetic writers exercise EOF and mutation boundaries."""
from __future__ import annotations

import os
from threading import Event, Thread

import pytest

from test_feedback_analysis_inputs import create_current_db, freeze, make_settings
from zotero_summarizer.services.golden.feedback_analysis import inputs, verify_inputs


def intervene_after_first_read(monkeypatch, path, action):
    inode = path.stat().st_ino
    original = inputs._read_prefix
    called = False

    def intervene(handle, size, guard, output=None):
        nonlocal called
        digest = original(handle, size, guard, output)
        if not called and os.fstat(handle.fileno()).st_ino == inode:
            called = True
            action()
        return digest

    monkeypatch.setattr(inputs, "_read_prefix", intervene)


def test_atomic_csv_replacement_retains_complete_opened_old_snapshot(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    create_current_db(settings)
    settings.golden_csv_path.write_bytes(b"item_key,notes\nOLD,fixture\n")
    replacement = settings.data_dir / "replacement.csv"
    replacement.write_bytes(b"item_key,notes\nNEW,fixture\n")
    intervene_after_first_read(monkeypatch, settings.golden_csv_path,
                              lambda: replacement.replace(settings.golden_csv_path))

    manifest = freeze(settings)

    bundle = settings.feedback_analysis_dir / "staging"
    assert (bundle / "inputs/golden.csv").read_bytes() == b"item_key,notes\nOLD,fixture\n"
    assert settings.golden_csv_path.read_bytes() == b"item_key,notes\nNEW,fixture\n"
    assert manifest["alignment"]["golden_csv_history"] == "unknown"
    assert verify_inputs(bundle) == manifest


def test_concurrent_append_stops_at_declared_eof_prefix(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    create_current_db(settings)
    settings.interaction_log_path.write_bytes(b"first\n{torn")
    append_allowed, appended = Event(), Event()

    def writer():
        assert append_allowed.wait(5)
        with settings.interaction_log_path.open("ab") as handle:
            handle.write(b"-completed}\nnext\n")
        appended.set()

    def allow_append():
        append_allowed.set()
        assert appended.wait(5)

    thread = Thread(target=writer)
    thread.start()
    intervene_after_first_read(monkeypatch, settings.interaction_log_path, allow_append)
    try:
        manifest = freeze(settings)
    finally:
        append_allowed.set()
        thread.join(5)
    assert not thread.is_alive()
    bundle = settings.feedback_analysis_dir / "staging"
    assert (bundle / "inputs/interaction-events.jsonl").read_bytes() == b"first\n{torn"
    assert manifest["sources"]["interaction_log"]["capture"]["eof_offset"] == len(b"first\n{torn")
    assert settings.interaction_log_path.read_bytes() == b"first\n{torn-completed}\nnext\n"
    assert verify_inputs(bundle) == manifest


@pytest.mark.parametrize("source_name", ["golden_csv_path", "interaction_log_path", "classifier_runs_log_path"])
@pytest.mark.parametrize("mutation", ["same_size", "truncate", "prefix_then_append"])
def test_in_place_mutation_is_not_a_successful_mixed_capture(tmp_path, monkeypatch, source_name, mutation):
    settings = make_settings(tmp_path)
    create_current_db(settings)
    source = getattr(settings, source_name)
    source.write_bytes(b"original\n")
    changed = {"same_size": b"modified\n", "truncate": b"short", "prefix_then_append": b"modified\nappended\n"}
    intervene_after_first_read(monkeypatch, source, lambda: source.write_bytes(changed[mutation]))

    with pytest.raises(ValueError):
        freeze(settings)

    assert source.read_bytes() == changed[mutation]
    assert not (settings.feedback_analysis_dir / "staging").exists()
