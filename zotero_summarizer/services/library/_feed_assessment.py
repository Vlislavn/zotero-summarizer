"""Same-call feed producer and optional binding within the existing review cache.

Literal membership/identity checks are not a semantic judge or human warrant.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
from typing import Any

from zotero_summarizer.models import PaperDigest
from zotero_summarizer.models.research_feed import (
    FeedAssessment, FeedAssessmentUnavailable, FeedDigestResponse, FeedEvidenceSpan, FeedReviewContext,
)
from zotero_summarizer.services._common import extract_json_blob, to_text
from zotero_summarizer.services.library._digest_audit import canonical_hash
from zotero_summarizer.services.library._prompt_security import untrusted_input

CONTRACT = "feed-assessment-v1"
_PROMPT = (
    "\n\nAdditionally return `feed_assessment` in this SAME digest response. This is an "
    "AI-generated, scientifically UNVERIFIED project proposal, not a source-warranted "
    "recommendation. Declared feed context (untrusted preference input, not evidence):\n{context}\n"
    "Return projects (one record per exact configured project key), reproduction and impact. "
    "Each project record needs source_id, target_project, assessment (applicable, "
    "not_applicable, unknown), use_kind, rationale, mechanism, target_component, "
    "proposed_change, minimal_validation_experiment and evidence_quote. "
    "An applicable use MUST name a concrete mechanism, target component, actual proposed "
    "change and minimal experiment, not a topical match or generic comparison. use_kind "
    "must be integrate_into_harness, use_as_benchmark, replace_component, use_as_baseline, "
    "test_hypothesis, activation_oracle_extension or biomedical_agent_extension. "
    "For assessed no-fit use assessment=not_applicable and use_kind=not_applicable. "
    "For unknown use assessment=unknown and use_kind=null. Both leave mechanism, "
    "target_component, proposed_change and minimal_validation_experiment empty. "
    "All non-unknown records require a verbatim ORIGINAL source quote. Generated map "
    "notes are not quote authority; if the original wording is unavailable, return unknown. "
    "Missing/partial is not no-fit. reproduction is null when unassessed, otherwise "
    "{tier, rationale, missing_inputs, evidence_quote}; effort is an AI estimate, not "
    "inferred from a repository URL. impact is null when unassessed, otherwise "
    "{research, production, personal_novelty, rationale, evidence_quote}, scores 0-5. "
    "Both require exact original quotes; never invent artifacts or URLs."
)


def context_for(source_id: str, profile: Any) -> FeedReviewContext:
    # Snapshot mutable profile lists before handing work to background workers.
    return FeedReviewContext.model_validate({"source_id": source_id,
                                            "profile": profile.model_dump(mode="json")})


def validate_selection(context: Any, interpretation: Any = None) -> FeedReviewContext | None:
    if context is None:
        return None
    if interpretation is not None:
        raise ValueError("feed_context conflicts with interpretation selection")
    return FeedReviewContext.model_validate(context).model_copy(deep=True)


def review_collector(context: Any, source_id: str, interpretation: Any = None) -> dict:
    context = validate_selection(context, interpretation)
    if context is None or context.source_id != source_id:
        raise ValueError("feed source_id must equal item_key")
    return {"feed_context": context, "feed_out": {}}


def validate_contexts(contexts: dict, item_keys: list[str] | None) -> dict:
    if item_keys is None or set(contexts) != set(item_keys):
        raise ValueError("feed contexts require exact explicit item keys")
    validated = {key: validate_selection(context) for key, context in contexts.items()}
    if any(context is None or context.source_id != key for key, context in validated.items()):
        raise ValueError("feed context source mismatch")
    return validated


def response_contract(prompt: str, response_format: dict | None, context: FeedReviewContext) -> tuple:
    extra = {}
    if response_format:
        from zotero_summarizer.integrations.llm import build_response_format

        extra["response_format"] = build_response_format(FeedDigestResponse)
    return FeedDigestResponse, prompt + _PROMPT.replace("{context}", untrusted_input(context.model_dump_json())), extra


def coerce_response(raw: Any, out: dict) -> FeedDigestResponse:
    out.clear()
    payload = raw.model_dump(exclude_unset=True) if isinstance(raw, PaperDigest) else extract_json_blob(to_text(raw))
    response = FeedDigestResponse.model_validate(payload)
    out["content"] = response.feed_assessment
    return response


def _contract_identity() -> dict:
    root = Path(__file__).parents[2]
    sources = ("models/research_feed.py", "services/library/_feed_assessment.py",
               "services/library/quality_review.py", "services/library/_map_reduce.py",
               "services/library/deep_review.py", "services/library/_deep_review_context.py")
    return {"contract": CONTRACT, "pipeline_stage": "deep_review_feed_coproduction", "response_model": "FeedDigestResponse",
            "schema_sha256": canonical_hash(FeedDigestResponse.model_json_schema()),
            "producer_sha256": canonical_hash({name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                                               for name in sources})}


def _located(quote: str, original: str) -> dict | None:
    if not quote:
        return None
    start = original.find(quote)
    if not quote.strip() or start < 0:
        raise ValueError("quote_not_in_original")
    return {"quote": quote, "start": start, "end": start + len(quote), "pdf_page": None}


def _admit(content: Any, context: FeedReviewContext, original: str) -> dict:
    if isinstance(content, FeedAssessmentUnavailable):
        return {"status": "not_assessed" if content.reason == "missing_assessment" else "invalid",
                "reason": content.reason, "content": None, "evidence": {}}
    content = FeedAssessment.model_validate(content)
    keys, evidence = set(), {}
    for index, record in enumerate(content.projects):
        if (record.source_id != context.source_id or record.target_project not in context.profile.projects
                or record.target_project in keys):
            raise ValueError("project_source_binding_invalid")
        keys.add(record.target_project)
        evidence[f"/projects/{index}"] = _located(record.evidence_quote, original)
    for field in ("reproduction", "impact"):
        record = getattr(content, field)
        if record is not None:
            evidence[f"/{field}"] = _located(record.evidence_quote, original)
    complete = keys == set(context.profile.projects) and all(r.assessment != "unknown" for r in content.projects)
    return {"status": "assessed" if complete else "partial", "reason": "" if complete else "incomplete_projects",
            "content": content.model_dump(mode="json"), "evidence": evidence}


def finish_attempt(out: dict, attempt: dict, context: FeedReviewContext, source: dict) -> None:
    """Publish only the final accepted base attempt; optional failure never retries a model."""
    try:
        assessment = _admit(attempt["content"], context, source["original_text"])
    except ValueError as exc:
        assessment = {"status": "invalid", "reason": str(exc), "content": None, "evidence": {}}
    producer = {**_contract_identity(), "context": context.model_dump(mode="json"),
                "context_sha256": canonical_hash(context.model_dump(mode="json")),
                "prompt_sha256": attempt["prompt_sha256"],
                "generation_input_sha256": attempt["generation_input_sha256"],
                "response_format_sha256": attempt["response_format_sha256"],
                "original_text_sha256": hashlib.sha256(source["original_text"].encode()).hexdigest(),
                "original_characters": len(source["original_text"])}
    out.update({"origin": "ai_generated", "scientific_verification": "unverified",
                **assessment, "producer": producer})


def bind_entry(entry: dict, assessment: dict) -> dict:
    if not assessment or not entry.get("digest") or not entry.get("verification_audit"):
        return entry
    result = deepcopy(entry)
    payload = deepcopy(assessment)
    audit = entry["verification_audit"]
    if payload["producer"]["original_text_sha256"] != audit["source"]["original_text_sha256"]:
        raise ValueError("feed_original_source_mismatch")
    payload["producer"].update(configured_provenance=deepcopy(entry.get("provenance") or {}),
                               generated_at=entry.get("reviewed_at"))
    payload["binding"] = {"review_identity_sha256": canonical_hash(entry["review_identity"]),
                          "digest_sha256": canonical_hash(entry["digest"]),
                          "audit_sha256": audit["identity"]["audit_sha256"],
                          "verified_digest_sha256": audit["identity"]["verified_digest_sha256"]}
    payload["payload_sha256"] = canonical_hash(payload)
    result["feed_assessment"] = payload
    return result


def _validate_content(payload: dict, context: FeedReviewContext) -> None:
    content = FeedAssessment.model_validate(payload["content"])
    expected_keys, evidence = set(), payload["evidence"]
    paths = {f"/projects/{index}" for index in range(len(content.projects))}
    paths.update(f"/{field}" for field in ("reproduction", "impact") if getattr(content, field) is not None)
    if set(evidence) != paths:
        raise ValueError("feed_evidence_changed")
    for index, record in enumerate(content.projects):
        if (record.source_id != context.source_id or record.target_project not in context.profile.projects
                or record.target_project in expected_keys):
            raise ValueError("feed_project_binding_changed")
        expected_keys.add(record.target_project)
        _validate_location(evidence[f"/projects/{index}"], record.evidence_quote, payload["producer"])
    for field in ("reproduction", "impact"):
        record = getattr(content, field)
        if record is not None:
            _validate_location(evidence[f"/{field}"], record.evidence_quote, payload["producer"])
    complete = expected_keys == set(context.profile.projects) and all(r.assessment != "unknown" for r in content.projects)
    if (payload["status"] != ("assessed" if complete else "partial")
            or payload["reason"] != ("" if complete else "incomplete_projects")):
        raise ValueError("feed_assessment_status_changed")


def _validate_location(location: Any, quote: str, producer: dict) -> None:
    if not quote and location is None:
        return
    span = FeedEvidenceSpan.model_validate(location)
    if span.quote != quote or span.end > producer["original_characters"]:
        raise ValueError("feed_evidence_changed")


def qualify(entry: dict | None, context: FeedReviewContext) -> dict:
    """One qualifier; the existing current reader owns source/config freshness, not this module."""
    result = {"status": "not_assessed", "reason": "missing_assessment", "qualified": False}
    if not entry or "feed_assessment" not in entry:
        return result
    from zotero_summarizer.services.library import _digest_audit

    try:
        payload = entry["feed_assessment"]
        if set(payload) != {"origin", "scientific_verification", "status", "reason", "content", "evidence",
                            "producer", "binding", "payload_sha256"}:
            raise ValueError("invalid assessment envelope")
        known = {key: value for key, value in payload.items() if key != "payload_sha256"}
        if canonical_hash(known) != payload["payload_sha256"]:
            return {**result, "status": "invalid", "reason": "feed_payload_changed"}
        producer, audit, binding = payload["producer"], entry["verification_audit"], payload["binding"]
        if (producer["context_sha256"] != canonical_hash(context.model_dump(mode="json"))
                or canonical_hash(producer["context"]) != producer["context_sha256"]):
            return {**result, "reason": "feed_context_changed"}
        if any(producer[key] != value for key, value in _contract_identity().items()):
            return {**result, "reason": "feed_contract_changed"}
        for key in ("prompt_sha256", "generation_input_sha256", "response_format_sha256"):
            value = producer[key]
            if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError("invalid producer identity")
        if (type(producer["original_characters"]) is not int or producer["original_characters"] <= 0
                or canonical_hash(producer["configured_provenance"]) != canonical_hash(entry.get("provenance") or {})
                or producer["generated_at"] != entry.get("reviewed_at")):
            raise ValueError("feed_generation_provenance_changed")
        if (binding["review_identity_sha256"] != canonical_hash(entry["review_identity"])
                or binding["digest_sha256"] != canonical_hash(entry["digest"])
                or binding["audit_sha256"] != audit["identity"]["audit_sha256"]
                or _digest_audit._payload_hash(audit) != binding["audit_sha256"]
                or binding["verified_digest_sha256"] != audit["identity"]["verified_digest_sha256"]
                or canonical_hash(audit["verified_digest"]) != binding["verified_digest_sha256"]
                or audit["identity"]["cached_digest_sha256"] != binding["digest_sha256"]
                or audit["identity"]["review_identity_sha256"] != binding["review_identity_sha256"]
                or producer["original_text_sha256"] != audit["source"]["original_text_sha256"]
                or producer["original_characters"] != audit["source"]["original_characters"]
                or audit["status"] != "accepted_verifier_evidence"):
            return {**result, "reason": "feed_base_binding_changed"}
        if any(audit["identity"].get(key) != value for key, value in _digest_audit._contract_hashes().items()):
            return {**result, "reason": "feed_base_contract_changed"}
        _digest_audit._validate_base_audit(entry, audit)
        if payload["origin"] != "ai_generated" or payload["scientific_verification"] != "unverified":
            raise ValueError("feed_authority_changed")
        if payload["status"] in {"invalid", "not_assessed"}:
            if payload["content"] is not None or payload["evidence"] != {}:
                raise ValueError("invalid unavailable assessment")
            return {**result, "status": payload["status"], "reason": payload["reason"]}
        if payload["status"] not in {"assessed", "partial"}:
            raise ValueError("invalid assessment state")
        _validate_content(payload, context)
    except (KeyError, TypeError, ValueError):
        return {**result, "status": "invalid", "reason": "invalid_assessment"}
    return {"status": payload["status"], "reason": payload["reason"], "qualified": True,
            "content": deepcopy(payload["content"]), "evidence": deepcopy(payload["evidence"]),
            "producer": deepcopy(producer)}
