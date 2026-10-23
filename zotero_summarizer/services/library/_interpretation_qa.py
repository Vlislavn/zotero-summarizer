"""Private comprehensive-QA placement of one qualified digest/view snapshot.

Prompt text is not authority: the caller keeps same-base legacy text separately.
No qualification, evidence resolution, source read or persistence lives here.
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.services.library import _interpretation_view, _paper_read_meta as meta
from zotero_summarizer.services.library._review_identity import _validate_interpretation_schema


def question_context(
    item_key: str, artifact: dict[str, Any], *, max_chars: int, interpretation_schema: str,
) -> tuple[str, str, dict[str, str]]:
    """Read exactly one existing qualified snapshot for an explicit C invocation."""
    _validate_interpretation_schema(interpretation_schema)
    review = _interpretation_view.current_review_view(item_key, interpretation_schema=interpretation_schema)
    return allocate_context(artifact, max_chars=max_chars, review=review)


def allocate_context(
    artifact: dict[str, Any], *, max_chars: int, review: dict[str, Any] | None,
) -> tuple[str, str, dict[str, str]]:
    """Return legacy L, supplied A and status; never truncate a complete bundle."""
    metadata, structured = meta.artifact_text_fields(artifact, review=review)
    prefix = meta.structured_qa_parts(metadata, structured)
    body = meta.qa_body_text(artifact)
    legacy = meta.assemble_qa_text(prefix, body)[:max_chars]
    if review is None:
        return legacy, legacy, {"availability": "unavailable", "reason": "review_unavailable"}
    view = review["interpretation_view"]
    if view["availability"] == "unavailable":
        return legacy, legacy, {"availability": "unavailable", "reason": view["reason"]}

    # Origin is structural, even if the raw body also occurs in a generated field.
    # Actual sliced length preserves Python's zero/negative-cap admission behavior.
    body_start = len(meta.assemble_qa_text(prefix, "")) + (2 if body else 0)
    admitted_body = body[:max(0, len(legacy) - body_start)]
    retained = {key: value for key, value in structured.items() if key != "digest"}
    bundle = {"digest": review["digest"], "interpretation_view": view}
    candidate = meta.assemble_qa_text(meta.structured_qa_parts(metadata, retained), admitted_body, after_body=bundle)
    if len(candidate) > max_chars:
        return legacy, legacy, {"availability": "unavailable", "reason": "context_budget"}
    return legacy, candidate, {"availability": "available", "reason": ""}
