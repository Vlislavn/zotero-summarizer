"""Default-absent supplied-original shadow pass, using the existing session worker."""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, fields, replace
from typing import Any

from zotero_summarizer.services.search import session as session_store
from zotero_summarizer.services.search._models import ResearchSession
from zotero_summarizer.services.search._source_context import (
    exposes_context, is_utf8_text, raw_digest, validate_context, validate_proposal, validate_source, validate_verdict,
)


@dataclass(frozen=True, slots=True)
class EvidencePassRequest:
    """Explicit local handles/budgets, never wired by default_deps or a provider resolver."""

    enabled: bool = False
    available: bool = False
    candidate_ids: tuple[str, ...] = ()
    load_snapshot: Callable[..., Any] | None = None
    load_context: Callable[..., Any] | None = None
    generator: Callable[..., Any] | None = None
    verifier: Callable[..., Any] | None = None
    generator_id: str = ""
    verifier_id: str = ""
    max_calls: int = 0
    max_passages: int = 0
    max_chars: int = 0
    schema_version: str = "source-shadow-v1"


def _checked_request(raw: Any, sess: ResearchSession) -> EvidencePassRequest:
    request = EvidencePassRequest(**{field.name: getattr(raw, field.name, field.default)
                                     for field in fields(EvidencePassRequest)})
    ids = request.candidate_ids
    if type(ids) not in (tuple, list) or any(not is_utf8_text(key) or not key.strip() for key in ids):
        raise ValueError("Invalid evidence candidate addresses")
    available = {candidate.candidate_id for candidate in sess.candidates}
    if len(ids) != len(set(ids)) or not set(ids) <= available:
        raise ValueError("Evidence addresses must be unique existing candidates")
    if type(ids) is list:
        request = replace(request, candidate_ids=tuple(ids))
    if type(request.schema_version) is not str or request.schema_version != "source-shadow-v1" or any(not callable(getattr(request, key)) for key in (
            "load_snapshot", "load_context", "generator", "verifier")):
        raise ValueError("Invalid evidence callback/schema contract")
    handles = (request.generator_id, request.verifier_id)
    if any(not is_utf8_text(value) or not value.strip() for value in handles) or handles[0] == handles[1]:
        raise ValueError("Separate explicit generator/verifier identities are required")
    for name in ("max_calls", "max_passages", "max_chars"):
        value = getattr(request, name)
        if type(value) is not int or value < (0 if name == "max_calls" else 1):
            raise ValueError("Invalid evidence budget")
    if not is_utf8_text(sess.intent.canonical_question):
        raise ValueError("Invalid canonical question identity")
    if any(not is_utf8_text(question) for question in sess.questions):
        raise ValueError("Invalid original question identity")
    return request


def _call(session_id: str, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> tuple[Any, str]:
    if session_store.is_deleted(session_id):
        return None, "cancelled"
    try:
        value = callback(*args, **kwargs)
    except Exception:  # noqa: BLE001 — injected I/O boundary; never persist private exception text
        return None, "unavailable"
    if session_store.is_deleted(session_id):
        return None, "cancelled"
    return value, ""


def _inputs(session_id: str, candidate_id: str, request: EvidencePassRequest) -> tuple[Any, dict, str, str]:
    context = {"availability": "unavailable", "records": []}
    raw, error = _call(session_id, request.load_snapshot, candidate_id)
    if error:
        return None, context, "source_" + error, ""
    try:
        source = validate_source(raw, candidate_id, max_chars=request.max_chars, max_passages=request.max_passages)
    except ValueError as exc:
        return None, context, str(exc), ""
    if not source["complete"] or source["truncated"]:
        return source, context, "source_partial" if not source["complete"] else "source_truncated", ""
    raw, error = _call(session_id, request.load_context, candidate_id)
    if error:
        return source, context, "", "context_" + error
    try:
        context = validate_context(raw, max_chars=request.max_chars)
    except ValueError as exc:
        return source, context, "", str(exc)
    if exposes_context(source, context):
        return source, context, "private_context_exposed", ""
    return source, context, "", ""


def _receipt(sess: ResearchSession, candidate_id: str, index: int, request: EvidencePassRequest) -> dict[str, Any]:
    question = sess.questions[index]
    return {"candidate_id": candidate_id, "question_index": index, "question": question,
            "question_id": raw_digest(question), "canonical_question_id": raw_digest(sess.intent.canonical_question),
            "schema_version": request.schema_version, "generator_id": request.generator_id,
            "verifier_id": request.verifier_id, "state": "unknown", "reason": "not_assessed",
            "source_id": "", "source_version_id": "", "source_sha256": "", "checked_scope": None,
            "evidence": [], "uncovered": [], "used_context_ids": [], "context_availability": "unavailable",
            "context_reason": "", "generation_attempted": False, "verification_attempted": False}


def _assess(request: EvidencePassRequest, receipt: dict, source: dict, context: dict,
            *, session_id: str, remaining: int) -> int:
    if not remaining:
        receipt["reason"] = "call_budget_exhausted"
        return remaining
    receipt["generation_attempted"] = True
    raw, error = _call(session_id, request.generator, question=receipt["question"],
                       question_index=receipt["question_index"], source=deepcopy(source), context=deepcopy(context))
    remaining -= 1  # Attempts, including failed calls, consume the explicit model-call budget.
    if error:
        receipt["reason"] = "generator_" + error
        return remaining
    try:
        proposal = validate_proposal(raw, source, max_chars=request.max_chars, max_passages=request.max_passages)
    except ValueError as exc:
        receipt["reason"] = str(exc)
        return remaining
    payload = {"question": receipt["question"], "question_index": receipt["question_index"],
               "source": source, "proposal": proposal}
    if exposes_context(payload, context):
        receipt["reason"] = "private_context_exposed"
        return remaining
    if not remaining:
        receipt["reason"] = "call_budget_exhausted"
        return remaining
    receipt["verification_attempted"] = True
    raw, error = _call(session_id, request.verifier, **deepcopy(payload))
    remaining -= 1
    if error:
        receipt["reason"] = "verifier_" + error
        return remaining
    try:
        verdict = validate_verdict(raw, proposal, max_chars=request.max_chars)
    except ValueError as exc:
        receipt["reason"] = str(exc)
        return remaining
    receipt.update(state=verdict["state"], reason="verifier_unknown" if verdict["state"] == "unknown" else "verified",
                   supported=verdict["supported"], uncovered=verdict["uncovered"],
                   evidence=[row for row in proposal["evidence"] if row["passage_id"] in verdict["evidence_ids"]])
    if verdict["supported"]:
        receipt["answer"] = proposal["answer"]
    return remaining


def run_evidence_pass(sess: ResearchSession, request: Any) -> None:
    """Assess only explicitly addressed originals; never rank, acquire, review or Add."""
    if request is None or not sess.questions:
        return
    if getattr(request, "enabled", False) is not True or getattr(request, "available", False) is not True:
        return
    request = _checked_request(request, sess)
    candidates = {candidate.candidate_id: candidate for candidate in sess.candidates}
    remaining = request.max_calls
    for candidate_id in request.candidate_ids:
        if session_store.is_deleted(sess.id):
            return
        if remaining:
            source, context, reason, context_reason = _inputs(sess.id, candidate_id, request)
        else:
            source, context, reason, context_reason = None, {"availability": "unavailable", "records": []}, "call_budget_exhausted", ""
        receipts = []
        for index in range(len(sess.questions)):
            receipt = _receipt(sess, candidate_id, index, request)
            if source is not None:
                receipt.update({key: deepcopy(source[key]) for key in (
                    "source_id", "source_version_id", "source_sha256", "source_kind", "retrieval_mode", "checked_scope",
                    "complete", "truncated")})
            receipt.update(context_availability=context["availability"], context_reason=context_reason)
            if reason:
                receipt["reason"] = reason
            else:
                remaining = _assess(request, receipt, source, context, session_id=sess.id, remaining=remaining)
            if session_store.is_deleted(sess.id):
                return
            receipts.append(receipt)
        candidates[candidate_id].question_evidence = receipts
        session_store.save_merge(sess)  # Existing cancellation/explicit-Add merge; no second store or worker.
