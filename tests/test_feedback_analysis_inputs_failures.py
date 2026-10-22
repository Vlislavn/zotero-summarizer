"""Admission, work budgets and verification fail closed without touching sources."""
from __future__ import annotations

import errno
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_feedback_analysis_inputs import CUTOFF, PROVENANCE, create_current_db, file_receipt, freeze, make_settings
from zotero_summarizer.services.golden.feedback_analysis import BudgetExceeded, freeze_inputs, inputs, verify_inputs
from zotero_summarizer.services.golden.feedback_analysis.provenance import digest_manifest


@pytest.mark.parametrize("second_version", [1, 2])
def test_duplicate_numbered_schema_namespaces_fail_without_changing_sources(tmp_path, second_version):
    import sqlite3
    from contextlib import closing

    settings = make_settings(tmp_path)
    create_current_db(settings)
    with closing(sqlite3.connect(settings.triage_db_path)) as conn:
        conn.execute("DROP TABLE schema_migrations")
        conn.execute("CREATE TABLE schema_migrations(namespace TEXT, version INTEGER)")
        conn.executemany("INSERT INTO schema_migrations VALUES (?, ?)", [("triage", 1), ("triage", second_version)])
        conn.commit()
    before = file_receipt(settings.triage_db_path)

    with pytest.raises(ValueError, match="duplicate numbered schema namespace"):
        freeze(settings)

    assert file_receipt(settings.triage_db_path) == before
    assert not (settings.feedback_analysis_dir / "staging").exists()


@pytest.mark.parametrize("budgets", [
    {"max_source_bytes": 1}, {"max_snapshot_pages": 1},
    {"backup_pages_per_step": 1, "max_progress_callbacks": 1},
])
def test_work_budget_exhaustion_does_not_publish_or_change_source(tmp_path, budgets):
    settings = make_settings(tmp_path)
    create_current_db(settings)
    before = file_receipt(settings.triage_db_path)

    with pytest.raises(BudgetExceeded):
        freeze(settings, budgets=budgets)

    assert file_receipt(settings.triage_db_path) == before
    assert not (settings.feedback_analysis_dir / "staging").exists()


def test_optional_source_byte_budget_enforced_before_capture(tmp_path):
    settings = make_settings(tmp_path)
    create_current_db(settings)
    size = settings.triage_db_path.stat().st_size
    settings.interaction_log_path.write_bytes(b"x" * (size + 1))

    with pytest.raises(BudgetExceeded):
        freeze(settings, budgets={"max_source_bytes": size})

    assert not (settings.feedback_analysis_dir / "staging").exists()


def test_wall_cap_exhaustion_during_capture_removes_staging(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    create_current_db(settings)
    settings.interaction_log_path.write_bytes(b"synthetic\n")
    real_capture = inputs._capture_file

    def expire(name, source, target, guard):
        guard.deadline = 0
        return real_capture(name, source, target, guard)

    monkeypatch.setattr(inputs, "_capture_file", expire)
    with pytest.raises(BudgetExceeded):
        freeze(settings)
    assert not (settings.feedback_analysis_dir / "staging").exists()


@pytest.mark.parametrize("raw", ["bad", "nan", "inf", "0", "-1", "601"])
def test_malformed_or_out_of_cap_environment_is_not_ignored(tmp_path, monkeypatch, raw):
    settings = make_settings(tmp_path)
    monkeypatch.setenv(inputs.WALL_CAP_ENV, raw)
    with pytest.raises(ValueError):
        freeze(settings, budgets={"wall_cap_seconds": 10})
    assert not settings.feedback_analysis_dir.exists()


@pytest.mark.parametrize("value", [{"unknown": 1}, {"max_source_bytes": True}, {"backup_pages_per_step": 0},
                                  {"max_snapshot_pages": inputs.MAX_SNAPSHOT_PAGES + 1}, []])
def test_invalid_budget_config_fails_before_io(tmp_path, value):
    settings = make_settings(tmp_path)
    with pytest.raises(ValueError):
        freeze(settings, budgets=value)
    assert not settings.feedback_analysis_dir.exists()


@pytest.mark.parametrize("which", ["cutoff_naive", "cutoff_non_utc", "provenance_missing", "provenance_non_json"])
def test_invalid_declared_identity_fails_before_io(tmp_path, which):
    settings = make_settings(tmp_path)
    cutoff, provenance = CUTOFF, PROVENANCE
    if which == "cutoff_naive":
        cutoff = datetime(2026, 10, 8)
    elif which == "cutoff_non_utc":
        cutoff = datetime(2026, 10, 8, tzinfo=timezone(timedelta(hours=1)))
    elif which == "provenance_missing":
        provenance = {"code": "synthetic"}
    else:
        provenance = PROVENANCE | {"bad": object()}
    with pytest.raises(ValueError):
        freeze_inputs(settings, settings.feedback_analysis_dir / "staging", cutoff=cutoff, provenance=provenance)
    assert not settings.feedback_analysis_dir.exists()


@pytest.mark.parametrize("which", ["outside", "root", "symlink_parent", "dangling", "traversal", "source_inside"])
def test_destination_boundary_rejects_escape_and_source_overlap(tmp_path, which):
    settings = make_settings(tmp_path)
    create_current_db(settings)
    destination = settings.feedback_analysis_dir / "staging"
    if which == "outside":
        destination = settings.data_dir / "outside"
    elif which == "root":
        destination = settings.feedback_analysis_dir
    elif which == "symlink_parent":
        settings.feedback_analysis_dir.symlink_to(settings.data_dir, target_is_directory=True)
    elif which == "dangling":
        settings.feedback_analysis_dir.mkdir()
        destination.symlink_to(settings.data_dir / "absent")
    elif which == "traversal":
        destination = settings.feedback_analysis_dir / ".." / "outside"
    else:
        destination = settings.feedback_analysis_dir / "staging"
        settings.feedback_analysis_dir.mkdir()
        # A caller-supplied data_dir can place sources underneath a requested destination.
        from dataclasses import replace
        settings = replace(settings, golden_csv_path=destination / "live.csv")
    before = file_receipt(settings.triage_db_path)

    with pytest.raises(ValueError):
        freeze_inputs(settings, destination, cutoff=CUTOFF, provenance=PROVENANCE)

    assert file_receipt(settings.triage_db_path) == before
    assert not (settings.data_dir / "outside").exists()


@pytest.mark.parametrize("source_attr", ["triage_db_path", "golden_csv_path", "interaction_log_path", "classifier_runs_log_path"])
def test_symlink_sources_are_not_followed(tmp_path, source_attr):
    settings = make_settings(tmp_path)
    create_current_db(settings)
    source = getattr(settings, source_attr)
    if source.exists():
        original = source.with_suffix(".original")
        source.replace(original)
    else:
        original = settings.data_dir / "original"
        original.write_bytes(b"synthetic")
    source.symlink_to(original)
    before = original.read_bytes()
    with pytest.raises(ValueError):
        freeze(settings)
    assert original.read_bytes() == before
    assert not settings.feedback_analysis_dir.exists()


@pytest.mark.parametrize("source_attr", ["golden_csv_path", "interaction_log_path", "classifier_runs_log_path"])
def test_optional_permission_errors_propagate_not_missing(tmp_path, monkeypatch, source_attr):
    settings = make_settings(tmp_path)
    create_current_db(settings)
    source = getattr(settings, source_attr)
    source.write_bytes(b"synthetic")
    original = inputs._regular_open

    def denied(path):
        if path == source:
            raise PermissionError("synthetic refusal")
        return original(path)

    monkeypatch.setattr(inputs, "_regular_open", denied)
    with pytest.raises(PermissionError, match="synthetic refusal"):
        freeze(settings)
    assert source.read_bytes() == b"synthetic"
    assert not (settings.feedback_analysis_dir / "staging").exists()


def test_manifest_publication_failure_preserves_sources_and_sibling_bundle(tmp_path, monkeypatch):
    from zotero_summarizer.services import _common
    settings = make_settings(tmp_path)
    create_current_db(settings)
    previous = freeze(settings, "previous")
    before = file_receipt(settings.triage_db_path)

    def fail_atomic(path, write):
        raise OSError("synthetic publication failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(_common, "atomic_write", fail_atomic)
        with pytest.raises(OSError, match="synthetic publication failure"):
            freeze(settings)
    assert file_receipt(settings.triage_db_path) == before
    assert not (settings.feedback_analysis_dir / "staging").exists()
    assert verify_inputs(settings.feedback_analysis_dir / "previous") == previous


@pytest.mark.parametrize("change", ["null_source", "missing_source", "bad_metadata", "metadata_number_type", "not_atomic", "bool_version",
                                   "bad_timestamp", "bad_eof", "missing_provenance", "extra_input", "duplicate_key"])
def test_manifest_shape_is_not_trusted_even_with_recomputed_identity(tmp_path, change):
    settings = make_settings(tmp_path)
    create_current_db(settings)
    settings.interaction_log_path.write_bytes(b"synthetic\n")
    manifest = freeze(settings)
    bundle = settings.feedback_analysis_dir / "staging"
    if change == "null_source":
        manifest["sources"]["golden_csv"] = None
    elif change == "missing_source":
        del manifest["sources"]["golden_csv"]
    elif change == "bad_metadata":
        manifest["sources"]["triage_db"]["sqlite"]["sync_changes"]["max_revision"] = 999
    elif change == "metadata_number_type":
        versions = manifest["sources"]["triage_db"]["sqlite"]["numbered_schema_versions"]["values"]
        versions["triage"] = float(versions["triage"])
    elif change == "not_atomic":
        manifest["alignment"]["cross_store_atomic"] = True
    elif change == "bool_version":
        manifest["schema_version"] = True
    elif change == "bad_timestamp":
        manifest["sources"]["triage_db"]["capture"]["finished_at"] = "1900-01-01T00:00:00Z"
    elif change == "bad_eof":
        manifest["sources"]["interaction_log"]["capture"]["eof_offset"] = 0
    elif change == "missing_provenance":
        del manifest["provenance"]["code"]
    elif change == "extra_input":
        (bundle / "inputs/unlisted").write_bytes(b"unexpected")
    if change not in {"null_source"}:
        manifest["content_digest"] = digest_manifest(manifest)
    raw = json.dumps(manifest)
    if change == "duplicate_key":
        raw = raw.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1')
    (bundle / "input-manifest.json").write_text(raw)

    with pytest.raises(ValueError):
        verify_inputs(bundle)


@pytest.mark.parametrize("target_name", ["input-manifest.json", "inputs"])
def test_verify_refuses_symlink_artifact_containers(tmp_path, target_name):
    settings = make_settings(tmp_path)
    create_current_db(settings)
    freeze(settings)
    bundle = settings.feedback_analysis_dir / "staging"
    target = bundle / target_name
    moved = settings.feedback_analysis_dir / ("moved-" + target_name)
    target.replace(moved)
    target.symlink_to(moved, target_is_directory=moved.is_dir())
    with pytest.raises(ValueError):
        verify_inputs(bundle)


def test_declared_identity_changes_digest_but_capture_clock_and_locator_do_not(tmp_path):
    settings = make_settings(tmp_path)
    create_current_db(settings)
    baseline = freeze(settings)
    bundle = settings.feedback_analysis_dir / "staging"
    changed = freeze_inputs(settings, settings.feedback_analysis_dir / "different-seed", cutoff=CUTOFF,
                            provenance=PROVENANCE | {"seed": 43})
    assert changed["content_digest"] != baseline["content_digest"]
    changed = freeze_inputs(settings, settings.feedback_analysis_dir / "different-cutoff", cutoff=CUTOFF + timedelta(days=1),
                            provenance=PROVENANCE)
    assert changed["content_digest"] != baseline["content_digest"]
    for source in baseline["sources"].values():
        source["source_locator"] = "/nonexistent/declared-source"
        source["capture"]["started_at"] = "2026-10-09T00:00:00Z"
        source["capture"]["finished_at"] = "2026-10-09T00:00:01Z"
    assert digest_manifest(baseline) == baseline["content_digest"]
    (bundle / "input-manifest.json").write_text(json.dumps(baseline))
    assert verify_inputs(bundle) == baseline


@pytest.mark.parametrize("error_number", [errno.ENOSPC, errno.EACCES])
def test_input_initialization_failure_removes_only_owned_destination_and_allows_retry(tmp_path, monkeypatch, error_number):
    settings = make_settings(tmp_path)
    create_current_db(settings)
    sources = [settings.triage_db_path, settings.golden_csv_path, settings.interaction_log_path,
               settings.classifier_runs_log_path]
    for path in sources[1:]:
        path.write_bytes(b"synthetic\n")
    previous = freeze(settings, "previous")
    before = {path: file_receipt(path) for path in sources}
    destination = settings.feedback_analysis_dir / "staging"
    failure = OSError(error_number, "synthetic input initialization failure")
    original = Path.mkdir

    def fail_inputs(path, *args, **kwargs):
        if path == destination / "inputs":
            raise failure
        return original(path, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "mkdir", fail_inputs)
        with pytest.raises(OSError) as raised:
            freeze(settings)
    assert raised.value is failure
    assert not destination.exists()
    assert {path: file_receipt(path) for path in sources} == before
    assert verify_inputs(settings.feedback_analysis_dir / "previous") == previous

    retried = freeze(settings)
    assert verify_inputs(destination) == retried
    assert {path: file_receipt(path) for path in sources} == before
    assert verify_inputs(settings.feedback_analysis_dir / "previous") == previous


@pytest.mark.parametrize("with_sentinel", [False, True])
def test_preexisting_destination_remains_untouched_when_initialization_is_refused(tmp_path, with_sentinel):
    settings = make_settings(tmp_path)
    create_current_db(settings)
    previous = freeze(settings, "previous")
    before = file_receipt(settings.triage_db_path)
    destination = settings.feedback_analysis_dir / "staging"
    destination.mkdir()
    if with_sentinel:
        (destination / "sentinel").write_bytes(b"caller-owned")
    contents = {path.name: path.read_bytes() for path in destination.iterdir()}

    with pytest.raises(FileExistsError):
        freeze(settings)

    assert destination.is_dir()
    assert {path.name: path.read_bytes() for path in destination.iterdir()} == contents
    assert file_receipt(settings.triage_db_path) == before
    assert verify_inputs(settings.feedback_analysis_dir / "previous") == previous


@pytest.mark.parametrize("competing_owner", [False, True])
def test_exclusive_destination_mkdir_failure_never_claims_cleanup_ownership(tmp_path, monkeypatch, competing_owner):
    settings = make_settings(tmp_path)
    create_current_db(settings)
    previous = freeze(settings, "previous")
    before = file_receipt(settings.triage_db_path)
    destination = settings.feedback_analysis_dir / "staging"
    failure = OSError(errno.EEXIST if competing_owner else errno.ENOSPC, "synthetic exclusive mkdir failure")
    original = Path.mkdir

    def fail_destination(path, *args, **kwargs):
        if path == destination:
            if competing_owner:
                original(path, *args, **kwargs)
                (path / "sentinel").write_bytes(b"other-owner")
            raise failure
        return original(path, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "mkdir", fail_destination)
        with pytest.raises(OSError) as raised:
            freeze(settings)
    assert raised.value is failure
    if competing_owner:
        assert (destination / "sentinel").read_bytes() == b"other-owner"
    else:
        assert not destination.exists()
    assert file_receipt(settings.triage_db_path) == before
    assert verify_inputs(settings.feedback_analysis_dir / "previous") == previous


def test_input_initialization_cleanup_error_preserves_original_error_context(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    create_current_db(settings)
    destination = settings.feedback_analysis_dir / "staging"
    initialization_error = OSError(errno.ENOSPC, "synthetic initialization failure")
    cleanup_error = OSError(errno.EACCES, "synthetic cleanup failure")
    original = Path.mkdir

    def fail_inputs(path, *args, **kwargs):
        if path == destination / "inputs":
            raise initialization_error
        return original(path, *args, **kwargs)

    def fail_cleanup(path):
        assert path == destination
        raise cleanup_error

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "mkdir", fail_inputs)
        scoped.setattr(inputs.shutil, "rmtree", fail_cleanup)
        with pytest.raises(OSError) as raised:
            freeze(settings)
    assert raised.value is cleanup_error
    assert raised.value.__context__ is initialization_error
    assert not (destination / "input-manifest.json").exists()
