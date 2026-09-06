"""Regression tests for the gate snapshot/rollback safety (Part 1).

Catches the bug this fixes: `train-classifier --force` used to overwrite the live
model with no backup, so a retrain that later looked wrong had no rollback target.
`save_trained` now snapshots the prior model first; these prove the invariant.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

import pytest

from zotero_summarizer.services.model.classifier_backup import (
    DEFAULT_KEEP,
    list_snapshots,
    restore_snapshot,
    snapshot_current,
)
from zotero_summarizer.services.model.classifier_training import save_trained
from zotero_summarizer.services.model.classifier_store import load_trained, read_metadata, write_archive
from zotero_summarizer.services._common import atomic_write


def _write_fake_model(model_dir: Path, name: str, *, oof: float) -> None:
    """Stand up a real serializable archive without training."""
    model_dir.mkdir(parents=True, exist_ok=True)
    write_archive(SimpleNamespace(
        classifier_name=name, golden_csv_sha256="abc", feature_dim=1, pca_dim=0,
        t_keep=0.4, t_must=0.7, t_could=0.5,
        training_metadata={
            "oof_spearman": oof,
            "trained_at": "2026-06-28T00:00:00Z",
            "git_commit": "deadbeef",
            "n_train": 2065,
        },
    ), model_dir / f"{name}.zip")


def test_snapshot_captures_prior_model(tmp_path: Path) -> None:
    """A snapshot copies the live archive into a versioned history dir."""
    model_dir = tmp_path / "models"
    _write_fake_model(model_dir, "lightgbm", oof=0.7072)

    snap = snapshot_current(model_dir, "lightgbm", ts="20260628T120000Z")

    assert snap is not None
    assert snap.is_dir()
    assert (snap / "lightgbm.zip").exists()
    # the snapshot name carries the oof so a rollback target is readable
    assert "oof0.7072" in snap.name


def test_snapshot_noop_when_no_prior_model(tmp_path: Path) -> None:
    """First train / clean slate: no prior model → None, never an error."""
    model_dir = tmp_path / "models"
    model_dir.mkdir(parents=True)
    assert snapshot_current(model_dir, "lightgbm") is None


def test_restore_round_trips_byte_identical(tmp_path: Path) -> None:
    """Restoring a snapshot reproduces the prior model exactly."""
    model_dir = tmp_path / "models"
    _write_fake_model(model_dir, "lightgbm", oof=0.7072)
    snap = snapshot_current(model_dir, "lightgbm", ts="20260628T120000Z")
    assert snap is not None
    original = (snap / "lightgbm.zip").read_bytes()

    # overwrite the live model with a different one (the "regression")
    _write_fake_model(model_dir, "lightgbm", oof=0.5878)
    assert load_trained(model_dir / "lightgbm.zip").training_metadata["oof_spearman"] == 0.5878

    # restore → live model is the prior 0.7072 again
    restore_snapshot(model_dir, "lightgbm", snap.name)
    assert (model_dir / "lightgbm.zip").read_bytes() == original
    assert load_trained(model_dir / "lightgbm.zip").training_metadata["oof_spearman"] == 0.7072
    assert read_metadata(model_dir / "lightgbm.zip")["oof_spearman"] == 0.7072


def test_restore_is_reversible(tmp_path: Path) -> None:
    """Restore backs up the current live model first, so it can be undone."""
    model_dir = tmp_path / "models"
    _write_fake_model(model_dir, "lightgbm", oof=0.7072)
    snap_a = snapshot_current(model_dir, "lightgbm", ts="20260628T120000Z")
    assert snap_a is not None
    _write_fake_model(model_dir, "lightgbm", oof=0.5878)

    restore_snapshot(model_dir, "lightgbm", snap_a.name)  # back to 0.7072

    # the 0.5878 model we just clobbered was itself snapshotted → restorable
    history = list_snapshots(model_dir, "lightgbm")
    oof5878 = [s for s in history if "oof0.5878" in s["name"]]
    assert oof5878, "restore must back up the live model before clobbering it"
    restore_snapshot(model_dir, "lightgbm", oof5878[0]["name"])
    assert load_trained(model_dir / "lightgbm.zip").training_metadata["oof_spearman"] == 0.5878


def test_rolling_prune_keeps_newest(tmp_path: Path) -> None:
    """Beyond DEFAULT_KEEP, the oldest snapshots are pruned (bounded history)."""
    model_dir = tmp_path / "models"
    _write_fake_model(model_dir, "lightgbm", oof=0.6)
    for i in range(DEFAULT_KEEP + 3):
        snapshot_current(model_dir, "lightgbm", ts=f"2026062{i:02d}T120000Z")
    snaps = list_snapshots(model_dir, "lightgbm")
    assert len(snaps) == DEFAULT_KEEP
    # newest-first; the oldest 3 ts (i=0,1,2 → ...200/201/202) must be pruned
    names = {s["name"] for s in snaps}
    assert not any(n.startswith("202606200") for n in names), "oldest snapshot pruned"
    assert any(n.startswith("202606212") for n in names), "newest snapshot kept"


def test_save_trained_snapshots_before_overwrite(tmp_path: Path) -> None:
    """The actual wiring: save_trained backs up the prior model before writing.

    This is the regression test for the overwrite bug — would fail if the
    `snapshot_current` call were removed from save_trained.
    """
    from zotero_summarizer.services.model.classifier_artifact import TrainedClassifier

    model_dir = tmp_path / "models"
    _write_fake_model(model_dir, "lightgbm", oof=0.7072)  # the "prior" model

    # save_trained overwrites; the prior 0.7072 must land in history.
    import numpy as np

    trained = TrainedClassifier(
        classifier_name="lightgbm",
        golden_csv_sha256="abc",
        feature_dim=780,
        pca_dim=100,
        X_train=np.zeros((1, 780), dtype=np.float32),
        y_train=np.zeros(1, dtype=np.float64),
        fitted_model=None,
        training_metadata={"oof_spearman": 0.5878, "git_commit": "c94f8b9",
                           "trained_at": "2026-06-28T14:02:21Z", "n_train": 2065},
    )
    save_trained(trained, model_dir)

    history = list_snapshots(model_dir, "lightgbm")
    assert any("oof0.7072" in s["name"] for s in history), (
        "save_trained must snapshot the prior model before overwriting it"
    )


def test_restore_missing_snapshot_raises(tmp_path: Path) -> None:
    """A missing snapshot name is a hard error, not a silent no-op."""
    model_dir = tmp_path / "models"
    _write_fake_model(model_dir, "lightgbm", oof=0.6)
    with pytest.raises(FileNotFoundError):
        restore_snapshot(model_dir, "lightgbm", "does-not-exist")


def test_restore_oldest_at_retention_limit(tmp_path):
    for i in range(DEFAULT_KEEP):
        _write_fake_model(tmp_path, "lightgbm", oof=i / 100)
        snapshot_current(tmp_path, "lightgbm", ts=f"202601{i + 1:02d}T120000Z")
    oldest = list_snapshots(tmp_path, "lightgbm")[-1]["name"]
    _write_fake_model(tmp_path, "lightgbm", oof=0.9)
    restore_snapshot(tmp_path, "lightgbm", oldest)
    assert load_trained(tmp_path / "lightgbm.zip").training_metadata["oof_spearman"] == 0
    assert read_metadata(tmp_path / "lightgbm.zip")["oof_spearman"] == 0
    history = list_snapshots(tmp_path, "lightgbm")
    assert len(history) == DEFAULT_KEEP
    previous = next(s for s in history if s["oof_spearman"] == 0.9)
    restore_snapshot(tmp_path, "lightgbm", previous["name"])
    assert load_trained(tmp_path / "lightgbm.zip").training_metadata["oof_spearman"] == 0.9


def test_failed_restore_does_not_prune_source(tmp_path):
    for i in range(DEFAULT_KEEP):
        _write_fake_model(tmp_path, "lightgbm", oof=i / 100)
        snapshot_current(tmp_path, "lightgbm", ts=f"202601{i + 1:02d}T120000Z")
    oldest = list_snapshots(tmp_path, "lightgbm")[-1]["name"]
    before = (tmp_path / "lightgbm.zip").read_bytes()
    def fail_live_publication(path, write):
        if path.parent == tmp_path:
            raise OSError("disk full")
        atomic_write(path, write)

    with patch("zotero_summarizer.services.model.classifier_backup.atomic_write", side_effect=fail_live_publication):
        with pytest.raises(OSError, match="disk full"):
            restore_snapshot(tmp_path, "lightgbm", oldest)
    assert (tmp_path / "lightgbm.zip").read_bytes() == before
    assert (tmp_path / "history" / "lightgbm" / oldest).is_dir()
    assert len(list_snapshots(tmp_path, "lightgbm")) == DEFAULT_KEEP + 1


def test_snapshot_names_cannot_overwrite_existing_backup(tmp_path):
    _write_fake_model(tmp_path, "lightgbm", oof=0.5)
    first = snapshot_current(tmp_path, "lightgbm", ts="20260101T120000Z")
    before = (first / "lightgbm.zip").read_bytes()
    trained = load_trained(tmp_path / "lightgbm.zip")
    trained.training_metadata["version"] = "different model, same timestamp and score"
    write_archive(trained, tmp_path / "lightgbm.zip")
    second = snapshot_current(tmp_path, "lightgbm", ts="20260101T120000Z")
    assert first != second
    assert (first / "lightgbm.zip").read_bytes() == before


def test_missing_archive_metadata_leaves_live_model_intact(tmp_path):
    _write_fake_model(tmp_path, "lightgbm", oof=0.5)
    snapshot = snapshot_current(tmp_path, "lightgbm", ts="20260101T120000Z")
    with ZipFile(snapshot / "lightgbm.zip") as archive:
        payload = archive.read("model.joblib")
    with ZipFile(snapshot / "lightgbm.zip", "w") as archive:
        archive.writestr("model.joblib", payload)
    _write_fake_model(tmp_path, "lightgbm", oof=0.9)
    before = (tmp_path / "lightgbm.zip").read_bytes()
    with pytest.raises(KeyError, match="metadata.json"):
        restore_snapshot(tmp_path, "lightgbm", snapshot.name)
    assert (tmp_path / "lightgbm.zip").read_bytes() == before
