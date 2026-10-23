"""Frozen #37 admission and native/binary capabilities; no model constructors."""
from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, model_validator

SUPPORT_OPTIONS = ('supports', 'contradicts', 'insufficient_evidence')
PROTOCOL = '8345f333951c661d166b00e6f9362e553768f292'
OWNERS = {'verifier': '0ab02c05166636e032d37f7dd87992e272f6ccb894d8071171462b924d6fb3d2',
          'minicheck': '6a5fdfe030701fb8887fc1b20c1b6c64ae82addd8c63043c2ffe81e0fa2f6d44'}
Sha = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
Text = Annotated[str, Field(min_length=1, pattern=r'\S')]
Positive = Annotated[int, Field(strict=True, gt=0)]
Probability = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
Label = Literal['supports', 'contradicts', 'insufficient_evidence']


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      indent=2).encode('utf-8')


def digest(value) -> str:
    return sha(encoded(value))


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON member')
        result[key] = value
    return result


def _constant(value):
    raise ValueError('nonfinite JSON constant')


def loads(raw: bytes):
    return json.loads(raw.decode('utf-8'), object_pairs_hook=_object, parse_constant=_constant)


class Frozen(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class FileRef(Frozen):
    path: Path
    sha256: Sha

    @model_validator(mode='after')
    def absolute_path(self):
        if not self.path.is_absolute():
            raise ValueError('frozen inputs require an absolute path')
        return self


def read_bound(ref: FileRef, cap: int) -> bytes:
    with ref.path.open('rb') as stream:
        raw = stream.read(cap + 1)
    if len(raw) > cap or sha(raw) != ref.sha256:
        raise ValueError('input byte cap or SHA-256 mismatch')
    return raw


class SupportCase(Frozen):
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    pair_id: Text
    paper_key: Text
    criterion: Text
    source_sha256: Sha
    span_start: Annotated[int, Field(strict=True, ge=0)]
    span_end: Positive
    excerpt: Text
    excerpt_sha256: Sha


def admit_support_case(data, original_text: str) -> SupportCase:
    case = SupportCase.model_validate(data)
    if sha(original_text.encode('utf-8')) != case.source_sha256:
        raise ValueError('original source SHA-256 mismatch')
    if not case.span_start < case.span_end <= len(original_text):
        raise ValueError('Unicode character span out of bounds')
    if original_text[case.span_start:case.span_end] != case.excerpt:
        raise ValueError('exact original occurrence mismatch')
    if sha(case.excerpt.encode('utf-8')) != case.excerpt_sha256:
        raise ValueError('excerpt SHA-256 mismatch')
    return case


def normalized(text: str) -> str:
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def gold_identity(case: SupportCase, original: str) -> str:
    return digest([normalized(original), normalized(case.criterion), normalized(case.excerpt),
                   case.span_start, case.span_end])


class Source(Frozen):
    paper_key: Text
    family: Text
    split: Literal['development', 'holdout']
    origin: Literal['original']
    text: Text


class Gold(Frozen):
    identity: Sha
    label: Label
    human_provenance: Text | None


class Calibration(Frozen):
    split_sha256: Sha
    development_families: tuple[Text, ...]
    method: Text
    frozen_at: AwareDatetime


class SupportArm(Frozen):
    arm_id: Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]+$')]
    display_name: Text
    kind: Literal['native', 'recorded', 'blocked']
    model: Text
    endpoint: str | None = None
    protocol: Literal[PROTOCOL] = PROTOCOL
    runtime_sha256: Sha | None = None
    checkpoint_sha256: Sha | None = None
    quantization: Text | None = None
    model_source: Text | None = None
    licence: Text | None = None
    eligibility: Literal['qualified', 'unknown', 'ineligible'] = 'unknown'
    access: Literal['qualified', 'unknown'] = 'unknown'
    context_verified: StrictBool = False
    forward_passes_bound: Positive | None = None
    abstention_threshold: Probability | None = None
    blocked_reason: Text | None = None

    def blockage(self):
        if self.kind == 'blocked':
            return self.blocked_reason or 'explicitly blocked'
        if self.kind == 'native' and not all((self.eligibility == 'qualified',
                self.access == 'qualified', self.runtime_sha256, self.checkpoint_sha256,
                self.quantization, self.model_source, self.licence, self.context_verified,
                self.forward_passes_bound)):
            return 'unqualified access/checkpoint/runtime/licence/context'
        return None


class Baseline(Frozen):
    arm_id: Text
    display_name: Text
    kind: Literal['verifier', 'minicheck']
    model: Text
    artifact: FileRef | None
    threshold: Probability | None = None


class BaselineRow(Frozen):
    case_sha256: Sha
    verdict: str | None = None
    score: Probability | None = None


class BaselineArtifact(Frozen):
    kind: Literal['verifier', 'minicheck']
    model: Text
    owner_sha256: Sha
    source_reference: Text
    projection: Literal['criterion-original-excerpt']
    critical_met_grounded: StrictBool
    rows: tuple[BaselineRow, ...]

    @model_validator(mode='after')
    def eligible(self):
        if not self.critical_met_grounded:
            raise ValueError('baseline must be matched to the critical/met/grounded pair cohort')
        return self


class NativeUsage(Frozen):
    input_tokens: Annotated[int, Field(strict=True, ge=0, le=2147483647)]
    output_tokens: Annotated[int, Field(strict=True, ge=0, le=0)]


class NativeSupportReadout(Frozen):
    label: Label
    probabilities: tuple[Probability, Probability, Probability]
    observed_model: Text
    confidence: Probability
    usage: NativeUsage


class SupportResult(Frozen):
    case_sha256: Sha
    pair_id: Text
    arm_id: Text
    family: Text
    split: Literal['development', 'holdout']
    gold: Label | None
    status: Literal['decision', 'threshold_abstention', 'model_mismatch', 'invalid_response',
                    'timeout', 'unavailable', 'response_too_large', 'encoded_response', 'blocked', 'wall_budget']
    support: StrictBool | None
    readout: NativeSupportReadout | None
    baseline_native: BaselineRow | None
    capability: Literal['native_three_way', 'binary_support']
    telemetry: dict
    request_sha256: Sha | None = None
    terminal: Annotated[str, Field(pattern=r'^terminal-[0-9]+\.bin$')] | None = None
    reason: str | None = None

    @model_validator(mode='after')
    def consistent(self):
        if (self.support is not None) != (self.status == 'decision'):
            raise ValueError('unscored outcome cannot be a semantic binary decision')
        native = self.capability == 'native_three_way'
        if native and self.baseline_native is not None:
            raise ValueError('native readout cannot manufacture a binary baseline artifact')
        expected = native and self.status in ('decision', 'threshold_abstention', 'model_mismatch')
        if (self.readout is not None) != expected:
            raise ValueError('native readout/status mismatch')
        if native and self.status == 'decision' and self.support != (self.readout.label == 'supports'):
            raise ValueError('binary native projection disagrees with preserved choice')
        if not native and self.status == 'decision' and self.baseline_native is None:
            raise ValueError('binary decision requires its original artifact row')
        return self


class Recording(Frozen):
    arm_id: Text
    case_sha256: Sha
    terminal: FileRef


class FrozenSupportInput(Frozen):
    sources: tuple[Source, ...]
    cases: tuple[SupportCase, ...]
    gold: tuple[Gold, ...] = ()
    recordings: tuple[Recording, ...] = ()
    split_frozen_at: AwareDatetime
    holdout_opened_at: AwareDatetime | None
    synthetic: StrictBool


class Config(Frozen):
    project_root: Path
    attempt: Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]+$')]
    inputs: FileRef
    arms: tuple[SupportArm, ...]
    baselines: tuple[Baseline, ...]
    reference: Text
    generator_handle: Text
    grader_handle: Text
    calibration: Calibration | None = None
    option_order: tuple[str, ...] = tuple(sorted(SUPPORT_OPTIONS))
    max_input_bytes: Annotated[int, Field(strict=True, gt=0, le=67108864)] = 8388608
    max_pairs: Annotated[int, Field(strict=True, gt=0, le=10000)] = 120
    max_prompt_chars: Annotated[int, Field(strict=True, gt=0, le=131072)] = 12000
    max_request_bytes: Annotated[int, Field(strict=True, gt=0, le=1048576)] = 65536
    max_response_bytes: Annotated[int, Field(strict=True, gt=0, le=1048576)] = 65536
    max_calls: Annotated[int, Field(strict=True, gt=0, le=10000)] = 200
    max_work_units: Positive = 400
    idle_timeout_seconds: Annotated[float, Field(strict=True, gt=0, le=600, allow_inf_nan=False)] = 120
    max_run_seconds: Annotated[float, Field(strict=True, gt=0, le=7200, allow_inf_nan=False)] = 3600
    external_guard: Text | None = None
    public_input_consent: StrictBool = False
    probability_sum_tolerance: Annotated[float, Field(strict=True, ge=0, le=0.001, allow_inf_nan=False)] = 0.000001
    min_pairs: Annotated[int, Field(strict=True, ge=80)] = 80
    min_papers: Annotated[int, Field(strict=True, ge=20)] = 20
    min_coverage: Probability = 0.9
    max_false_confirmation: Probability = 0.05
    max_false_rejection: Probability = 0.1
    max_operational_error: Probability = 0.05
    noninferiority_margin: Probability = 0.02
    max_latency_seconds: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)] = 120
    max_cost: Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)] = 0
    max_peak_memory_bytes: Positive = 1073741824
    bootstrap_samples: Annotated[int, Field(strict=True, ge=200, le=10000)] = 2000
    seed: Annotated[int, Field(strict=True)] = 37

    @model_validator(mode='after')
    def coherent(self):
        ids = [a.arm_id for a in (*self.arms, *self.baselines)]
        if (len(ids) > 32 or len(set(ids)) != len(ids)
                or self.reference not in [a.arm_id for a in self.baselines]):
            raise ValueError('unique arms and explicit baseline reference required')
        if not self.project_root.is_absolute() or self.generator_handle == self.grader_handle:
            raise ValueError('absolute selected root and separate generator/grader handles required')
        if self.option_order != tuple(sorted(SUPPORT_OPTIONS)):
            raise ValueError('freeze lexical ordering across all b11430 arms (including CLEF)')
        if any(a.abstention_threshold is not None for a in self.arms) or any(
                a.threshold is not None for a in self.baselines):
            if self.calibration is None:
                raise ValueError('thresholds require frozen development-only provenance')
        return self


def admit(cfg: Config, bundle: FrozenSupportInput):
    sources = {s.paper_key: s for s in bundle.sources}
    if len(sources) != len(bundle.sources) or not 0 < len(bundle.cases) <= cfg.max_pairs:
        raise ValueError('unique sources and nonempty bounded pairs required')
    splits, text_families = {}, {}
    for source in sources.values():
        canonical = sha(normalized(source.text).encode())
        if canonical in text_families and text_families[canonical] != source.family:
            raise ValueError('canonical source aliases must share a paper family')
        text_families[canonical] = source.family
        for key in (('family', source.family), ('text', canonical)):
            if key in splits and splits[key] != source.split:
                raise ValueError('whole-family/canonical-text split leakage')
            splits[key] = source.split
    cases, identities, pair_ids = {}, set(), set()
    for case in bundle.cases:
        if case.paper_key not in sources:
            raise ValueError('case references unknown original source')
        source = sources[case.paper_key]
        admit_support_case(case, source.text)
        key = digest(case.model_dump())
        identity = gold_identity(case, source.text)
        if key in cases or identity in identities or case.pair_id in pair_ids:
            raise ValueError('duplicate pair/normalized identity')
        cases[key] = case
        identities.add(identity)
        pair_ids.add(case.pair_id)
    gold = {g.identity: g for g in bundle.gold}
    if len(gold) != len(bundle.gold) or not set(gold) <= identities:
        raise ValueError('duplicate or unmatched grader identity')
    if bundle.holdout_opened_at and bundle.split_frozen_at >= bundle.holdout_opened_at:
        raise ValueError('split must precede holdout opening')
    if cfg.calibration:
        cal = cfg.calibration
        split = [(s.paper_key, s.family, s.split) for s in bundle.sources]
        dev = {s.family for s in bundle.sources if s.split == 'development'}
        if (cal.split_sha256 != digest(split) or not cal.development_families
                or not set(cal.development_families) <= dev or not bundle.holdout_opened_at
                or not bundle.split_frozen_at <= cal.frozen_at < bundle.holdout_opened_at):
            raise ValueError('development/holdout calibration firewall')
    return sources, cases, gold
