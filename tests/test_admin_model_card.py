"""Tests for GET /api/admin/model — the Settings model-card endpoint.

Calls the route handler ``model_card`` directly (it's a plain async
function). The handler now lives in ``services/model/model_card.py`` (lifted out
of the api layer); ``admin`` re-exports it, so the route is unchanged. For each
case we point ``settings.project_root`` at a tmp directory and override the
``_model_dir`` helper *on the service module* (where ``model_card`` resolves it)
to read from the same tmp tree, so nothing touches the user's real ``~/.cache``.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from zotero_summarizer.api.routes import admin as admin_route
from zotero_summarizer.runtime import AppContext, set_context
from zotero_summarizer.services.model import model_card as model_card_svc
from zotero_summarizer.settings import Settings


def _seed_settings(tmp_path: Path) -> None:
    """Point the runtime at ``tmp_path`` for project_root + triage DB.

    ``Settings.load(project_root=...)`` derives every per-project path
    (triage_history.db, corpus_cache.db, golden CSV) from the same root,
    so a single argument is enough to hermetically sandbox the run.
    """
    set_context(AppContext(settings=Settings.load(project_root=tmp_path)))


def _override_model_dir(monkeypatch: pytest.MonkeyPatch, model_dir: Path) -> None:
    """Make ``model_card``'s ``_model_dir()`` return ``model_dir`` instead of
    ~/.cache/.../models. Patched on the service module (where ``model_card``
    resolves the helper); ``admin`` re-exports the same object."""
    monkeypatch.setattr(model_card_svc, "_model_dir", lambda: model_dir)


def _run(coro):
    return asyncio.run(coro)


def test_model_card_empty_when_no_model_on_disk(tmp_path: Path, monkeypatch):
    _seed_settings(tmp_path)
    _override_model_dir(monkeypatch, tmp_path / "models")  # does not exist
    out = _run(admin_route.model_card())
    assert out == {"model": None}


def test_model_card_returns_metadata_from_json_twin(tmp_path: Path, monkeypatch):
    _seed_settings(tmp_path)
    model_dir = tmp_path / "models"
    model_dir.mkdir()

    twin = {
        "classifier_name": "lightgbm",
        "golden_csv_sha256": "d4e039e152a38b83e5cd09c16bded7943a96a56941747a8c8e798dbbb0abf23c",
        "feature_dim": 780,
        "pca_dim": 100,
        "thresholds": {"keep": 0.4, "must": 0.7, "could": 0.5},
        "n_train": 1171,
        "n_positive_library": 516,
        "objective": "regression",
        "oof_spearman": 0.7617,
        "trained_at": "2026-05-15T22:15:20Z",
        "git_commit": "4d24654",
    }
    (model_dir / "lightgbm.json").write_text(json.dumps(twin))
    (model_dir / "lightgbm.joblib").write_bytes(b"stub")  # just needs to exist + have size

    _override_model_dir(monkeypatch, model_dir)
    out = _run(admin_route.model_card())

    assert out["model"] is not None
    m = out["model"]
    assert m["classifier_name"] == "lightgbm"
    assert m["n_train"] == 1171
    assert m["oof_spearman"] == pytest.approx(0.7617)
    assert m["trained_at"] == "2026-05-15T22:15:20Z"
    assert m["git_commit"] == "4d24654"
    assert m["golden_csv_sha256_prefix"] == "d4e039e152a3"
    assert m["thresholds"] == {"keep": 0.4, "must": 0.7, "could": 0.5}
    assert m["joblib_size_bytes"] == 4
    assert m["runlog"] is None  # no classifier-runs.jsonl present


def test_model_card_skips_orphan_json_without_joblib(tmp_path: Path, monkeypatch):
    """A .json twin without a paired .joblib is half-deleted; skip it."""
    _seed_settings(tmp_path)
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "lightgbm.json").write_text(json.dumps({"classifier_name": "lightgbm"}))
    # No .joblib alongside.

    _override_model_dir(monkeypatch, model_dir)
    out = _run(admin_route.model_card())
    assert out == {"model": None}


def test_model_card_picks_freshest_when_multiple_models_exist(tmp_path: Path, monkeypatch):
    """Two models on disk → return the one whose .json mtime is newer."""
    _seed_settings(tmp_path)
    model_dir = tmp_path / "models"
    model_dir.mkdir()

    older = {"classifier_name": "logreg", "trained_at": "2026-05-10T00:00:00Z"}
    newer = {"classifier_name": "lightgbm", "trained_at": "2026-05-15T22:15:20Z"}

    older_json = model_dir / "logreg.json"
    newer_json = model_dir / "lightgbm.json"
    older_json.write_text(json.dumps(older))
    newer_json.write_text(json.dumps(newer))
    (model_dir / "logreg.joblib").write_bytes(b"x")
    (model_dir / "lightgbm.joblib").write_bytes(b"x")

    import os
    # Force older_json mtime to be in the past.
    os.utime(older_json, (1_700_000_000, 1_700_000_000))
    os.utime(model_dir / "logreg.joblib", (1_700_000_000, 1_700_000_000))

    _override_model_dir(monkeypatch, model_dir)
    out = _run(admin_route.model_card())
    assert out["model"]["classifier_name"] == "lightgbm"


def test_model_card_includes_latest_runlog_entry(tmp_path: Path, monkeypatch):
    """When classifier-runs.jsonl has a matching entry, surface it as ``runlog``."""
    _seed_settings(tmp_path)
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "lightgbm.json").write_text(json.dumps({"classifier_name": "lightgbm"}))
    (model_dir / "lightgbm.joblib").write_bytes(b"x")

    log_path = tmp_path / "data" / "classifier-runs.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    older_entry = {
        "run_id": "20260510_logreg", "timestamp": "2026-05-10T10:00:00Z",
        "classifier": "logreg", "type": "train_artifact", "cv": {"auc": 0.60},
    }
    newer_entry = {
        "run_id": "20260515_lightgbm", "timestamp": "2026-05-15T22:15:20Z",
        "classifier": "lightgbm", "type": "train_artifact", "cv": {"auc": 0.71},
    }
    log_path.write_text("\n".join(json.dumps(e) for e in (older_entry, newer_entry)))

    _override_model_dir(monkeypatch, model_dir)
    out = _run(admin_route.model_card())
    assert out["model"]["runlog"] is not None
    assert out["model"]["runlog"]["run_id"] == "20260515_lightgbm"
    assert out["model"]["runlog"]["cv"]["auc"] == pytest.approx(0.71)
