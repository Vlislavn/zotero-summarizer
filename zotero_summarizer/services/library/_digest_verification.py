"""Fail-closed output-to-source verification for a generated PaperDigest."""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from zotero_summarizer.models import PaperDigest
from zotero_summarizer.services._common import extract_json_blob, to_text
from zotero_summarizer.services.library._prompt_security import UNTRUSTED_INPUT_RULE, untrusted_input
from zotero_summarizer.services.library._grounding import quote_is_grounded


class _Check(BaseModel):
    index: int
    supported: bool
    quote: str = ""


class _Checks(BaseModel):
    checks: list[_Check] = Field(default_factory=list)


class DigestVerifierUnavailable(ValueError):
    """The verifier produced no valid contract; distinct from a factual reject."""


_PROMPT = UNTRUSTED_INPUT_RULE + """

Verify EVERY numbered field from a generated academic-paper digest against the
paper text. `supported=true` only when the field follows from the paper. For a
reading/quality recommendation, the paper evidence must reasonably justify it.
The quote need not state the subjective recommendation itself, but it must support
its factual premise; reader-goal fit may additionally use the goals below.
For each item return one verbatim supporting quote copied from the paper. A missing,
contradicted, or merely plausible field is unsupported. Do not omit indices.

Digest fields:
{claims}

Paper text:
{paper}

Reader goals (preference context, not paper evidence):
{goals}

Return one JSON object: {{"checks":[{{"index":0,"supported":true,"quote":"..."}}]}}"""

_NUMBER_RE = re.compile(
    r"(?<![\w.])[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:\s*(?:%|percent))?",
    re.IGNORECASE,
)
_CLAIM_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+")


def _claim_parts(path: str, value: Any) -> list[str]:
    if isinstance(value, dict):
        return [part for key, child in value.items() for part in _claim_parts(f"{path}.{key}", child)]
    if isinstance(value, list):
        return [part for index, child in enumerate(value) for part in _claim_parts(f"{path}[{index}]", child)]
    rendered = str(value).strip()
    return [f"{path}: {part}" for part in _CLAIM_SPLIT_RE.split(rendered) if part]


def _claims(digest: PaperDigest) -> list[str]:
    from zotero_summarizer.services.faithbench._build_claims import CLAIM_FIELDS

    out: list[str] = []
    values = digest.model_dump(mode="json")
    for field in CLAIM_FIELDS:
        value = values.get(field)
        if value in (None, "", [], {}):
            continue
        out.extend(_claim_parts(field, value))
    return out


def _unsupported_literals(claims: list[str], paper_text: str) -> list[str]:
    def numbers(text: str) -> set[str]:
        return {
            re.sub(r"\s*(?:%|percent)$", "", value.casefold()).replace(",", "")
            for value in _NUMBER_RE.findall(text)
        }

    source_numbers = numbers(paper_text)
    suspicious: list[str] = []
    for claim in claims:
        field, _, content = claim.partition(":")
        literals = set() if field in {
            "soundness", "novelty", "significance", "reproducibility", "clarity",
            "confidence", "estimated_read_minutes",
        } else numbers(content)
        if not literals.issubset(source_numbers):
            suspicious.append(claim)
    return suspicious


def _parse_checks(raw: Any) -> _Checks:
    if isinstance(raw, _Checks):
        return raw
    if isinstance(raw, dict):
        return _Checks.model_validate(raw)
    return _Checks.model_validate(extract_json_blob(to_text(raw)))


def verify_digest(
    digest: PaperDigest, paper_text: str, llm: Any, *, research_goals: str = "",
) -> None:
    claims = _claims(digest)
    unsupported = _unsupported_literals(claims, paper_text)
    if unsupported:
        raise ValueError(f"Digest contains source-absent literals: {unsupported[:3]}")
    prompt = _PROMPT.format(
        claims=untrusted_input("\n".join(f"[{i}] {claim}" for i, claim in enumerate(claims))),
        paper=untrusted_input(paper_text),
        goals=untrusted_input(research_goals or "(none)"),
    )
    try:
        raw = llm.pydantic_prompt(prompt=prompt, pydantic_model=_Checks)
        parsed = _parse_checks(raw)
    except ValueError:
        try:
            raw = llm.pydantic_prompt(
                prompt=prompt + "\nYour previous response was invalid. Return the JSON object only.",
                pydantic_model=_Checks,
            )
            parsed = _parse_checks(raw)
        except ValueError as retry_error:
            raise DigestVerifierUnavailable("Digest verifier returned invalid JSON twice") from retry_error
    by_index = {check.index: check for check in parsed.checks}
    if len(parsed.checks) != len(claims) or set(by_index) != set(range(len(claims))):
        raise ValueError("Digest verifier omitted or duplicated fields")
    failed = [claims[i] for i, check in by_index.items()
              if not check.supported or not quote_is_grounded(check.quote, paper_text)]
    if failed:
        raise ValueError(f"Digest contains unsupported fields: {failed[:3]}")
