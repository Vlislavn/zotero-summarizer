"""The loaded gate's metadata for Settings and setup readiness.

A disk artifact or evaluation run is not evidence of what this process serves.
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.services._common import state


async def model_card() -> dict[str, Any]:
    """Snapshot the current gate reference; no disk reads or model loading."""
    gate = state().classifier_gate
    if gate is None:
        return {"model": None}
    metadata = gate.training_metadata
    return {"model": {
        "classifier_name": gate.classifier_name,
        "n_train": metadata.get("n_train"),
        "trained_at": metadata.get("trained_at"),
        "oof_spearman_verified": metadata.get("oof_spearman_verified"),
    }}
