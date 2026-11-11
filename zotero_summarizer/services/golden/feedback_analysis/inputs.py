"""Freeze private staging inputs; verify replay without consulting live stores."""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import stat
import time
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path

from zotero_summarizer.settings import Settings

from .provenance import (ALIGNMENT, RETAINED_PATHS, canonical_bytes, declared_provenance,
                         digest_manifest, source_record, timestamp_now, utc_iso, validate_manifest)
from .sqlite_snapshot import capture_sqlite, inspect_snapshot

DEFAULT_WALL_CAP_SECONDS = 60.0
MAX_WALL_CAP_SECONDS = 600.0
WALL_CAP_ENV = "ZS_FEEDBACK_FREEZE_WALL_CAP_SECONDS"
MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_SNAPSHOT_PAGES = 65536
MAX_BACKUP_PAGES_PER_STEP = 128
MAX_PROGRESS_CALLBACKS = 4096
MAX_MANIFEST_BYTES = 1024 * 1024
COPY_CHUNK_BYTES = 65536


@dataclass(frozen=True)
class FreezeBudgets:
    max_source_bytes: int = MAX_SOURCE_BYTES
    max_snapshot_pages: int = MAX_SNAPSHOT_PAGES
    backup_pages_per_step: int = MAX_BACKUP_PAGES_PER_STEP
    max_progress_callbacks: int = MAX_PROGRESS_CALLBACKS
    wall_cap_seconds: float = DEFAULT_WALL_CAP_SECONDS

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name == "wall_cap_seconds":
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= MAX_WALL_CAP_SECONDS:
                    raise ValueError(f"wall_cap_seconds must be positive and <= {MAX_WALL_CAP_SECONDS}")
            elif type(value) is not int or not 0 < value <= field.default:
                raise ValueError(f"{field.name} must be a positive integer <= {field.default}")


def _budgets(value) -> FreezeBudgets:
    # Validate even an overridden environment value; never hide malformed config.
    wall = float(os.environ.get(WALL_CAP_ENV, DEFAULT_WALL_CAP_SECONDS))
    default = FreezeBudgets(wall_cap_seconds=wall)
    if value is None:
        return default
    if isinstance(value, FreezeBudgets):
        return value
    if type(value) is not dict or value.keys() - {field.name for field in fields(FreezeBudgets)}:
        raise ValueError("budgets must be FreezeBudgets or a known-field dict")
    return FreezeBudgets(**({"wall_cap_seconds": default.wall_cap_seconds} | value))


class BudgetExceeded(ValueError):
    """A freeze cannot be published within its declared safety/work bounds."""


class _Guard:
    def __init__(self, budgets):
        self.budgets = budgets
        self.deadline = time.monotonic() + budgets.wall_cap_seconds
        self.page_size = 0

    def expired(self):
        return time.monotonic() >= self.deadline

    def check(self):
        if self.expired():
            raise BudgetExceeded("frozen-input safety wall cap exhausted")

    def source_bytes(self, size):
        self.check()
        if size > self.budgets.max_source_bytes:
            raise BudgetExceeded("frozen-input source byte budget exhausted")

    def snapshot(self, pages, page_size):
        self.check()
        if pages > self.budgets.max_snapshot_pages:
            raise BudgetExceeded("frozen-input SQLite page budget exhausted")
        self.source_bytes(pages * page_size)
        self.page_size = page_size

    def backup_progress(self, callbacks, pages):
        self.snapshot(pages, self.page_size)
        if callbacks > self.budgets.max_progress_callbacks:
            raise BudgetExceeded("frozen-input backup callback budget exhausted")


def _no_symlinks(path: Path) -> Path:
    path = Path(path).absolute()
    if ".." in path.parts:
        raise ValueError("parent traversal is not a frozen-input path")
    for component in (*reversed(path.parents), path):
        if component.is_symlink():
            raise ValueError("symlinks are not frozen-input paths")
    return path


def _new_destination(settings: Settings, destination: Path, sources: dict) -> Path:
    root = _no_symlinks(settings.feedback_analysis_dir)
    destination = _no_symlinks(destination)
    if destination == root or not destination.is_relative_to(root):
        raise ValueError("destination must be beneath Settings.feedback_analysis_dir")
    if destination.exists():
        raise FileExistsError("frozen-input destination already exists")
    if any(path.is_relative_to(destination) for path in sources.values()):
        raise ValueError("destination cannot contain a live source")
    for parent in destination.parents:
        if parent == root.parent:
            break
        if (parent / "input-manifest.json").exists():
            raise ValueError("destination cannot be nested inside a frozen bundle")
    for directory in reversed(destination.parents):
        if directory == root or directory.is_relative_to(root):
            directory.mkdir(mode=0o700, exist_ok=True)
    destination.mkdir(mode=0o700)
    try:
        (destination / "inputs").mkdir(mode=0o700)
    except BaseException:
        shutil.rmtree(destination)
        raise
    return destination


def _regular_open(path: Path):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("input must be a regular file")
        return os.fdopen(fd, "rb", buffering=0)
    except BaseException:
        os.close(fd)
        raise


def _read_prefix(handle, size: int, guard, output=None) -> str:
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        guard.check()
        chunk = handle.read(min(remaining, COPY_CHUNK_BYTES))
        if not chunk:
            raise ValueError("input shrank before its declared EOF")
        digest.update(chunk)
        if output is not None:
            output.write(chunk)
        remaining -= len(chunk)
    guard.check()
    return digest.hexdigest()


def _same_file_state(before, after, *, allow_detached=False):
    content_state_equal = (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
    # Atomic replacement unlinks the opened old inode: ctime changes, bytes do not.
    detached = allow_detached and before.st_nlink > 0 and after.st_nlink == 0
    return content_state_equal and (before.st_ctime_ns == after.st_ctime_ns or detached)


def _capture_file(name, source, target, guard):
    started = timestamp_now()
    try:
        handle = _regular_open(source)
    except FileNotFoundError:
        return source_record(name, source, start=started, end=timestamp_now())
    with handle:
        before = os.fstat(handle.fileno())
        guard.source_bytes(before.st_size)
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as output:
            digest = _read_prefix(handle, before.st_size, guard, output)
        handle.seek(0)
        if _read_prefix(handle, before.st_size, guard) != digest:
            raise ValueError("input prefix mutated during capture")
        after = os.fstat(handle.fileno())
        if after.st_size < before.st_size:
            raise ValueError("input truncated during capture")
        if (name == "golden_csv" or after.st_size == before.st_size) and not _same_file_state(
            before, after, allow_detached=name == "golden_csv",
        ):
            raise ValueError("input changed in place during capture")
    return source_record(name, source, start=started, end=timestamp_now(), size=before.st_size, sha256=digest)


def _retained_hash(path, guard):
    with _regular_open(path) as handle:
        before = os.fstat(handle.fileno())
        guard.source_bytes(before.st_size)
        digest = _read_prefix(handle, before.st_size, guard)
        if not _same_file_state(before, os.fstat(handle.fileno())):
            raise ValueError("retained input changed during verification")
    return digest, before.st_size


def _verify_retained(destination, manifest, guard, *, published):
    validate_manifest(manifest)
    destination = _no_symlinks(destination)
    inputs = _no_symlinks(destination / "inputs")
    expected_root = {"inputs", "input-manifest.json"} if published else {"inputs"}
    if {path.name for path in destination.iterdir()} != expected_root or not inputs.is_dir():
        raise ValueError("partial or unexpected input bundle artifacts")
    expected_inputs = {Path(source["path"]).name for source in manifest["sources"].values()
                       if source["status"] == "present"}
    if {path.name for path in inputs.iterdir()} != expected_inputs:
        raise ValueError("missing or unexpected retained inputs")
    for name, source in manifest["sources"].items():
        if source["status"] == "missing":
            continue
        path = _no_symlinks(destination / source["path"])
        if _retained_hash(path, guard) != (source["sha256"], source["byte_size"]):
            raise ValueError(f"retained input hash/size mismatch: {name}")
        if name == "triage_db":
            actual_metadata = inspect_snapshot(path, guard)
            if canonical_bytes(actual_metadata) != canonical_bytes(source["sqlite"]):
                raise ValueError("retained SQLite metadata mismatch")
    guard.check()
    return manifest


def freeze_inputs(settings: Settings, destination: Path, *, cutoff: datetime, provenance: dict, budgets=None) -> dict:
    """Capture a new private staging bundle; failures remove this new bundle only."""
    guard = _Guard(_budgets(budgets))
    cutoff_iso = utc_iso(cutoff)
    declared = declared_provenance(provenance)
    sources = {"triage_db": Path(settings.triage_db_path).absolute(),
               "golden_csv": Path(settings.golden_csv_path).absolute(),
               "interaction_log": Path(settings.interaction_log_path).absolute(),
               "classifier_runs_log": Path(settings.classifier_runs_log_path).absolute()}
    for path in sources.values():
        _no_symlinks(path)
    destination = _new_destination(settings, destination, sources)
    try:
        captured = {}
        for name, source in sources.items():
            guard.check()
            target = destination / RETAINED_PATHS[name]
            if name == "triage_db":
                start = timestamp_now()
                metadata = capture_sqlite(source, target, guard)
                digest, size = _retained_hash(target, guard)
                captured[name] = source_record(name, source, start=start, end=timestamp_now(),
                                               size=size, sha256=digest, sqlite=metadata)
            else:
                captured[name] = _capture_file(name, source, target, guard)
        manifest = {"schema_version": 1, "cutoff": cutoff_iso, "provenance": declared,
                    "alignment": ALIGNMENT.copy(), "sources": captured}
        manifest["content_digest"] = digest_manifest(manifest)
        _verify_retained(destination, manifest, guard, published=False)
        payload = canonical_bytes(manifest)
        if len(payload) > MAX_MANIFEST_BYTES:
            raise BudgetExceeded("frozen-input manifest byte budget exhausted")
        from zotero_summarizer.services._common import atomic_write

        def write_manifest(path):
            guard.check()
            path.write_bytes(payload)
            guard.check()

        atomic_write(destination / "input-manifest.json", write_manifest)
        guard.check()
        return manifest
    except BaseException:
        shutil.rmtree(destination)
        raise


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate input manifest key")
        result[key] = value
    return result


def verify_inputs(destination: Path) -> dict:
    """Validate shape, retained bytes and DB metadata; never follow source locators."""
    guard = _Guard(_budgets(None))
    destination = _no_symlinks(destination)
    manifest_path = _no_symlinks(destination / "input-manifest.json")
    with _regular_open(manifest_path) as handle:
        raw = handle.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise BudgetExceeded("frozen-input manifest byte budget exhausted")
    manifest = json.loads(raw, object_pairs_hook=_unique_keys)
    return _verify_retained(destination, manifest, guard, published=True)
