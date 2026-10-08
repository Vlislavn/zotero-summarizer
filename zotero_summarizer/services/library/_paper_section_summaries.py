"""One grounded sentence per paper section — "what this section covers" — for the
story page's Paper map.

One batched LLM generation over the review's OWN sections (heading + a text prefix),
keyed back to ``section_id``. A second batched pass checks each candidate against the
same selected original-text prefix; a verbatim quote must also belong to that prefix.
A best-effort enrichment — the ``deep_review`` layer boundary wraps both calls, and
the Paper map renders titles + pages without unsupported or unavailable summaries.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr
from zotero_summarizer.services.library._grounding import quote_is_grounded
from zotero_summarizer.services.library._prompt_security import UNTRUSTED_INPUT_RULE, untrusted_input

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
    '"summary": "...", "supporting_quote": "..."}}, ...]}} — one entry per index above. '
    'The quote must be copied verbatim from the supplied section text. Start {{ end }}.'
)

_SECTION_SUPPORT_PROMPT = (
    "Independently verify each proposed one-sentence summary using only the exact "
    "heading and selected original-text prefix supplied during generation. Decide "
    "whether the whole summary is stated or directly entailed by that prefix; a valid quote or "
    "shared topic alone is not support. Faithful paraphrases are supported. Never "
    "use another candidate's section to support a summary. Preserve both the index "
    "and section_id from each candidate, in the same order.\n\n{blocks}\n\n"
    'Return ONE strict JSON object: {{"checks": [{{"index": <int>, '
    '"section_id": "...", "supported": <bool>, "supporting_quote": "..."}}, ...]}} '
    '— exactly one check per candidate. For supported=true, quote a verbatim span '
    'from that prefix that helps establish the summary; for false, use an empty quote. '
    'Start {{ end }}.'
)


class _SectionLine(BaseModel):
    index: int = Field(default=-1)
    summary: str = Field(default="")
    supporting_quote: str = Field(default="")


class _SectionSummaryResponse(BaseModel):
    sections: list[_SectionLine] = Field(default_factory=list)


class _SectionSupportCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    index: StrictInt
    section_id: StrictStr
    supported: StrictBool
    supporting_quote: StrictStr


class _SectionSupportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    checks: list[_SectionSupportCheck]


class SectionVerifierUnavailable(ValueError):
    """No complete, correctly mapped support verdicts were returned."""


@dataclass(frozen=True)
class _SectionCandidate:
    index: int
    section_id: str
    title: str
    summary: str
    text: str


def _verify_candidates(candidates: list[_SectionCandidate], llm: Any) -> set[int]:
    expected = [(candidate.index, candidate.section_id) for candidate in candidates]
    if len(set(expected)) != len(expected) or len({section_id for _, section_id in expected}) != len(expected):
        raise SectionVerifierUnavailable("Section verifier candidates have duplicate mappings")
    blocks = untrusted_input("\n\n".join(
        f"[Candidate index={c.index} section_id={c.section_id}]\n"
        f"Heading: {c.title}\nProposed summary: {c.summary}\n"
        f"Selected original-section text:\n{c.text}"
        for c in candidates
    ))
    parsed = llm.pydantic_prompt(
        prompt=UNTRUSTED_INPUT_RULE + "\n\n" + _SECTION_SUPPORT_PROMPT.format(blocks=blocks),
        pydantic_model=_SectionSupportResponse,
    )
    actual = [(check.index, check.section_id) for check in parsed.checks]
    if len(set(actual)) != len(actual) or actual != expected:
        raise SectionVerifierUnavailable(
            "Section verifier omitted, duplicated, unknown, or cross-section outcomes"
        )
    by_index = {candidate.index: candidate for candidate in candidates}
    for check in parsed.checks:
        quote = check.supporting_quote
        if check.supported and not quote_is_grounded(quote, by_index[check.index].text):
            raise SectionVerifierUnavailable(
                f"Section verifier cited no source span for index {check.index}"
            )
        if quote and not quote_is_grounded(quote, by_index[check.index].text):
            raise SectionVerifierUnavailable(
                f"Section verifier cited another section for index {check.index}"
            )
    return {check.index for check in parsed.checks if check.supported}


def summarize_sections(sections: list[dict[str, Any]], llm: Any) -> dict[str, str]:
    """Return supported ``{section_id: one_sentence}`` entries.

    One batched generation call is followed by one batched support check against
    each candidate's selected original-text prefix. Errors propagate (the ``deep_review``
    layer boundary degrades this optional enrichment). Invalid generation rows,
    unsupported claims and sections without body text are absent from the map.
    """
    usable = [s for s in (sections or []) if str(s.get("text") or "").strip()][:_MAX_SECTIONS]
    if not usable:
        return {}
    selected_sections = [
        (section, str(section.get("text") or "")[:_SECTION_BODY_CHARS])
        for section in usable
    ]
    blocks = untrusted_input("\n\n".join(
        f"[Section {i}] {section.get('title') or 'Section'}\n{selected_text}"
        for i, (section, selected_text) in enumerate(selected_sections)
    ))
    generated = llm.pydantic_prompt(
        prompt=UNTRUSTED_INPUT_RULE + "\n\n" + _SECTION_SUMMARY_PROMPT.format(blocks=blocks),
        pydantic_model=_SectionSummaryResponse,
    )
    candidates = []
    for line in generated.sections or []:
        i = int(line.index)
        if not 0 <= i < len(usable):
            continue
        section, selected_text = selected_sections[i]
        summary = " ".join(str(line.summary or "").split()).strip()
        if not summary or not quote_is_grounded(line.supporting_quote, selected_text):
            continue
        section_id = str(section.get("id") or "")
        if section_id:
            candidates.append(_SectionCandidate(
                index=i, section_id=section_id, title=str(section.get("title") or "Section"),
                summary=summary, text=selected_text,
            ))
    if not candidates:
        return {}
    supported = _verify_candidates(candidates, llm)
    return {candidate.section_id: candidate.summary for candidate in candidates
            if candidate.index in supported}


__all__ = ["summarize_sections"]
