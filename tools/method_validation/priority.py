"""Pure #38 four-class projection; no adapter, grader, persistence or product policy."""
from __future__ import annotations

import json
from math import fsum
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError, model_validator

from tools.method_validation.support import Nonblank, Probability, ProviderReply, Sha256, SupportArm
from zotero_summarizer.domain import READING_PRIORITY_VALUES, ReadingPriority
from zotero_summarizer.services.faithbench._corpus import sha256_text
from zotero_summarizer.services.library._prompt_security import UNTRUSTED_INPUT_RULE, untrusted_input

PRIORITY_OPTIONS = READING_PRIORITY_VALUES
PriorityStatus = Literal['decision', 'threshold_abstention', 'invalid_response', 'model_mismatch',
                         'timeout', 'unavailable', 'input_too_large', 'missing_input']


class PriorityCase(BaseModel):
    """Generator-only frozen request, not a labelled export or verified human truth.

    The caller supplies canonical identity and exact evidence. Absent/blank title
    or abstract is retained for a missing_input receipt, never sent to a provider.
    """

    model_config = ConfigDict(extra='forbid', frozen=True)
    schema_version: StrictInt = Field(ge=1, le=1)
    canonical_id: Nonblank
    title: StrictStr | None = None
    abstract: StrictStr | None = None
    goals: tuple[Nonblank, ...] = Field(min_length=1)


class PriorityArm(SupportArm):
    """Reuse #37 budgets; caller freezes ranks in canonical option order.

    Ranks strictly descend from must_read to dont_read. They map the native
    chosen class only, not a production composite or a fabricated probability.
    Calibration/development provenance and runtime admission remain caller-owned.
    """

    experiment_id: Nonblank
    ordinal_mapping: tuple[StrictInt, StrictInt, StrictInt, StrictInt]

    @model_validator(mode='after')
    def ordered_ranks(self) -> PriorityArm:
        if any(left <= right for left, right in zip(self.ordinal_mapping, self.ordinal_mapping[1:])):
            raise ValueError('ordinal ranks must strictly descend in canonical option order')
        return self


class NativePriorityReadout(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    label: ReadingPriority
    probabilities: tuple[Probability, Probability, Probability, Probability]
    observed_model: Nonblank


def _missing_input(case: PriorityCase) -> bool:
    return not (case.title and case.title.strip() and case.abstract and case.abstract.strip())


def _priority_prompt(case: PriorityCase) -> str:
    goals = '\n'.join(untrusted_input(goal) for goal in case.goals)
    return (
        f'{UNTRUSTED_INPUT_RULE}\n'
        'Classify personal reading priority using only the supplied title, abstract and research goals.\n'
        f'Title:\n{untrusted_input(case.title)}\n'
        f'Abstract:\n{untrusted_input(case.abstract)}\n'
        f'Research goals:\n{goals}\n'
        f'Options in fixed order: {" / ".join(PRIORITY_OPTIONS)}.\n'
        'Preserve the native chosen class and all four native probabilities in that order; '
        'do not invent a rationale.'
    )


def _digest(value: Any) -> str:
    return sha256_text(json.dumps(value, sort_keys=True, ensure_ascii=True))


def _identities(case: PriorityCase, arm: PriorityArm, prompt: str | None) -> dict[str, str | None]:
    return {'input_sha256': _digest(case.model_dump(mode='json')),
            'arm_sha256': _digest(arm.model_dump(mode='json')),
            'prompt_sha256': None if prompt is None else sha256_text(prompt),
            'readout_schema_sha256': _digest(NativePriorityReadout.model_json_schema())}


def _preflight(arm: PriorityArm, prompt: str | None) -> PriorityStatus | None:
    if prompt is None:
        return 'missing_input'
    if len(prompt) > arm.max_prompt_chars:
        return 'input_too_large'
    return None


def _readout_status(arm: PriorityArm, readout: NativePriorityReadout) -> PriorityStatus:
    if readout.observed_model != arm.model:
        return 'model_mismatch'
    confidence = readout.probabilities[PRIORITY_OPTIONS.index(readout.label)]
    if arm.abstention_threshold is not None and confidence < arm.abstention_threshold:
        return 'threshold_abstention'
    return 'decision'


class PriorityResult(BaseModel):
    """Structural receipt only: unscored states never acquire an ordinal rank.

    Hashes bind declared inputs, not trusted acquisition, human provenance or the
    actual provider/runtime. Reopening checks internal consistency, not authenticity.
    """

    model_config = ConfigDict(extra='forbid', frozen=True)
    case: PriorityCase
    arm: PriorityArm
    input_sha256: Sha256
    arm_sha256: Sha256
    prompt_sha256: Sha256 | None
    readout_schema_sha256: Sha256
    status: PriorityStatus
    logical_calls: StrictInt = Field(ge=0, le=1)
    raw_response: StrictStr | None = None
    readout: NativePriorityReadout | None = None
    ordinal_value: StrictInt | None = None

    @model_validator(mode='after')
    def consistent_outcome(self) -> PriorityResult:
        prompt = None if _missing_input(self.case) else _priority_prompt(self.case)
        for field, expected in _identities(self.case, self.arm, prompt).items():
            if getattr(self, field) != expected:
                raise ValueError(f'{field} differs from frozen contract')
        preflight = _preflight(self.arm, prompt)
        if preflight is not None and self.status != preflight:
            raise ValueError('status disagrees with input admission')
        if preflight is None and self.status in ('missing_input', 'input_too_large'):
            raise ValueError('input failure requires missing or oversized evidence')
        if self.logical_calls != int(preflight is None):
            raise ValueError('status disagrees with logical call accounting')
        has_readout = self.status in ('decision', 'threshold_abstention', 'model_mismatch')
        if (self.readout is not None) != has_readout:
            raise ValueError('status disagrees with native readout availability')
        if (self.raw_response is not None) != (has_readout or self.status == 'invalid_response'):
            raise ValueError('status disagrees with raw response availability')
        if self.readout is not None:
            if abs(fsum(self.readout.probabilities) - 1) > self.arm.probability_sum_tolerance:
                raise ValueError('native probabilities must sum to one within declared tolerance')
            if self.status != _readout_status(self.arm, self.readout):
                raise ValueError('status disagrees with frozen model/threshold policy')
        ordinal = None
        if self.status == 'decision' and self.readout is not None:
            ordinal = self.arm.ordinal_mapping[PRIORITY_OPTIONS.index(self.readout.label)]
        if self.ordinal_value != ordinal:
            raise ValueError('only native decisions receive their frozen chosen-class rank')
        return self


def evaluate_priority(
    data: dict[str, Any], arm: PriorityArm,
    provider: Callable[[str, tuple[str, ...]], ProviderReply],
) -> PriorityResult:
    """Frozen generator projection -> at most ONE injected call -> native receipt.

    No trimming/clipping, retry, fallback, baseline conversion, cost estimates,
    semantic scoring, calibration, native endpoint access or human-export reader.
    The adapter owns real version/readout evidence; this seam cannot certify it or
    enforce its physical-call/time/resource budget. Gold never belongs in data.
    """
    case = PriorityCase.model_validate(data)
    prompt = None if _missing_input(case) else _priority_prompt(case)
    common = {'case': case, 'arm': arm, **_identities(case, arm, prompt)}
    preflight = _preflight(arm, prompt)
    if preflight is not None:
        return PriorityResult(**common, status=preflight, logical_calls=0)
    try:
        reply = provider(prompt, PRIORITY_OPTIONS)
    except TimeoutError:
        return PriorityResult(**common, status='timeout', logical_calls=1)
    except ConnectionError:
        return PriorityResult(**common, status='unavailable', logical_calls=1)
    if not isinstance(reply, ProviderReply) or not isinstance(reply.raw_response, str):
        raise TypeError('adapter must return ProviderReply with raw text')
    try:
        readout = NativePriorityReadout.model_validate(reply.readout)
    except ValidationError:
        readout = None
    if readout is None or abs(fsum(readout.probabilities) - 1) > arm.probability_sum_tolerance:
        return PriorityResult(**common, status='invalid_response', logical_calls=1,
                              raw_response=reply.raw_response)
    status = _readout_status(arm, readout)
    ordinal = arm.ordinal_mapping[PRIORITY_OPTIONS.index(readout.label)] if status == 'decision' else None
    return PriorityResult(**common, status=status, logical_calls=1, raw_response=reply.raw_response,
                          readout=readout, ordinal_value=ordinal)
