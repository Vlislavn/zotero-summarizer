"""Atomic JSON sidecar for the review-fleet's PROPOSED verdicts.

One file — ``proposed_verdicts.json`` under the model dir — keyed by ``item_key``,
each value a serialized ``ProposedVerdict``. This is the fleet's only persistence:
``fleet`` upserts a proposal per paper and ``reading_queue`` reads them all back to
attach ``proposed_verdict`` to each row.

It mirrors ``deep_review``'s cache idiom exactly — same ``{updated_at, ...}``
envelope, same ``tmp.replace(path)`` atomic write — and resolves its path via
``classifier_persistence.DEFAULT_MODEL_DIR`` (the app's model dir, per CLAUDE.md
rule 4 — never a hardcoded ``project_root / "..."``).

This store holds SUGGESTIONS only. It is distinct from ``label_verdicts`` (the
user's confirmed labels in the triage DB); a proposal here NEVER writes a label or
touches Zotero — that stays an explicit user Confirm/Override flow.
"""
from __future__ import annotations

import json
from typing import Any

from zotero_summarizer.services._common import now_iso_z

_CACHE_FILENAME = "proposed_verdicts.json"


def _cache_path():
    from zotero_summarizer.services.model.classifier_persistence import DEFAULT_MODEL_DIR

    return DEFAULT_MODEL_DIR / _CACHE_FILENAME


def read_all() -> dict[str, Any]:
    """Every stored proposal as ``{item_key: proposed_verdict_dict}``.

    ``{}`` when the file does not exist yet (the fleet has not run). A malformed
    file raises out of ``json.loads`` at this I/O boundary rather than being
    silently treated as empty — a corrupt cache is a signal, not a no-op."""
    path = _cache_path()
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("proposals") or {}


def _write_all(proposals: dict[str, Any]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"updated_at": now_iso_z(), "proposals": proposals}, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(path)


def upsert(item_key: str, proposal: dict[str, Any]) -> None:
    """Insert or replace the proposal for ``item_key`` (read-modify-atomic-write).

    Called serially from the single-flight fleet job, so the read-modify-write is
    not racing a second fleet run; concurrent READERS see whole files only
    (``tmp.replace`` is atomic)."""
    if not item_key:
        raise ValueError("upsert requires a non-empty item_key")
    proposals = read_all()
    proposals[item_key] = proposal
    _write_all(proposals)


def clear(item_key: str) -> bool:
    """Drop the stored proposal for ``item_key`` (e.g. after the user Confirms or
    Overrides it, so it stops being suggested). Returns whether one was removed."""
    if not item_key:
        raise ValueError("clear requires a non-empty item_key")
    proposals = read_all()
    if item_key not in proposals:
        return False
    del proposals[item_key]
    _write_all(proposals)
    return True


__all__ = ["read_all", "upsert", "clear"]
