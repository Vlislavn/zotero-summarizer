"""What the app proposed at the moment the owner decided (#224, REQ-S19).

Stored on the verdict row (``label_verdicts.proposal_json``) so app-vs-owner
agreement stays measurable after the live scores and reviews move on. Pure
read of the row + review cache; never writes. Best effort: a missing piece is
``None``, never an error that blocks the owner's verdict.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zotero_summarizer.domain import READING_PRIORITY_SORT_RANK
from zotero_summarizer.services.library import _review_cache
from zotero_summarizer.storage.feed_identity import row_feed_keys
from zotero_summarizer.storage import feeds as feeds_storage

PROPOSAL_VERSION = 1


def _review_for(keys: list[str]) -> dict[str, Any]:
    for key in keys:
        try:
            entry = _review_cache.get_usable_review(key)
        except Exception:  # noqa: BLE001 — the snapshot must never block a verdict
            entry = None
        if entry:
            return entry
    return {}


def load_feed_row(db_path: Path, item_key: str) -> dict[str, Any] | None:
    """The processed feed row behind a ``feed:`` verdict key (None for Zotero items)."""
    if not item_key.startswith("feed:"):
        return None
    with feeds_storage.open_triage_conn(db_path) as conn:
        row = feeds_storage.get_processed_feed_item_by_stable_key(conn, item_key)
    return dict(row) if row is not None else None


def build_proposal(
    *, surface: str, row: dict[str, Any] | None = None, item_key: str = "",
    priority: str | None = None,
) -> str:
    """JSON snapshot: proposed verdict, rank score, grade, band, freshness, surface, ids."""
    row = row or {}
    keys = row_feed_keys(row) if row else []
    if item_key and item_key not in keys:
        keys.append(item_key)
    review = _review_for(keys)
    quality = review.get("quality") or {}
    digest = review.get("model_digest") or review.get("digest") or {}
    prov = review.get("provenance") or {}
    proposed = priority if priority is not None else (row.get("reading_priority") or "").strip() or None
    snapshot = {
        "v": PROPOSAL_VERSION,
        "priority": proposed if proposed in READING_PRIORITY_SORT_RANK else None,
        "composite_score": row.get("composite_score"),
        "surprise_score": row.get("surprise_score"),
        "corpus_affinity": row.get("corpus_affinity"),
        "model_version": row.get("model_version"),
        "grade": quality.get("grade") or digest.get("grade") or None,
        "quality_band": quality.get("quality_band"),
        "read_decision": review.get("model_read_decision"),
        "review_freshness": (review.get("review_freshness") or {}).get("status"),
        "review_provider": prov.get("provider"),
        "review_model": prov.get("model"),
        "surface": surface,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    return json.dumps(snapshot, sort_keys=True)
