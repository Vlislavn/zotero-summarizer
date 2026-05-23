"""Session JSON I/O for the relabel-audit pipeline.

Persists the (unanswered) audit session so the UI can resume across
restarts. ``record_response`` is the ONLY mutator for the responses map;
the trickle module mutates only the top-level ``last_trickle_emitted_at``
field (never the responses).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zotero_summarizer.services.golden.relabel_audit._constants import (
    AUDIT_PRIORITY_NAMES,
    PRIORITY_TO_SCORE,
    AuditCandidate,
    AuditResponse,
    now_iso,
)


def write_session(
    session_path: Path,
    candidates: list[AuditCandidate],
    *,
    sample_size: int,
    seed: int,
) -> None:
    """Persist the (unanswered) audit session to disk so the UI can resume."""
    payload = {
        "type": "relabel_audit_session",
        "version": 1,
        "created_at": now_iso(),
        "sample_size": sample_size,
        "seed": seed,
        "candidates": [
            {
                "item_key": c.item_key,
                "title": c.title,
                "authors": c.authors,
                "venue": c.venue,
                "abstract": c.abstract,
                "days_since_added": c.days_since_added,
                "age_bucket": c.age_bucket,
                "original_priority": c.original_priority,
                "original_inferred_relevance": c.original_inferred_relevance,
            }
            for c in candidates
        ],
        "responses": {},
    }
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def read_session(session_path: Path) -> dict[str, Any]:
    if not session_path.exists():
        raise FileNotFoundError(f"audit session not found at {session_path}")
    return json.loads(session_path.read_text(encoding="utf-8"))


def record_response(
    session_path: Path, item_key: str, new_priority: str,
) -> dict[str, Any]:
    """Mutate the session JSON, recording one response. Returns the updated session."""
    if new_priority not in AUDIT_PRIORITY_NAMES:
        raise ValueError(
            f"new_priority must be one of {AUDIT_PRIORITY_NAMES}; got {new_priority!r}"
        )
    session = read_session(session_path)
    if not any(c["item_key"] == item_key for c in session["candidates"]):
        raise ValueError(
            f"item_key {item_key!r} not in this session — "
            f"reject any UI-supplied keys outside the sampled set"
        )
    session["responses"][item_key] = {
        "new_priority": new_priority,
        "new_relevance": PRIORITY_TO_SCORE[new_priority],
        "timestamp_iso": now_iso(),
    }
    session_path.write_text(
        json.dumps(session, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return session


def responses_from_session(session: dict[str, Any]) -> list[AuditResponse]:
    """Pair candidates with their recorded responses; skips unanswered."""
    candidates_by_key = {c["item_key"]: c for c in session["candidates"]}
    out: list[AuditResponse] = []
    for item_key, resp in (session.get("responses") or {}).items():
        cand = candidates_by_key.get(item_key)
        if cand is None:
            raise ValueError(
                f"session has response for {item_key!r} but no candidate — "
                f"corrupted session JSON"
            )
        out.append(
            AuditResponse(
                item_key=item_key,
                original_priority=cand["original_priority"],
                original_inferred_relevance=float(cand["original_inferred_relevance"]),
                new_priority=resp["new_priority"],
                new_relevance=float(resp["new_relevance"]),
                timestamp_iso=resp["timestamp_iso"],
                age_bucket=cand["age_bucket"],
            )
        )
    return out


__all__ = ["write_session", "read_session", "record_response", "responses_from_session"]
