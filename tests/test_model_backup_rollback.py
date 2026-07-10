"""Regression tests for the gate snapshot/rollback safety (Part 1).

Catches the bug this fixes: `train-classifier --force` used to overwrite the live
model with no backup, so a retrain that later looked wrong had no rollback target.
`save_trained` now snapshots the prior model first; these prove the invariant.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from zotero_summarizer.services.model.classifier_backup import (
    DEFAULT_KEEP,
    list_snapshots,
    restore_snapshot,
    snapshot_current,
)
from zotero_summarizer.services.model.classifier_training import save_trained


def _write_fake_model(model_dir: Path, name: str, *, oof: float) -> None:
    """Stand up a minimal live model pair (joblib + json) without training."""
    import joblib

    model_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump({"fake": name, "oof": oof}, model_dir / f"{name}.joblib")
    (model_dir / f"{name}.json").write_text(
        json.dumps({
            "classifier_name": name,
            "oof_spearman": oof,
            "trained_at": "2026-06-28T00:00:00Z",
            "git_commit": "deadbeef",
            "n_train": 2065,
        }),
        encoding="utf-8",
    )


def test_snapshot_captures_prior_model(tmp_path: Path) -> None:
    """A snapshot copies the live joblib+json into a versioned history dir."""
    model_dir = tmp_path / "models"
    _write_fake_model(model_dir, "lightgbm", oof=0.7072)

    snap = snapshot_current(model_dir, "lightgbm", ts="20260628T120000Z")

    assert snap is not None
    assert snap.is_dir()
    assert (snap / "lightgbm.joblib").exists()
    assert (snap / "lightgbm.json").exists()
    # the snapshot name carries the oof so a rollback target is readable
    assert "oof0.7072" in snap.name


def test_snapshot_noop_when_no_prior_model(tmp_path: Path) -> None:
    """First train / clean slate: no prior model → None, never an error."""
    model_dir = tmp_path / "models"
    model_dir.mkdir(parents=True)
    assert snapshot_current(model_dir, "lightgbm") is None


def test_restore_round_trips_byte_identical(tmp_path: Path) -> None:
    """Restoring a snapshot reproduces the prior model exactly."""
    import joblib

    model_dir = tmp_path / "models"
    _write_fake_model(model_dir, "lightgbm", oof=0.7072)
    snap = snapshot_current(model_dir, "lightgbm", ts="20260628T120000Z")
    assert snap is not None

    # overwrite the live model with a different one (the "regression")
    _write_fake_model(model_dir, "lightgbm", oof=0.5878)
    assert joblib.load(model_dir / "lightgbm.joblib")["oof"] == 0.5878

    # restore → live model is the prior 0.7072 again
    restore_snapshot(model_dir, "lightgbm", snap.name)
    assert joblib.load(model_dir / "lightgbm.joblib")["oof"] == 0.7072
    assert json.loads((model_dir / "lightgbm.json").read_text())["oof_spearman"] == 0.7072


def test_restore_is_reversible(tmp_path: Path) -> None:
    """Restore backs up the current live model first, so it can be undone."""
    import joblib

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
    assert joblib.load(model_dir / "lightgbm.joblib")["oof"] == 0.5878


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
    # joblib.dump is patched, so X_train/y_train/fitted_model need only satisfy
    # the dataclass shape (their values are never serialized here).
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
    with patch(
        "zotero_summarizer.services.model.classifier_training.joblib.dump",
        side_effect=lambda obj, target: target.write_bytes(b"patched"),
    ):
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
