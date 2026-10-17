"""Pure #42 collection input, lineage purge and native membership projection; no writes."""
from __future__ import annotations

import json
from datetime import date
from math import fsum
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, model_validator

from .support import (
    Nonblank, Probability, ProviderReply, Sha256, SupportArm,
    UNTRUSTED_INPUT_RULE, sha256_text, untrusted_input,
)

MembershipLabel = Literal['belongs', 'does_not_belong', 'insufficient_evidence']
MEMBERSHIP_OPTIONS: tuple[MembershipLabel, ...] = (
    'belongs', 'does_not_belong', 'insufficient_evidence',
)


class _FrozenContract(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class CollectionNode(_FrozenContract):
    key: Nonblank
    name: Nonblank
    parent_key: Nonblank | None
    path: tuple[Nonblank, ...] = Field(min_length=1)
    role: Literal['topical', 'project', 'workflow']
    description: Nonblank


class CollectionPlan(_FrozenContract):
    """Caller-frozen eligibility/ordering, never selected from held-out assignments."""

    schema_version: StrictInt = Field(ge=1, le=1)
    taxonomy_id: Nonblank
    nodes: tuple[CollectionNode, ...] = Field(min_length=1)
    eligible_keys: tuple[Nonblank, ...] = Field(min_length=1)
    question_keys: tuple[Nonblank, ...] = Field(min_length=1)
    selection_mode: Literal['full_tree', 'frozen_shortlist']
    shortlist_provenance: Nonblank | None

    @model_validator(mode='after')
    def valid_tree_and_selection(self) -> CollectionPlan:
        nodes = {node.key: node for node in self.nodes}
        if len(nodes) != len(self.nodes):
            raise ValueError('duplicate collection key')
        if any(node.parent_key is not None and node.parent_key not in nodes for node in self.nodes):
            raise ValueError('unknown parent collection key')
        for node in self.nodes:
            lineage, seen, current = [], set(), node
            while current is not None:
                if current.key in seen:
                    raise ValueError('collection hierarchy contains a cycle')
                seen.add(current.key)
                lineage.append(current.name)
                current = nodes[current.parent_key] if current.parent_key is not None else None
            if tuple(reversed(lineage)) != node.path:
                raise ValueError('collection path disagrees with full parent hierarchy')
        if len(set(self.eligible_keys)) != len(self.eligible_keys):
            raise ValueError('duplicate eligible collection key')
        if any(key not in nodes or nodes[key].role == 'workflow' for key in self.eligible_keys):
            raise ValueError('eligibility requires explicit existing topical/project keys')
        if (len(set(self.question_keys)) != len(self.question_keys)
                or not set(self.question_keys) <= set(self.eligible_keys)):
            raise ValueError('questions must use distinct eligible keys')
        if self.selection_mode == 'full_tree':
            if self.question_keys != self.eligible_keys or self.shortlist_provenance is not None:
                raise ValueError('full tree requires all eligible keys in declared order, no shortlist')
        elif self.shortlist_provenance is None:
            raise ValueError('shortlist requires caller-frozen non-gold provenance')
        return self


class PaperIdentity(_FrozenContract):
    paper_key: Nonblank
    canonical_key: Nonblank
    version_key: Nonblank
    published_on: date
    split: Literal['development', 'heldout']


class FrozenSplit(_FrozenContract):
    """Complete supplied canonical/version mapping; identity acquisition is caller-owned."""

    split_id: Nonblank
    provenance: Nonblank
    development_before: date
    papers: tuple[PaperIdentity, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def family_time_partition(self) -> FrozenSplit:
        keys, families, versions = set(), {}, {}
        for paper in self.papers:
            if paper.paper_key in keys:
                raise ValueError('duplicate paper key')
            keys.add(paper.paper_key)
            if families.setdefault(paper.canonical_key, paper.split) != paper.split:
                raise ValueError('canonical paper family crosses development/heldout split')
            version = (paper.canonical_key, paper.published_on)
            if versions.setdefault(paper.version_key, version) != version:
                raise ValueError('version identity is inconsistent')
            if paper.split == 'development' and paper.published_on >= self.development_before:
                raise ValueError('development paper violates frozen time cutoff')
        return self


class DerivedRecord(_FrozenContract):
    """All source keys of a supplied baseline/example derivation, not inferred lineage."""

    entry_id: Nonblank
    source_paper_keys: tuple[Nonblank, ...] = Field(min_length=1)
    derivation: Nonblank
    payload: Nonblank


class RemovedDerivation(_FrozenContract):
    channel: Literal['baseline', 'examples']
    record: DerivedRecord
    heldout_identities: tuple[PaperIdentity, ...] = Field(min_length=1)


def _heldout_sources(record: DerivedRecord, split: FrozenSplit) -> tuple[PaperIdentity, ...]:
    identities = {paper.paper_key: paper for paper in split.papers}
    if (len(set(record.source_paper_keys)) != len(record.source_paper_keys)
            or any(key not in identities for key in record.source_paper_keys)):
        raise ValueError('derivation needs distinct complete frozen source identities')
    return tuple(identities[key] for key in record.source_paper_keys
                 if identities[key].split == 'heldout')


class PurgedReferences(_FrozenContract):
    split: FrozenSplit
    baseline: tuple[DerivedRecord, ...]
    examples: tuple[DerivedRecord, ...]
    removed: tuple[RemovedDerivation, ...]

    @model_validator(mode='after')
    def clean_references_and_audit(self) -> PurgedReferences:
        for channel in ('baseline', 'examples'):
            kept = getattr(self, channel)
            removed = tuple(row for row in self.removed if row.channel == channel)
            ids = [row.entry_id for row in kept] + [row.record.entry_id for row in removed]
            if len(set(ids)) != len(ids):
                raise ValueError('duplicate derivation identity in channel')
            if any(_heldout_sources(row, self.split) for row in kept):
                raise ValueError('heldout derivation cannot remain in references/examples')
            for row in removed:
                if _heldout_sources(row.record, self.split) != row.heldout_identities:
                    raise ValueError('removal audit disagrees with supplied source identities')
        return self


def purge_heldout(
    split_data: dict[str, Any], baseline: list[dict[str, Any]], examples: list[dict[str, Any]],
) -> PurgedReferences:
    """Remove every declared heldout-family source and mixed derivation, without writes.

    Every version in a heldout family must be frozen as heldout, including older
    versions. No library search, title matching, hidden identity completion or tuning.
    """
    split = FrozenSplit.model_validate(split_data)
    kept: dict[str, list[DerivedRecord]] = {'baseline': [], 'examples': []}
    removed = []
    for channel, records in (('baseline', baseline), ('examples', examples)):
        for data in records:
            record = DerivedRecord.model_validate(data)
            heldout = _heldout_sources(record, split)
            if heldout:
                removed.append(RemovedDerivation(channel=channel, record=record, heldout_identities=heldout))
            else:
                kept[channel].append(record)
    return PurgedReferences(split=split, baseline=tuple(kept['baseline']),
                            examples=tuple(kept['examples']), removed=tuple(removed))


class CollectionCase(_FrozenContract):
    """Strict generator projection: no assignments, tags, gold, reference rows or examples."""

    schema_version: StrictInt = Field(ge=1, le=1)
    case_id: Nonblank
    paper_key: Nonblank
    title: Nonblank | None
    abstract: Nonblank | None
    evidence: Nonblank | None


def admit_collection_case(data: dict[str, Any], references: PurgedReferences) -> CollectionCase:
    case = CollectionCase.model_validate(data)
    identities = {paper.paper_key: paper for paper in references.split.papers}
    if case.paper_key not in identities or identities[case.paper_key].split != 'heldout':
        raise ValueError('candidate requires exact heldout paper identity in frozen split')
    return case


class NativeMembershipReadout(_FrozenContract):
    label: MembershipLabel
    probabilities: tuple[Probability, Probability, Probability]
    observed_model: Nonblank


class MembershipResult(_FrozenContract):
    collection_key: Nonblank
    prompt_sha256: Sha256
    status: Literal['decision', 'threshold_abstention', 'invalid_response', 'model_mismatch',
                    'timeout', 'unavailable', 'input_too_large']
    logical_calls: StrictInt = Field(ge=0, le=1)
    raw_response: str | None = None
    readout: NativeMembershipReadout | None = None

    @model_validator(mode='after')
    def consistent_shape(self) -> MembershipResult:
        if self.logical_calls != int(self.status != 'input_too_large'):
            raise ValueError('status disagrees with logical call count')
        has_readout = self.status in ('decision', 'threshold_abstention', 'model_mismatch')
        if (self.readout is not None) != has_readout:
            raise ValueError('status disagrees with native readout availability')
        if (self.raw_response is not None) != (has_readout or self.status == 'invalid_response'):
            raise ValueError('status disagrees with raw response availability')
        return self


def _readout_status(arm: SupportArm, readout: NativeMembershipReadout) -> str:
    if readout.observed_model != arm.model:
        return 'model_mismatch'
    confidence = readout.probabilities[MEMBERSHIP_OPTIONS.index(readout.label)]
    if arm.abstention_threshold is not None and confidence < arm.abstention_threshold:
        return 'threshold_abstention'
    return 'decision'


def _summary(questions: tuple[MembershipResult, ...]) -> tuple[str, tuple[str, ...]]:
    selected = tuple(q.collection_key for q in questions
                     if q.status == 'decision' and q.readout.label == 'belongs')
    if any(q.status not in ('decision', 'threshold_abstention') for q in questions):
        return 'operational_failure', selected
    if any(q.status == 'threshold_abstention' for q in questions):
        return 'threshold_abstention', selected
    if any(q.readout.label == 'insufficient_evidence' for q in questions):
        return 'insufficient_evidence', selected
    return ('memberships' if selected else 'no_match'), selected


class CollectionResult(_FrozenContract):
    case: CollectionCase
    plan: CollectionPlan
    arm: SupportArm
    references_sha256: Sha256
    readout_schema_sha256: Sha256
    max_logical_calls: StrictInt = Field(ge=0)
    status: Literal['memberships', 'no_match', 'insufficient_evidence', 'threshold_abstention',
                    'operational_failure', 'missing_input', 'budget_exhausted']
    selected_keys: tuple[Nonblank, ...]
    logical_calls: StrictInt = Field(ge=0)
    questions: tuple[MembershipResult, ...]

    @model_validator(mode='after')
    def consistent_projection(self) -> CollectionResult:
        missing = all(value is None for value in (self.case.title, self.case.abstract, self.case.evidence))
        blocked = 'missing_input' if missing else (
            'budget_exhausted' if len(self.plan.question_keys) > self.max_logical_calls else None)
        if blocked:
            if (self.status != blocked or self.questions or self.selected_keys or self.logical_calls):
                raise ValueError('preflight refusal cannot contain calls or decisions')
            return self
        if tuple(q.collection_key for q in self.questions) != self.plan.question_keys:
            raise ValueError('results must cover the exact frozen question keys in declared order')
        calls = sum(q.logical_calls for q in self.questions)
        if calls != self.logical_calls or calls > self.max_logical_calls:
            raise ValueError('logical calls disagree with frozen workbudget')
        if (self.status, self.selected_keys) != _summary(self.questions):
            raise ValueError('paper projection disagrees with independent membership outcomes')
        for question in self.questions:
            if question.readout is not None:
                if abs(fsum(question.readout.probabilities) - 1) > self.arm.probability_sum_tolerance:
                    raise ValueError('native vector must sum to one within frozen tolerance')
                if question.status != _readout_status(self.arm, question.readout):
                    raise ValueError('native outcome disagrees with frozen arm policy')
        return self


def _membership_prompt(case: CollectionCase, node: CollectionNode) -> str:
    candidate = {'title': case.title, 'abstract': case.abstract, 'evidence': case.evidence}
    collection = {'key': node.key, 'path': node.path, 'description': node.description}
    return (
        f'{UNTRUSTED_INPUT_RULE}\n'
        'Decide independent membership in this existing collection using ONLY supplied evidence.\n'
        'Child membership never implies parent membership.\n'
        f'Candidate:\n{untrusted_input(json.dumps(candidate, ensure_ascii=False))}\n'
        f'Collection:\n{untrusted_input(json.dumps(collection, ensure_ascii=False))}\n'
        'Options in fixed order: belongs / does_not_belong / insufficient_evidence.\n'
        'Preserve the native selected label and full probability vector; do not invent a rationale.'
    )


def _evaluate_membership(
    case: CollectionCase, node: CollectionNode, arm: SupportArm,
    provider: Callable[[str, tuple[MembershipLabel, ...]], ProviderReply],
) -> MembershipResult:
    prompt = _membership_prompt(case, node)
    common = {'collection_key': node.key, 'prompt_sha256': sha256_text(prompt)}
    if len(prompt) > arm.max_prompt_chars:
        return MembershipResult(**common, status='input_too_large', logical_calls=0)
    try:
        reply = provider(prompt, MEMBERSHIP_OPTIONS)
    except TimeoutError:
        return MembershipResult(**common, status='timeout', logical_calls=1)
    except ConnectionError:
        return MembershipResult(**common, status='unavailable', logical_calls=1)
    if not isinstance(reply, ProviderReply) or not isinstance(reply.raw_response, str):
        raise TypeError('adapter must return ProviderReply with raw text')
    try:
        readout = NativeMembershipReadout.model_validate(reply.readout)
    except ValidationError:
        readout = None
    if readout is None or abs(fsum(readout.probabilities) - 1) > arm.probability_sum_tolerance:
        return MembershipResult(**common, status='invalid_response', logical_calls=1,
                                raw_response=reply.raw_response)
    return MembershipResult(**common, status=_readout_status(arm, readout), logical_calls=1,
                            raw_response=reply.raw_response, readout=readout)


def evaluate_collections(
    data: dict[str, Any], plan: CollectionPlan, references: PurgedReferences, arm: SupportArm,
    provider: Callable[[str, tuple[MembershipLabel, ...]], ProviderReply], *, max_logical_calls: int,
) -> CollectionResult:
    """Pure projection around one injected logical call per question; never an action.

    Preflight reserves the entire declared question count, even if some prompts
    later exceed the cap. Callers own cumulative budgets across papers/arms and
    must supply the identical plan to every arm. No retries or transitive/ranking step.
    """
    if type(max_logical_calls) is not int or max_logical_calls < 0:
        raise ValueError('max_logical_calls must be an explicit nonnegative integer')
    case = admit_collection_case(data, references)
    common = {'case': case, 'plan': plan, 'arm': arm, 'max_logical_calls': max_logical_calls,
              'references_sha256': sha256_text(references.model_dump_json()),
              'readout_schema_sha256': sha256_text(json.dumps(
                  NativeMembershipReadout.model_json_schema(), sort_keys=True, ensure_ascii=True))}
    if all(value is None for value in (case.title, case.abstract, case.evidence)):
        return CollectionResult(**common, status='missing_input', selected_keys=(), logical_calls=0, questions=())
    if len(plan.question_keys) > max_logical_calls:
        return CollectionResult(**common, status='budget_exhausted', selected_keys=(), logical_calls=0, questions=())
    nodes = {node.key: node for node in plan.nodes}
    questions = tuple(_evaluate_membership(case, nodes[key], arm, provider) for key in plan.question_keys)
    status, selected = _summary(questions)
    return CollectionResult(**common, status=status, selected_keys=selected,
                            logical_calls=sum(q.logical_calls for q in questions), questions=questions)
