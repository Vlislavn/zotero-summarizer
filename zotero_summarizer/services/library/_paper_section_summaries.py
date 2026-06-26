"""One grounded sentence per paper section — "what this section covers" — for the
story page's Paper map.

ONE batched LLM call over the review's OWN sections (heading + the start of each
section's text), keyed back to ``section_id``. Anti-fabrication, mirroring the
digest's discipline: describe only what a section's text says; an empty summary
beats an invented fact/number. A best-effort enrichment — the ``deep_review``
layer boundary wraps this, and the Paper map renders titles + pages without it.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# Body chars per section fed to the summarizer — enough to characterize a section
# without spending the prompt budget on a long paper's full body.
_SECTION_BODY_CHARS = 700
# Sentinel cap (real papers have ~10-15 detected sections); bounds the one call.
_MAX_SECTIONS = 24

_SECTION_SUMMARY_PROMPT = (
    "Below are the numbered sections of ONE paper, each with its heading and the "
    "start of its text. For EACH section, write ONE short sentence saying what that "
    "section COVERS (its topic / role in the paper), using ONLY its own text. Do "
    "NOT invent facts, numbers, or findings; if a section's text is too sparse to "
    'tell, return an empty string for it.\n\n{blocks}\n\n'
    'Return ONE strict JSON object: {{"sections": [{{"index": <int 0-based>, '
    '"summary": "..."}}, ...]}} — one entry per index above. Start {{ end }}.'
)


class _SectionLine(BaseModel):
    index: int = Field(default=-1)
    summary: str = Field(default="")


class _SectionSummaryResponse(BaseModel):
    sections: list[_SectionLine] = Field(default_factory=list)


def summarize_sections(sections: list[dict[str, Any]], llm: Any) -> dict[str, str]:
    """``{section_id: one_sentence}`` for sections that have body text. ONE LLM call.

    Errors propagate (the ``deep_review`` layer boundary wraps this enrichment).
    Sections with no body text, an out-of-range index, or an empty model summary
    are simply absent from the map."""
    usable = [s for s in (sections or []) if str(s.get("text") or "").strip()][:_MAX_SECTIONS]
    if not usable:
        return {}
    blocks = "\n\n".join(
        f"[Section {i}] {s.get('title') or 'Section'}\n{str(s.get('text') or '')[:_SECTION_BODY_CHARS]}"
        for i, s in enumerate(usable)
    )
    parsed = llm.pydantic_prompt(
        prompt=_SECTION_SUMMARY_PROMPT.format(blocks=blocks),
        pydantic_model=_SectionSummaryResponse,
    )
    out: dict[str, str] = {}
    for line in parsed.sections or []:
        i = int(line.index)
        if 0 <= i < len(usable):
            summary = " ".join(str(line.summary or "").split()).strip()
            if summary:
                out[str(usable[i].get("id") or "")] = summary
    return out


__all__ = ["summarize_sections"]
