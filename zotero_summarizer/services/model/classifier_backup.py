"""Versioned snapshots of the trained gate — rollback safety for ``--force``.

``train-classifier --force`` replaces ``{name}.zip`` in place.
The atomic write (``_common.atomic_write``) already prevents a *crashed* write from
bricking the gate, but a *successful* overwrite still destroys the prior model with no
recovery — a measured-better retrain that later looks wrong has no rollback target.

This module snapshots the CURRENT model into a versioned history dir BEFORE
``save_trained`` overwrites it:

    data/models/history/{name}/{ts}__{sha8}__oof{rho}/
        {name}.zip

Snapshot directories have a unique suffix so equal timestamps/metadata cannot
overwrite history. Restore publishes model and metadata together with one atomic
replace, then prunes to ``DEFAULT_KEEP``. A failed restore retains all backups.
Legacy joblib snapshots are converted on restore, ignoring their JSON twins.

INVARIANT (fail-fast): a snapshot that cannot be written RAISES — ``save_trained``
then aborts, so the live model is NEVER overwritten without a successful backup.
The only non-error no-op is "no prior model exists" (first train / clean slate),
which returns ``None``. We do not swallow disk/parse errors: a corrupt model mirror
or a full disk is a signal the operator must see, not a silent skip.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

from zotero_summarizer.services._common import atomic_write, now_iso_z
from zotero_summarizer.services.model.classifier_store import (
    load_trained, model_path, read_metadata, write_archive,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_KEEP = 10
"""Max snapshots kept per classifier. Oldest pruned beyond this (rolling)."""

_SEP = "__"


def _history_dir(model_dir: Path, classifier_name: str) -> Path:
    """Per-classifier history root: ``<model_dir>/history/<name>/``."""
    return model_dir / "history" / classifier_name


def _snapshot_name(meta: dict[str, Any], ts: str) -> str:
    """``{ts}__{sha8}__oof{rho}`` — readable rollback-target label."""
    sha = str(meta.get("git_commit") or meta.get("git_sha") or "nogit")[:8]
    oof = meta.get("oof_spearman")
    oof_tag = f"oof{float(oof):.4f}" if isinstance(oof, (int, float)) else "oofNone"
    return f"{ts}{_SEP}{sha}{_SEP}{oof_tag}"


def snapshot_current(
    model_dir: Path,
    classifier_name: str,
    *,
    keep: int = DEFAULT_KEEP,
    ts: str | None = None,
) -> Path | None:
    """Snapshot the current model artifact before an overwrite.

    Returns the snapshot dir, or ``None`` when there is no current model to back up
    (first train / clean slate). Any I/O error RAISES — by the module invariant the
    caller (``save_trained``) must abort the overwrite rather than destroy the prior
    model without a backup. Prunes to ``keep`` newest after the copy succeeds.
    """
    source = model_path(model_dir, classifier_name)
    if not source.exists():
        return None
    ts = ts or now_iso_z().replace(":", "").replace("-", "")
    meta = read_metadata(source)
    hdir = _history_dir(model_dir, classifier_name)
    hdir.mkdir(parents=True, exist_ok=True)
    snap_dir = Path(mkdtemp(prefix=_snapshot_name(meta, ts) + _SEP, dir=hdir))
    atomic_write(snap_dir / source.name, lambda target: shutil.copy2(source, target))
    _prune(hdir, keep=keep)
    LOGGER.info("snapshot: backed up %s -> %s", source.name, snap_dir.name)
    return snap_dir


def _prune(hdir: Path, *, keep: int) -> None:
    """Keep the ``keep`` newest snapshot dirs (lexicographic == chronological).

    Runs only after the new snapshot is safely on disk, so a prune failure leaves the
    backup intact (worst case: ``keep + 1`` snapshots). It still RAISES on a real
    disk/permission error rather than silently leaking old snapshots.
    """
    if keep <= 0 or not hdir.exists():
        return
    snaps = sorted(p.name for p in hdir.iterdir() if p.is_dir())
    for old in snaps[:-keep]:
        shutil.rmtree(hdir / old)


def list_snapshots(model_dir: Path, classifier_name: str) -> list[dict[str, Any]]:
    """Newest-first snapshot inventory: ``{name, trained_at, git_commit, oof_spearman,
    temporal_spearman, n_train, path}``. ZIP metadata needs no joblib load."""
    hdir = _history_dir(model_dir, classifier_name)
    if not hdir.exists():
        return []
    out: list[dict[str, Any]] = []
    for snap in sorted(p.name for p in hdir.iterdir() if p.is_dir()):
        source = model_path(hdir / snap, classifier_name)
        if not source.is_file():
            continue  # Snapshot publication has not completed.
        meta = read_metadata(source)
        out.append({
            "name": snap,
            "trained_at": meta.get("trained_at"),
            "git_commit": meta.get("git_commit"),
            "oof_spearman": meta.get("oof_spearman"),
            "temporal_spearman": meta.get("temporal_spearman"),
            "n_train": meta.get("n_train"),
            "path": str(hdir / snap),
        })
    out.reverse()  # newest first
    return out


def restore_snapshot(
    model_dir: Path, classifier_name: str, snapshot_name: str
) -> Path:
    """Restore a snapshot as one atomically replaced live archive.

    Validates the source before any write. The
    current live model is snapshotted first (so a restore is reversible too); that
    snapshot RAISES on failure per the invariant — refuse to clobber the live model
    if it can't be backed up.
    """
    hdir = _history_dir(model_dir, classifier_name)
    snap_dir = hdir / snapshot_name
    source = model_path(snap_dir, classifier_name)
    trained = load_trained(source)
    # Retain the restore source until publication succeeds; failure keeps all backups.
    snapshot_current(model_dir, classifier_name, keep=0)
    destination = model_dir / f"{classifier_name}.zip"
    if source.suffix == ".joblib":
        write_archive(trained, destination)
    else:
        atomic_write(destination, lambda target: shutil.copy2(source, target))
    _prune(hdir, keep=DEFAULT_KEEP)
    LOGGER.info("restore: %s -> live %s", snapshot_name, destination.name)
    return destination


__all__ = [
    "DEFAULT_KEEP",
    "snapshot_current",
    "list_snapshots",
    "restore_snapshot",
]
