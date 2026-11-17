"""Prompt + response schema of the method card (kept OUT of ``_review_identity._prompt_contract_text``:
editing it re-stamps method cards, never reviews)."""
from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, Field

from zotero_summarizer.services.library._prompt_security import paper_block

CARD_VERSION = 1

_PROMPT = (
    "The whole paper (without its reference list) is given above. Explain HOW ITS METHOD WORKS for a reader "
    "deciding whether to reproduce it. Use ONLY the paper text; never use memory or other papers. "
    "Every item needs a `quote`: ONE WHOLE sentence copied verbatim from the paper that states it. "
    "Leave an item out (empty string / empty list) rather than guess.\n\n"
    "- core_idea.text: at most 45 words, in the paper's own wording, shaped 'X turns A into B by C' "
    "(the mechanism only: no libraries, hyperparameters or praise); explain any new term in the same sentence.\n"
    "- compared: at most 4 rows, each one measured comparison the paper reports. `what` = the NAMED baseline or "
    "alternative; `result` = the numbers exactly as written (at most 25 words); the quote is the sentence that "
    "contains BOTH the name and the number.\n"
    "- novelty_claim.quote: the sentence where the authors state what is new in their work.\n"
    "- reuse: at most 3 steps to repeat the method, each `input` -> `operation` -> `output` (short noun phrases, "
    "no 'use X'); the quote states that step.\n"
    "- use_when: at most 25 words completing 'Use it when ...' as a condition about the reader's situation.\n"
    "- caveat: the limitation that sits next to the headline result (at most 25 words) with its quote.\n\n"
    'Return ONE strict JSON object: {"core_idea": {"text": "", "quote": ""}, '
    '"compared": [{"what": "", "result": "", "quote": ""}], "novelty_claim": {"quote": ""}, '
    '"reuse": [{"input": "", "operation": "", "output": "", "quote": ""}], "use_when": "", '
    '"caveat": {"text": "", "quote": ""}}. Start { end }.'
)


class _Idea(BaseModel):
    text: str = Field(default="")
    quote: str = Field(default="")


class _Compared(BaseModel):
    what: str = Field(default="")
    result: str = Field(default="")
    quote: str = Field(default="")


class _Claim(BaseModel):
    quote: str = Field(default="")


class _Step(BaseModel):
    input: str = Field(default="")
    operation: str = Field(default="")
    output: str = Field(default="")
    quote: str = Field(default="")


class CardResponse(BaseModel):
    core_idea: _Idea = Field(default_factory=_Idea)
    compared: list[_Compared] = Field(default_factory=list)
    novelty_claim: _Claim = Field(default_factory=_Claim)
    reuse: list[_Step] = Field(default_factory=list)
    use_when: str = Field(default="")
    caveat: _Idea = Field(default_factory=_Idea)


def prompt_sha() -> str:
    """Stamp part: the prompt text, the schema and the card version."""
    payload = json.dumps([CARD_VERSION, _PROMPT, CardResponse.model_json_schema()], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def build_prompt(body: str) -> str:
    """Paper block first (byte-identical to the review's prefix), then the card instructions."""
    return paper_block(body) + _PROMPT
