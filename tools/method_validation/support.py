"""Offline #37 criterion/evidence contract; no endpoint, runner or semantic grader."""
from __future__ import annotations

import json
from dataclasses import dataclass
from math import fsum
from typing import Annotated, Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, model_validator

from zotero_summarizer.services.faithbench._corpus import sha256_text
from zotero_summarizer.services.library._prompt_security import UNTRUSTED_INPUT_RULE, untrusted_input

SupportLabel = Literal['supports', 'contradicts', 'insufficient_evidence']
SUPPORT_OPTIONS: tuple[SupportLabel, ...] = ('supports', 'contradicts', 'insufficient_evidence')
Sha256 = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
Nonblank = Annotated[str, Field(min_length=1, pattern=r'\S')]
Probability = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]


class _FrozenContract(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class SupportCase(_FrozenContract):
    """Generator projection only; offsets are Unicode character indices, not bytes."""

    schema_version: StrictInt = Field(ge=1, le=1)
    pair_id: Nonblank
    paper_key: Nonblank
    criterion: Nonblank
    source_sha256: Sha256
    span_start: StrictInt = Field(ge=0)
    span_end: StrictInt = Field(gt=0)
    excerpt: Nonblank
    excerpt_sha256: Sha256


def admit_support_case(data: dict[str, Any], original_text: str) -> SupportCase:
    """Check the exact supplied frozen extraction, never generated summaries/gold.

    Binding proves literal occurrence only; trusted source acquisition and semantic
    labels remain separate. No trimming, normalization, search or source substitution.
    """
    case = SupportCase.model_validate(data)
    if sha256_text(original_text) != case.source_sha256:
        raise ValueError('frozen source SHA-256 mismatch')
    if not case.span_start < case.span_end <= len(original_text):
        raise ValueError('original source span out of bounds')
    if original_text[case.span_start:case.span_end] != case.excerpt:
        raise ValueError('original source occurrence differs from excerpt')
    if sha256_text(case.excerpt) != case.excerpt_sha256:
        raise ValueError('excerpt SHA-256 mismatch')
    return case


class SupportArm(_FrozenContract):
    """Caller-frozen policy, not recommended scientific thresholds or live consent."""

    arm_id: Nonblank
    model: Nonblank
    runtime_sha256: Sha256
    max_prompt_chars: StrictInt = Field(gt=0)
    probability_sum_tolerance: Annotated[float, Field(strict=True, ge=0, lt=1, allow_inf_nan=False)]
    abstention_threshold: Probability | None


class NativeSupportReadout(_FrozenContract):
    label: SupportLabel
    probabilities: tuple[Probability, Probability, Probability]
    observed_model: Nonblank


@dataclass(frozen=True)
class ProviderReply:
    """An adapter preserves raw provider text separately from its native projection.

    Native adapters are not supplied here; endpoint formats must be validated from
    their actual APIs. Single-confidence/binary baselines cannot invent this vector.
    """

    raw_response: str
    readout: dict[str, Any]


class SupportResult(_FrozenContract):
    case: SupportCase
    arm: SupportArm
    prompt_sha256: Sha256
    readout_schema_sha256: Sha256
    status: Literal['decision', 'threshold_abstention', 'invalid_response', 'model_mismatch',
                    'timeout', 'unavailable', 'input_too_large']
    logical_calls: StrictInt = Field(ge=0, le=1)
    raw_response: str | None = None
    readout: NativeSupportReadout | None = None

    @model_validator(mode='after')
    def consistent_outcome(self) -> SupportResult:
        attempted = self.status != 'input_too_large'
        if self.logical_calls != int(attempted):
            raise ValueError('status disagrees with logical call accounting')
        has_readout = self.status in ('decision', 'threshold_abstention', 'model_mismatch')
        if (self.readout is not None) != has_readout:
            raise ValueError('status disagrees with native readout availability')
        has_response = has_readout or self.status == 'invalid_response'
        if (self.raw_response is not None) != has_response:
            raise ValueError('status disagrees with raw response availability')
        if self.readout is not None:
            if abs(fsum(self.readout.probabilities) - 1) > self.arm.probability_sum_tolerance:
                raise ValueError('probability vector must sum to one within declared tolerance')
            if _readout_status(self.arm, self.readout) != self.status:
                raise ValueError('status disagrees with frozen model/abstention policy')
        return self


def _support_prompt(case: SupportCase) -> str:
    return (
        f'{UNTRUSTED_INPUT_RULE}\n'
        'Decide criterion-evidence support using ONLY the supplied original excerpt.\n'
        'Internal validation alone does not establish external validation or prove its absence elsewhere.\n'
        f'Criterion:\n{untrusted_input(case.criterion)}\n'
        f'Original excerpt:\n{untrusted_input(case.excerpt)}\n'
        'Options in fixed order: supports / contradicts / insufficient_evidence.\n'
        'Preserve the native decision and complete probability vector; do not invent a rationale.'
    )


def _readout_status(arm: SupportArm, readout: NativeSupportReadout) -> str:
    if readout.observed_model != arm.model:
        return 'model_mismatch'
    confidence = readout.probabilities[SUPPORT_OPTIONS.index(readout.label)]
    if arm.abstention_threshold is not None and confidence < arm.abstention_threshold:
        return 'threshold_abstention'
    return 'decision'


def evaluate_support(
    data: dict[str, Any], original_text: str, arm: SupportArm,
    provider: Callable[[str, tuple[SupportLabel, ...]], ProviderReply],
) -> SupportResult:
    """Admit -> one injected adapter call -> structural result; never grade meaning.

    No retries, model loading, endpoint access, persistence, calibration or product
    policy changes. A caller must separately admit any real provider/data/budget.
    """
    case = admit_support_case(data, original_text)
    prompt = _support_prompt(case)
    common = {'case': case, 'arm': arm, 'prompt_sha256': sha256_text(prompt),
              'readout_schema_sha256': sha256_text(json.dumps(
                  NativeSupportReadout.model_json_schema(), sort_keys=True, ensure_ascii=True))}
    if len(prompt) > arm.max_prompt_chars:
        return SupportResult(**common, status='input_too_large', logical_calls=0)
    try:
        reply = provider(prompt, SUPPORT_OPTIONS)
    except TimeoutError:
        return SupportResult(**common, status='timeout', logical_calls=1)
    except ConnectionError:
        return SupportResult(**common, status='unavailable', logical_calls=1)
    if not isinstance(reply, ProviderReply) or not isinstance(reply.raw_response, str):
        raise TypeError('adapter must return ProviderReply with raw text')
    try:
        readout = NativeSupportReadout.model_validate(reply.readout)
    except ValidationError:
        readout = None
    if readout is None or abs(fsum(readout.probabilities) - 1) > arm.probability_sum_tolerance:
        return SupportResult(**common, status='invalid_response', logical_calls=1,
                             raw_response=reply.raw_response)
    return SupportResult(**common, status=_readout_status(arm, readout), logical_calls=1,
                         raw_response=reply.raw_response, readout=readout)
