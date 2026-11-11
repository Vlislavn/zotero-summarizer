"""Daemon training ledger producer, using only provenance retained on its artifact."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zotero_summarizer.services import run_log
from zotero_summarizer.services._common import now_iso_z
from zotero_summarizer.settings import Settings


def _hash_identity(value: str | None) -> dict[str, Any]:
    if value is None or value == "":
        return {"value": None, "scope": "unavailable", "reason": "not_retained_on_artifact"}
    if not isinstance(value, str):
        raise TypeError("retained hash identity must be a string or null")
    if len(value) <= 64 and all(c in "0123456789abcdefABCDEF" for c in value):
        return {"value": value, "scope": "sha256_full" if len(value) == 64 else "sha256_prefix", "reason": None}
    return {"value": value, "scope": "opaque", "reason": "retained_value_is_not_a_sha256_digest"}


def append_daemon_training_record(settings: Settings, trained: Any, golden_csv: Path) -> None:
    """Append after install/rescore succeeds; serialization and append errors propagate.

    Paths are locators, not evidence of their current contents. The composite input
    identity belongs to the actual trainer's snapshot, not a post-training read.
    """
    metadata = trained.training_metadata
    entry = {
        "run_id": run_log.make_run_id(trained.classifier_name),
        "timestamp": now_iso_z(),
        "classifier": trained.classifier_name,
        "type": "train_artifact",
        "producer": "daemon-retrain",
        "identity_scope": "gate_training",
        "input_csv": str(golden_csv),
        "model_dir": str(settings.model_dir),
        "git_commit": metadata.get("git_commit") or None,
        "identities": {
            "golden_csv": _hash_identity(getattr(trained, "golden_csv_sha256", None)),
            "training_input": _hash_identity(metadata.get("training_input_sha256")),
            "model": _hash_identity(getattr(trained, "model_sha256", None)),
            "source": {"value": None, "scope": "unavailable", "reason": "not_separately_retained"},
            "config": {"value": None, "scope": "unavailable", "reason": "not_separately_retained"},
            "seed": {"value": None, "scope": "unavailable", "reason": "not_retained_on_artifact"},
            "historical_inference_model": {
                "value": None, "scope": "unavailable", "reason": "training_is_not_historical_inference",
            },
        },
        "training_metadata": metadata,
        "cv": {
            "n_rows": metadata.get("n_train"),
            "auc": metadata.get("oof_auc"),
            "metrics_vs_gold": metadata.get("oof_metrics_vs_gold"),
        },
    }
    # The shared legacy writer permits nonfinite JSON; this new producer does not.
    json.dumps(entry, ensure_ascii=False, allow_nan=False)
    run_log.append_run(settings.classifier_runs_log_path, entry)
