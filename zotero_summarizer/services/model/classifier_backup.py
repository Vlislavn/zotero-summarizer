"""Versioned snapshots of the trained gate — rollback safety for ``--force``.

``train-classifier --force`` overwrites ``{name}.joblib`` + ``{name}.json`` in place.
The atomic write (``_common.atomic_write``) already prevents a *crashed* write from
bricking the gate, but a *successful* overwrite still destroys the prior model with no
recovery — a measured-better retrain that later looks wrong has no rollback target.

This module snapshots the CURRENT model into a versioned history dir BEFORE
``save_trained`` overwrites it:

    ~/.cache/zotero-summarizer/models/history/{name}/{ts}__{sha8}__oof{rho}/
        {name}.joblib
        {name}.json

Each snapshot is self-contained (joblib + json), so ``restore_snapshot`` is a plain
copy back via ``atomic_write``. A rolling cap (default ``DEFAULT_KEEP``) prunes the
oldest snapshots beyond N so the history is bounded.

INVARIANT (fail-fast): a snapshot that cannot be written RAISES — ``save_trained``
then aborts, so the live model is NEVER overwritten without a successful backup.
The only non-error no-op is "no prior model exists" (first train / clean slate),
which returns ``None``. We do not swallow disk/parse errors: a corrupt model mirror
or a full disk is a signal the operator must see, not a silent skip.
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from zotero_summarizer.services._common import atomic_write, now_iso_z

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


def _read_meta(json_path: Path) -> dict[str, Any]:
    """Read the model json mirror. Missing file → ``{}`` (legitimate absence — the
    joblib may exist without a mirror on a partially-migrated cache). A file that
    EXISTS but is unreadable/unparseable RAISES — a corrupt model mirror is a real
    signal, not something to paper over with an empty label."""
    if not json_path.exists():
        return {}
    return json.loads(json_path.read_text(encoding="utf-8"))


def snapshot_current(
    model_dir: Path,
    classifier_name: str,
    *,
    keep: int = DEFAULT_KEEP,
    ts: str | None = None,
) -> Path | None:
    """Snapshot the current ``{name}.joblib``+``.json`` before an overwrite.

    Returns the snapshot dir, or ``None`` when there is no current model to back up
    (first train / clean slate). Any I/O error RAISES — by the module invariant the
    caller (``save_trained``) must abort the overwrite rather than destroy the prior
    model without a backup. Prunes to ``keep`` newest after the copy succeeds.
    """
    joblib_path = model_dir / f"{classifier_name}.joblib"
    json_path = model_dir / f"{classifier_name}.json"
    if not joblib_path.exists():
        return None
    ts = ts or now_iso_z().replace(":", "").replace("-", "")
    meta = _read_meta(json_path)
    snap_dir = _history_dir(model_dir, classifier_name) / _snapshot_name(meta, ts)
    snap_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(joblib_path, snap_dir / joblib_path.name)
    if json_path.exists():
        shutil.copy2(json_path, snap_dir / json_path.name)
    _prune(_history_dir(model_dir, classifier_name), keep=keep)
    LOGGER.info("snapshot: backed up %s -> %s", joblib_path.name, snap_dir.name)
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
    temporal_spearman, n_train, path}``. Reads the per-snapshot json (no joblib load)."""
    hdir = _history_dir(model_dir, classifier_name)
    if not hdir.exists():
        return []
    out: list[dict[str, Any]] = []
    for snap in sorted(p.name for p in hdir.iterdir() if p.is_dir()):
        meta = _read_meta(hdir / snap / f"{classifier_name}.json")
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
    """Restore a snapshot as the live model (atomic).

    Raises ``FileNotFoundError`` if the snapshot dir or its joblib is missing. The
    current live model is snapshotted first (so a restore is reversible too); that
    snapshot RAISES on failure per the invariant — refuse to clobber the live model
    if it can't be backed up.
    """
    hdir = _history_dir(model_dir, classifier_name)
    snap_dir = hdir / snapshot_name
    src_joblib = snap_dir / f"{classifier_name}.joblib"
    src_json = snap_dir / f"{classifier_name}.json"
    if not src_joblib.exists():
        raise FileNotFoundError(f"snapshot has no joblib: {snap_dir}")
    snapshot_current(model_dir, classifier_name)  # back up live first (reversible)
    joblib_path = model_dir / f"{classifier_name}.joblib"
    json_path = model_dir / f"{classifier_name}.json"
    atomic_write(joblib_path, lambda target: shutil.copy2(src_joblib, target))
    atomic_write(json_path, lambda target: shutil.copy2(src_json, target))
    LOGGER.info("restore: %s -> live %s", snapshot_name, joblib_path.name)
    return joblib_path


__all__ = [
    "DEFAULT_KEEP",
    "DEFAULT_KEEP",
    "snapshot_current",
    "list_snapshots",
    "restore_snapshot",
]
