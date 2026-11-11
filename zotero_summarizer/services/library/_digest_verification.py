"""Fail-closed output-to-source verification for a generated PaperDigest."""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, StrictBool, StrictInt

from zotero_summarizer.models import PaperDigest
from zotero_summarizer.services._common import extract_json_blob, to_text
from zotero_summarizer.services.library._prompt_security import UNTRUSTED_INPUT_RULE, untrusted_input
from zotero_summarizer.services.library._grounding import quote_is_grounded


# Passage IDs a supported claim may cite. The verifier often returns 4 while the
# response schema capped the list at 3 and rejected the WHOLE response (1-2 extra
# verifier calls per paper, #149); extras are now dropped instead.
MAX_EVIDENCE = 3


class _Check(BaseModel):
    index: StrictInt
    supported: StrictBool
    evidence: list[StrictInt] = Field(default_factory=list)


class _Checks(BaseModel):
    checks: list[_Check] = Field(default_factory=list)
    _repair_attempts: int = PrivateAttr(default=0)


class _ScientificCheck(_Check):
    model_config = ConfigDict(strict=True, extra='forbid')


class _ScientificChecks(_Checks):
    model_config = ConfigDict(strict=True, extra='forbid')
    checks: list[_ScientificCheck] = Field(default_factory=list)


class DigestSourceRejected(ValueError):
    """A well-formed generated digest failed original-source grounding."""

    def __init__(self, message: str, *, failure_kind: str, rejected_claims: list[str] | None = None,
                 checks: '_Checks | None' = None):
        super().__init__(message)
        self.failure_kind = failure_kind
        self.rejected_claims = tuple(rejected_claims or ())
        self.checks = checks  # every verdict of the verifier run (None before it ran: numeric preflight)


class DigestVerifierUnavailable(ValueError):
    """The verifier produced no valid contract; distinct from a factual reject."""


_PROMPT = UNTRUSTED_INPUT_RULE + """

Paper text (numbered verbatim passages):
{paper}

Reader goals (preference context, not paper evidence):
{goals}
"""

_TASK = """Verify ONLY the following {claim_count} digest fields against the original paper.
Return exactly {claim_count} checks, one per CLAIM_ID, without omissions or duplicates.
`index` is a CLAIM_ID in {claim_ids}; `evidence` contains PASSAGE_IDs in {passage_ids}.
Claim IDs and passage IDs are separate namespaces: their positions have no relationship.
Do not produce one check per paper passage or infer support from matching positions.

This digest is a peer review, not a list of findings attributed to the authors.
Judge each WHOLE field according to what it asserts:
- Every factual premise, number, method, result or claim that something is absent
  must be justified by the cited original passages, even inside a reviewer judgment.
- Check numerical claims against the rest of the supplied source, not just a
  matching literal or quotation. If the source reports conflicting values, units,
  scales or denominators for the same result, an unqualified claim is unsupported.
  Explicitly bounded reporting of that inconsistency can be supported; do not
  silently choose a value, normalize the discrepancy or invent a correction.
- Reading advice, quality judgments, conditional impacts and bounded implications
  are reviewer assessments. Their factual premises and the connection to the
  assessment need support; the paper need not state the assessment or demonstrate
  that a conditional future benefit has already occurred.
- Distinguish a question about what measurements establish from an assertion of
  a defect or increased risk. Respect the source's stated safeguards and limits.
- Reject invented mechanisms, causal chains, failure predictions or population-
  specific risks that require additional evidence. 'May' or 'could' alone is not
  support. A topical association is not a grounded premise.
- Reader goals establish preference fit, never paper facts. Not directly matching
  a reader goal does not mean the paper lacks that domain, method or application.
Use supported=true only when all factual premises and the bounded assessment are
justified; cite 1-3 actual supporting PASSAGE_IDs, not rewritten quotes.
For contradicted or unsupported fields use supported=false, evidence=[].
Never invent a premise or turn verification feedback into paper evidence.

Digest fields:
{claims}

Return ONE strict JSON object with `checks`, a list of objects containing only
`index` (integer CLAIM_ID), `supported` (boolean), `evidence` (integer PASSAGE_ID list).
Choose each support decision from the original evidence; no default support verdict.
"""

# PDF word/number joins still contain literals; identifier/range hyphens are not signs.
_NUMBER_RE = re.compile(
    r"(?<![\d.%])(?:(?<!\w)[+-])?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:\s*(?:%|percent))?",
    re.IGNORECASE,
)
_CLAIM_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+")


def _claim_parts(path: str, value: Any) -> list[str]:
    if value is None:
        return []
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
            re.sub(r"\s*(?:%|percent)$", "", value.casefold()).replace(",", "").removeprefix("+")
            for value in _NUMBER_RE.findall(text.replace('\N{MINUS SIGN}', '-'))
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


def _keep_grounded_evidence(check: Any, passages: list[str], paper_text: str) -> None:
    """Keep the first ``MAX_EVIDENCE`` distinct passage IDs that really ground.

    A duplicate, out-of-range or ungrounded ID is dropped, not fatal: the claim is
    still accepted only through a passage that exists, grounds verbatim and was
    actually cited — and a supported claim left with none still fails."""
    if not check.supported:
        return
    kept: list[int] = []
    for passage_id in check.evidence:
        if passage_id in kept or passage_id < 0 or passage_id >= len(passages):
            continue
        if not quote_is_grounded(passages[passage_id], paper_text):
            continue
        kept.append(passage_id)
        if len(kept) == MAX_EVIDENCE:
            break
    check.evidence = kept


def _parse_checks(raw: Any, claim_count: int, passages: list[str], paper_text: str) -> _Checks:
    if isinstance(raw, _Checks):
        parsed = raw
    else:
        parsed = _Checks.model_validate(raw if isinstance(raw, dict) else extract_json_blob(to_text(raw)))
    if len(parsed.checks) != claim_count or {c.index for c in parsed.checks} != set(range(claim_count)):
        raise ValueError("Digest verifier omitted or duplicated fields")
    for check in parsed.checks:
        if check.supported and not check.evidence:
            raise ValueError(f"Verifier omitted evidence for index {check.index}")
        _keep_grounded_evidence(check, passages, paper_text)
        if check.supported and not check.evidence:
            raise ValueError(f"Verifier cited invalid passage IDs for index {check.index}")
    return parsed


def _invalid_observation(raw: Any, claim_count: int, error: ValueError, attempt: int) -> tuple[dict, str]:
    reasons = (
        ('Digest verifier omitted or duplicated fields', 'verifier_coverage_invalid'),
        ('Verifier omitted evidence', 'verifier_evidence_missing'),
        ('Verifier cited invalid passage IDs', 'verifier_evidence_invalid'),
    )
    message, code = next(((prefix, code) for prefix, code in reasons if str(error).startswith(prefix)),
                         ('Verifier response did not match the required schema', 'verifier_schema_invalid'))
    checks = raw.checks if isinstance(raw, _Checks) else raw.get('checks') if isinstance(raw, dict) else None
    facts = {'reason_code': code, 'expected_checks': claim_count,
             'returned_checks': len(checks) if isinstance(checks, list) else None, 'verifier_attempt': attempt + 1}
    return facts, message


def verify_digest(
    digest: PaperDigest, paper_text: str, llm: Any, *, research_goals: str = "",
    scientific_claims: list[str] | None = None, only_claims: list[str] | None = None,
) -> _Checks:
    from zotero_summarizer.services.faithbench._corpus import chunk_text
    from zotero_summarizer.services.library._review_attempt import observed_client, record

    claims = _claims(digest) if scientific_claims is None else scientific_claims
    if only_claims is not None:  # re-check just these base-digest claims (field repair), same base contract
        claims = only_claims
    if scientific_claims is not None and not claims:
        raise ValueError('Optional verification requires scientific claims')
    unsupported = _unsupported_literals(claims, paper_text)
    if unsupported:
        raise DigestSourceRejected(f"Digest contains source-absent literals: {unsupported[:3]}",
                                   failure_kind="numeric_preflight", rejected_claims=unsupported)
    passages = chunk_text(paper_text)
    task = _TASK.format(
        claim_count=len(claims), claim_ids=f'0..{len(claims) - 1}' if claims else '(none)',
        passage_ids=f'0..{len(passages) - 1}',
        claims=untrusted_input("\n".join(f"[{i}] CLAIM_ID={i}: {claim}" for i, claim in enumerate(claims))),
    )
    prompt = _PROMPT.format(
        paper="\n\n".join(f"PASSAGE {i}:\n{untrusted_input(p)}" for i, p in enumerate(passages)),
        goals=untrusted_input(research_goals or "(none)"),
    ) + '\n' + task
    model = _Checks if scientific_claims is None else _ScientificChecks
    for attempt in range(2):
        raw = None
        try:
            raw = observed_client(llm, 'verifier').pydantic_prompt(prompt=prompt, pydantic_model=model)
            if scientific_claims is not None and not isinstance(raw, _ScientificChecks):
                raw = _ScientificChecks.model_validate(raw if isinstance(raw, dict) else extract_json_blob(to_text(raw)))
            parsed = _parse_checks(raw, len(claims), passages, paper_text)
            break
        except ValueError as exc:
            facts, message = _invalid_observation(raw, len(claims), exc, attempt)
            record('verifier_validation', facts, kind='identity')
            if attempt:
                raise DigestVerifierUnavailable(f"Digest verifier returned an invalid response twice: {message}") from exc
            prompt += (f"\nYour previous verification was invalid: {message}; "
                       f"expected {facts['expected_checks']}; returned {facts['returned_checks']}.\n" + task)
    failed = [claims[check.index] for check in parsed.checks if not check.supported]
    if failed:
        raise DigestSourceRejected(f"Digest contains unsupported fields: {failed[:3]}",
                                   failure_kind="verifier_support_not_established", rejected_claims=failed,
                                   checks=parsed)
    parsed._repair_attempts = attempt
    return parsed
