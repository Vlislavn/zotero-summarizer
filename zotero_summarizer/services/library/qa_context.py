"""Deterministic multi-turn context and extraction-versioned evidence handles."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from zotero_summarizer.services.library._paper_read_meta import qa_body_text

_SPACE = re.compile(r"\s+")


def _digest(value: str) -> str:
    return hashlib.sha256(_SPACE.sub(" ", value).strip().encode()).hexdigest()


def extraction_version(artifact: dict[str, Any]) -> str:
    return str(artifact.get("pdf_key") or _digest(qa_body_text(artifact))[:16])


def evidence_handle(
    item_key: str, artifact: dict[str, Any], question: str, quote: str | None,
) -> dict[str, Any] | None:
    if not quote:
        return None
    body = qa_body_text(artifact)
    start = body.find(quote)
    return {
        "paper_id": item_key,
        "extraction_version": extraction_version(artifact),
        "span": {"start": start, "end": start + len(quote)} if start >= 0 else None,
        "quote_digest": _digest(quote),
        "input_digest": _digest(question),
    }


def verified_quote(item_key: str, artifact: dict[str, Any], handle: Any) -> str | None:
    if not isinstance(handle, dict) or handle.get("paper_id") != item_key:
        return None
    if handle.get("extraction_version") != extraction_version(artifact):
        return None
    span = handle.get("span")
    if not isinstance(span, dict):
        return None
    body = qa_body_text(artifact)
    try:
        quote = body[int(span["start"]):int(span["end"])]
    except (KeyError, TypeError, ValueError):
        return None
    return quote if _digest(quote) == handle.get("quote_digest") else None


def citation(item_key: str, artifact: dict[str, Any], handle: Any, *, answered: bool) -> dict[str, Any]:
    return {
        "claimed": answered,
        "quote_verified": answered and isinstance(handle, dict),
        "location_verified": verified_quote(item_key, artifact, handle) is not None,
        "evidence_handle": handle,
    }


def compact_history(
    item_key: str, artifact: dict[str, Any], history: list[dict[str, Any]], *, tail: int = 4,
) -> tuple[str, bool, int]:
    """Keep a conversational tail; older answers become resolvable evidence handles."""
    turns = history[-20:]
    raw = "\n".join(_turn_text(turn) for turn in turns)
    if len(turns) <= tail:
        return raw, False, 0
    older, recent = turns[:-tail], turns[-tail:]
    checkpoints = []
    for turn in older:
        handle = turn.get("evidence_handle")
        if verified_quote(item_key, artifact, handle) is None:
            continue
        checkpoints.append(
            f"evidence:{handle['quote_digest'][:16]} (resolve against the current extraction before exact claims)"
        )
    compacted = "\n".join([
        "Earlier evidence checkpoint: " + ("; ".join(checkpoints) or "no current verified handles"),
        *[_turn_text(turn) for turn in recent],
    ])
    return (compacted, True, len(checkpoints)) if len(compacted) < len(raw) else (raw, False, 0)


def _turn_text(turn: dict[str, Any]) -> str:
    question = str(turn.get("question") or "").strip()
    answer = str(turn.get("answer") or "[abstained]").strip()
    quote = str(turn.get("quote") or "").strip()
    return f"User: {question}\nAssistant: {answer}" + (f"\nVerified quote: {quote}" if quote else "")


__all__ = ["citation", "compact_history", "evidence_handle", "extraction_version", "verified_quote"]
