"""Atomic JSON sidecar for the review-fleet's PROPOSED verdicts.

One file — ``proposed_verdicts.json`` under the model dir — keyed by ``item_key``,
each value a serialized ``ProposedVerdict``. This is the fleet's only persistence:
``fleet`` upserts a proposal per paper and ``reading_queue`` reads them all back to
attach ``proposed_verdict`` to each row.

It mirrors ``deep_review``'s cache idiom exactly — same ``{updated_at, ...}``
envelope, same ``tmp.replace(path)`` atomic write — and resolves its path via
``Settings.model_dir`` in the selected project's ``data/`` directory.

This store holds SUGGESTIONS only. It is distinct from ``label_verdicts`` (the
user's confirmed labels in the triage DB); a proposal here NEVER writes a label or
touches Zotero — that stays an explicit user Confirm/Override flow.
"""
from __future__ import annotations

import json
import hashlib
import threading
from typing import Any

from zotero_summarizer.services._common import LOGGER, now_iso_z, settings, write_json_atomic
from zotero_summarizer.services.library._review_cache import _quarantine_corrupt_cache

_CACHE_FILENAME = "proposed_verdicts.json"
_CACHE_LOCK = threading.RLock()
PROPOSAL_VERSION = 1


def _cache_path():
    return settings().model_dir / _CACHE_FILENAME


def read_all() -> dict[str, Any]:
    """Every stored proposal as ``{item_key: proposed_verdict_dict}``.

    ``{}`` when absent or corrupt. Corrupt bytes are moved aside with a warning;
    proposals are regenerable suggestions, so a damaged sidecar must not disable
    reading-queue access."""
    with _CACHE_LOCK:
        path = _cache_path()
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            backup = _quarantine_corrupt_cache(path)
            LOGGER.warning("quarantined corrupt review-fleet verdicts to %s (%s)", backup, exc)
            return {}
        proposals = payload.get("proposals") if isinstance(payload, dict) else None
        if not isinstance(proposals, dict):
            backup = _quarantine_corrupt_cache(path)
            LOGGER.warning("quarantined invalid review-fleet verdict envelope to %s", backup)
            return {}
        return proposals


def _write_all(proposals: dict[str, Any]) -> None:
    write_json_atomic(_cache_path(), {"updated_at": now_iso_z(), "proposals": proposals})


def upsert(item_key: str, proposal: dict[str, Any]) -> None:
    """Insert or replace the proposal for ``item_key`` (read-modify-atomic-write).

    Called serially from the single-flight fleet job, so the read-modify-write is
    not racing a second fleet run; concurrent READERS see whole files only
    (``tmp.replace`` is atomic)."""
    if not item_key:
        raise ValueError("upsert requires a non-empty item_key")
    with _CACHE_LOCK:
        proposals = read_all()
        proposals[item_key] = proposal
        _write_all(proposals)


def clear(item_key: str) -> bool:
    """Drop the stored proposal for ``item_key`` (e.g. after the user Confirms or
    Overrides it, so it stops being suggested). Returns whether one was removed."""
    if not item_key:
        raise ValueError("clear requires a non-empty item_key")
    with _CACHE_LOCK:
        proposals = read_all()
        if item_key not in proposals:
            return False
        del proposals[item_key]
        _write_all(proposals)
        return True


def proposal_matches_review(proposal: Any, review: Any) -> bool:
    """Accept a suggestion only for the review identity that produced it."""
    if not isinstance(proposal, dict) or not isinstance(review, dict):
        return False
    identity = review_fingerprint(review)
    if not identity:
        return False
    return (proposal.get("proposal_version") == PROPOSAL_VERSION
            and proposal.get("review_identity_sha256") == identity)


def review_fingerprint(review: dict[str, Any]) -> str:
    identity = review.get("review_identity")
    if not isinstance(identity, dict):
        identity = {key: review.get(key) for key in ("digest", "quality", "goal_summaries")}
        if not any(identity.values()):
            return ""
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = ["read_all", "upsert", "clear", "proposal_matches_review",
           "review_fingerprint", "PROPOSAL_VERSION"]
