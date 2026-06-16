"""Trained-classifier metadata for the Settings UI (the "ModelCard").

Reads the on-disk model artifacts written by ``classifier_persistence`` and the
append-only FAIR run-log, and assembles the card the Settings page renders. Lives
in the services layer (not ``api/routes``) so both the admin route and the setup
status service can consume it without an api→api import.

Returns ``{"model": null}`` when no model is on disk yet so callers can render an
empty state instead of 404'ing.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zotero_summarizer.services._common import settings as get_settings


def _model_dir() -> Path:
    """Where ``train_and_save`` writes ``{classifier}.{joblib,json}``."""
    from zotero_summarizer.services.model.classifier_persistence import DEFAULT_MODEL_DIR
    return DEFAULT_MODEL_DIR


def _load_latest_runlog_entry(classifier_name: str) -> dict[str, Any] | None:
    """Return the newest matching entry from ``classifier-runs.jsonl``.

    The classifier writes one JSONL line per train. We scan the whole file
    (it's tiny — append-only) and pick the latest line whose
    ``classifier == classifier_name`` and whose ``type`` is the training
    artefact entry (skip prediction-run lines that share the file).
    """
    settings = get_settings()
    log_path = settings.data_dir / "classifier-runs.jsonl"
    if not log_path.exists():
        return None
    latest: dict[str, Any] | None = None
    with log_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry.get("classifier") != classifier_name:
                continue
            # Older entries had no "type" field; treat them as training rows.
            if entry.get("type") not in (None, "train_artifact"):
                continue
            if latest is None or str(entry.get("timestamp", "")) > str(latest.get("timestamp", "")):
                latest = entry
    return latest


async def model_card() -> dict[str, Any]:
    """Return the current trained-classifier metadata for the Settings UI.

    Reads two on-disk sources:
      * ``~/.cache/zotero-summarizer/models/{classifier}.json`` — the JSON
        twin of the joblib artefact (n_train, oof_spearman, trained_at,
        git_commit, sha of the golden CSV used).
      * ``classifier-runs.jsonl`` in the project root — append-only FAIR
        log; the newest matching entry adds CV AUC, per-class metrics,
        thresholds, and the full config.

    Returns ``{"model": null}`` when no model is on disk yet so the UI
    can render an empty state instead of 404'ing.
    """
    md = _model_dir()
    if not md.exists():
        return {"model": None}

    # Find every classifier with a .json twin and pick the freshest by
    # mtime — the user may have multiple (logreg + lightgbm) on disk and
    # we want to surface the one most recently trained.
    candidates: list[tuple[float, Path]] = []
    for json_path in md.glob("*.json"):
        joblib_path = json_path.with_suffix(".joblib")
        if not joblib_path.exists():
            continue
        candidates.append((json_path.stat().st_mtime, json_path))
    if not candidates:
        return {"model": None}
    candidates.sort(reverse=True)
    _, freshest = candidates[0]

    twin = json.loads(freshest.read_text(encoding="utf-8"))
    classifier_name = str(twin.get("classifier_name") or freshest.stem)
    runlog = _load_latest_runlog_entry(classifier_name)

    joblib_path = freshest.with_suffix(".joblib")
    joblib_stat = joblib_path.stat()

    return {
        "model": {
            "classifier_name": classifier_name,
            "trained_at": twin.get("trained_at"),
            "git_commit": twin.get("git_commit"),
            "n_train": twin.get("n_train"),
            "n_positive_library": twin.get("n_positive_library"),
            "feature_dim": twin.get("feature_dim"),
            "objective": twin.get("objective"),
            "oof_spearman": twin.get("oof_spearman"),
            # Forward-looking (train-past/test-future) Spearman; null on models
            # trained before June 2026 or when the holdout was too small.
            "temporal_spearman": twin.get("temporal_spearman"),
            "temporal_holdout_n": twin.get("temporal_holdout_n"),
            "golden_csv_sha256_prefix": str(twin.get("golden_csv_sha256") or "")[:12],
            "thresholds": twin.get("thresholds") or {},
            "joblib_path": str(joblib_path),
            "joblib_size_bytes": int(joblib_stat.st_size),
            "joblib_mtime": datetime.fromtimestamp(
                joblib_stat.st_mtime, tz=timezone.utc
            ).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "runlog": runlog,  # full JSONL entry; null if no log line found
        },
    }
