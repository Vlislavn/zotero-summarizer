"""One atomically published ZIP containing a joblib model and its metadata.

Legacy joblib inputs remain readable; their separate JSON twins are never trusted.
Only load locally trusted models: joblib deserialization can execute Python code.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import joblib

from zotero_summarizer.services._common import atomic_write


def model_path(model_dir: Path, classifier_name: str) -> Path:
    archive = model_dir / f"{classifier_name}.zip"
    legacy = model_dir / f"{classifier_name}.joblib"
    return legacy if not archive.exists() and legacy.exists() else archive


def _metadata(trained: Any) -> dict[str, Any]:
    return {
        **trained.training_metadata,
        "classifier_name": trained.classifier_name,
        "golden_csv_sha256": trained.golden_csv_sha256,
        "feature_dim": trained.feature_dim,
        "pca_dim": trained.pca_dim,
        "thresholds": {
            "keep": round(trained.t_keep, 4),
            "must": round(trained.t_must, 4),
            "could": round(trained.t_could, 4),
        },
    }


def load_trained(path: Path) -> Any:
    # One open descriptor binds identity to the loaded bytes across atomic replacements.
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
        source.seek(0)
        if path.suffix == ".joblib":
            trained = joblib.load(source)
        else:
            with ZipFile(source) as archive, archive.open("model.joblib") as payload:
                trained = joblib.load(payload)
                recorded = json.loads(archive.read("metadata.json"))
                if json.dumps(recorded, sort_keys=True) != json.dumps(_metadata(trained), sort_keys=True):
                    raise ValueError("Model archive metadata does not match its payload")
    trained.model_sha256 = digest
    return trained


def read_metadata(path: Path) -> dict[str, Any]:
    if path.suffix == ".joblib":
        return _metadata(load_trained(path))
    with ZipFile(path) as archive:
        archive.getinfo("model.joblib")
        return json.loads(archive.read("metadata.json"))


def write_archive(trained: Any, path: Path) -> None:
    digest: str

    def write(target: Path) -> None:
        nonlocal digest
        with ZipFile(target, "w") as archive:
            with archive.open("model.joblib", "w", force_zip64=True) as payload:
                joblib.dump(trained, payload)
            archive.writestr("metadata.json", json.dumps(_metadata(trained), ensure_ascii=False, allow_nan=False))
        with target.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()

    atomic_write(path, write)
    trained.model_sha256 = digest
