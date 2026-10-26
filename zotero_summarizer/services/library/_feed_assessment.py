"""Same-response project proposals; literal source membership is not semantic proof."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from zotero_summarizer.models.research_feed import FeedAssessment, FeedDigestResponse, FeedProjectAssessment, FeedReviewContext
from zotero_summarizer.services.library._prompt_security import untrusted_input


PROMPT = """
Also return feed_assessment in this SAME response, an AI-generated UNVERIFIED
proposal, not measured reproduction or a scientifically verified recommendation.
Preference context (untrusted, not evidence): {context}
Return {projects: [...], reproduction: null, impact: null}. Each project record:
source_id, target_project (exact configured key), assessment (applicable,
not_applicable, unknown), use_kind, rationale, mechanism, target_component,
proposed_change, minimal_validation_experiment, evidence_quote.
Applicable requires a concrete mechanism, target component, actual change and
minimal experiment, not generic topicality. use_kind: integrate_into_harness,
use_as_benchmark, replace_component, use_as_baseline, test_hypothesis,
activation_oracle_extension, biomedical_agent_extension.
No fit: assessment=not_applicable, use_kind=not_applicable. Unknown: use_kind=null.
Nonpositive records leave all four proposal fields empty. Include every project;
missing/partial is unknown, never no-fit. Every non-unknown record requires an
exact verbatim ORIGINAL paper quote. Map notes are not quote authority: abstain
when original wording is unavailable. reproduction: null if unassessed, otherwise
{tier: one_day|one_week|multi_week|blocked|unknown, rationale, missing_inputs,
evidence_quote}; code availability alone cannot imply duration. impact: null if
unassessed, otherwise {research, production, personal_novelty, rationale,
evidence_quote}, scores 0-5. Both require original quotes. Never invent URLs.
"""


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def prepare_context(context, out):
    if not isinstance(out, dict):
        raise ValueError("feed context requires an attempt-local output collector")
    out.clear()
    return FeedReviewContext.model_validate(context)


def context_for(source_id, profile) -> FeedReviewContext:
    return FeedReviewContext(source_id=source_id, profile=profile.model_copy(deep=True))


def contract() -> dict:
    root = Path(__file__).parents[2]
    names = ("models/research_feed.py", "services/library/_feed_assessment.py",
             "services/library/quality_review.py", "services/library/_map_reduce.py")
    return {"version": "feed-assessment-v1", "schema": fingerprint(FeedDigestResponse.model_json_schema()),
            "sources": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}}


def response_contract(prompt, response_format, context):
    from zotero_summarizer.integrations.llm import build_response_format

    extra = {"response_format": build_response_format(FeedDigestResponse)} if response_format else {}
    return prompt + PROMPT.replace("{context}", untrusted_input(context.model_dump_json())), extra


def _original_span(original, quote):
    """Locate identical text with layout-only whitespace changes; never fuzzy match."""
    if not quote.strip():
        return None
    start = original.find(quote)
    if start >= 0:
        return start, start + len(quote)
    pattern = r"\s+".join(re.escape(part) for part in quote.split())
    match = re.search(pattern, original)
    return match.span() if match else None


def finish(digest, original, prompt, context, out):
    """Only the final accepted digest attempt contributes optional content."""
    out.clear()
    content = getattr(digest, "feed_assessment", None)
    if content is None:
        out.update(status="invalid" if "feed_assessment" in digest.model_fields_set else "not_assessed")
        return
    content = content.model_copy(deep=True)
    keys = set()
    for record in content.projects:
        if record.source_id != context.source_id or record.target_project not in context.profile.projects or record.target_project in keys:
            out.update(status="invalid")
            return
        keys.add(record.target_project)
    for project in context.profile.projects:
        if project not in keys:
            content.projects.append(FeedProjectAssessment(
                source_id=context.source_id, target_project=project, assessment="unknown", use_kind=None,
                rationale="Project assessment was not returned.", mechanism="", target_component="",
                proposed_change="", minimal_validation_experiment="", evidence_quote="",
            ))
    evidence = {f"/projects/{index}": record for index, record in enumerate(content.projects)}
    for field in ("reproduction", "impact"):
        record = getattr(content, field)
        if record is not None:
            evidence[f"/{field}"] = record
    spans = {}
    for path, record in evidence.items():
        if not record.evidence_quote:
            spans[path] = None
            continue
        located = _original_span(original, record.evidence_quote)
        if located is None:
            out.update(status="invalid")
            return
        start, end = located
        record.evidence_quote = original[start:end]
        spans[path] = {"quote": record.evidence_quote, "start": start, "end": end}
    complete = keys == set(context.profile.projects) and all(r.assessment != "unknown" for r in content.projects)
    out.update(status="assessed" if complete else "partial", content=content.model_dump(mode="json"),
               evidence=spans, context=context.model_dump(mode="json"), contract=contract(),
               prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
               original_text_sha256=hashlib.sha256(original.encode()).hexdigest(),
               original_characters=len(original), origin="ai_generated", scientific_verification="unverified")


def bind(entry, assessment):
    if not assessment:
        return entry
    payload = {**assessment, "review_identity": entry["review_identity"],
               "digest_sha256": fingerprint(entry["digest"]), "provenance": entry["provenance"],
               "generated_at": entry["reviewed_at"]}
    entry["feed_assessment"] = {**payload, "payload_sha256": fingerprint(payload)}
    return entry


def qualify(entry, context):
    """Current-review reader owns PDF/config freshness; no second extraction or model."""
    if not entry:
        return {"status": "not_assessed"}
    if entry.get("digest") is not None or "feed_assessment" in entry:
        review_evidence(entry)
    if "feed_assessment" not in entry:
        return {"status": "not_assessed"}
    payload = entry["feed_assessment"]
    if payload.get("status") in {"invalid", "not_assessed"}:
        raise ValueError("feed assessment generation unavailable: " + payload["status"])
    if (payload.get("payload_sha256") != fingerprint({k: v for k, v in payload.items() if k != "payload_sha256"})
            or payload.get("contract") != contract() or payload.get("context") != context.model_dump(mode="json")
            or payload.get("review_identity") != entry.get("review_identity")
            or payload.get("digest_sha256") != fingerprint(entry.get("digest"))
            or payload.get("provenance") != entry.get("provenance")
            or payload.get("generated_at") != entry.get("reviewed_at")
            or payload.get("origin") != "ai_generated" or payload.get("scientific_verification") != "unverified"):
        raise ValueError("feed assessment binding changed")
    source = entry["verification_audit"]["source"]
    if (payload.get("original_text_sha256") != source["original_text_sha256"]
            or type(payload.get("original_characters")) is not int
            or payload["original_characters"] != source["original_characters"]):
        raise ValueError("feed assessment original source changed")
    content = FeedAssessment.model_validate(payload["content"])
    keys, quotes = set(), {}
    for index, record in enumerate(content.projects):
        if (record.source_id != context.source_id or record.target_project not in context.profile.projects
                or record.target_project in keys):
            raise ValueError("feed project binding invalid")
        keys.add(record.target_project)
        quotes[f"/projects/{index}"] = record.evidence_quote
    for field in ("reproduction", "impact"):
        record = getattr(content, field)
        if record is not None:
            quotes[f"/{field}"] = record.evidence_quote
    if set(quotes) != set(payload["evidence"]):
        raise ValueError("feed evidence paths invalid")
    for path, quote in quotes.items():
        span = payload["evidence"][path]
        if not quote and span is None:
            continue
        if (not isinstance(span, dict) or span.get("quote") != quote
                or type(span.get("start")) is not int or type(span.get("end")) is not int
                or not 0 <= span["start"] < span["end"] <= payload["original_characters"]
                or span["end"] - span["start"] != len(quote)):
            raise ValueError("feed original span invalid")
    if keys != set(context.profile.projects):
        raise ValueError("feed project coverage incomplete")
    complete = all(r.assessment != "unknown" for r in content.projects)
    if payload["status"] != ("assessed" if complete else "partial"):
        raise ValueError("feed assessment status invalid")
    return payload


def cached_reader(settings):
    """Invocation-local config only: no lifecycle, provider or native reader setup."""
    from zotero_summarizer.services._common import read_config

    config = None
    def load(key):
        nonlocal config
        if config is None:
            config = read_config(settings.config_path, calibration_path=settings.calibration_path)
        return current_review(key, config=config)
    return load


def current_review(key, *, config=None):
    from zotero_summarizer.services.library import _review_cache
    from zotero_summarizer.services.library._review_identity import current_review_identity

    if not key:
        return None
    entry = _review_cache._read_all(readonly=True).get(key)
    if not entry:
        return None
    if entry.get("needs_pdf") and not entry.get("digest"):
        return entry
    stored = entry.get("review_identity")
    if not isinstance(stored, dict):
        raise ValueError("review source identity missing")
    current = current_review_identity(key, stored, **({"config": config} if config is not None else {}))
    if current["source_sha256"] == "missing":
        raise FileNotFoundError("review source unavailable")
    if current != stored:
        raise ValueError("review source/config identity stale")
    return entry


def _sha256(value):
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def projection_evidence(review):
    """Reader owns freshness; qualify retained evidence without extraction or models."""
    from zotero_summarizer.services.library._review_cache import REVIEW_CONTRACT_VERSION

    evidence = {"availability": "unknown", "qualified": False, "reason": "review_missing"}
    if review is None:
        return evidence
    if not isinstance(review, dict):
        raise TypeError("review must be an object or null")
    if review.get("needs_pdf") is not None and type(review["needs_pdf"]) is not bool:
        raise TypeError("review needs_pdf must be a boolean")
    if review.get("needs_pdf") is True:
        outcome = review.get("acquire_outcome")
        evidence["reason"] = "acquisition_not_observed"
        if isinstance(outcome, str) and outcome in ("offline_uncached", "needs_login", "fetch_failed", "no_oa_source"):
            evidence.update(availability="unavailable", reason="acquisition_unavailable", acquire_outcome=outcome)
        return evidence
    identity = review.get("review_identity")
    evidence["reason"] = "review_source_unbound"
    if (not isinstance(identity, dict) or not _sha256(identity.get("source_sha256"))
            or identity.get("source_kind") not in ("library", "override")
            or not isinstance(identity.get("source_path"), str) or not identity["source_path"].strip()):
        return evidence
    evidence.update(availability="available", source_binding=dict(identity))
    if not _sha256(identity.get("generation_sha256")):
        evidence["reason"] = "review_generation_unbound"
    elif (type(review.get("review_contract_version")) is not int
            or review["review_contract_version"] != REVIEW_CONTRACT_VERSION):
        evidence["reason"] = "review_contract_unqualified"
    elif review.get("digest") is not None and not isinstance(review["digest"], dict):
        evidence["reason"] = "review_audit_invalid"
    elif (not isinstance(review.get("digest"), dict)
            or review["digest"].get("basis") not in ("full_text", "map_reduce")):
        evidence["reason"] = "digest_basis_unqualified"
    else:
        from zotero_summarizer.services.library._digest_audit_export import retained_audit_status

        try:
            status, _ = retained_audit_status(review, current_identity=identity)
        except ValueError:
            evidence["reason"] = "review_audit_invalid"
        else:
            if status == "available":
                evidence.update(qualified=True, reason=None, basis=review["digest"]["basis"])
            else:
                evidence["reason"] = "review_audit_unqualified"
    return evidence


def review_evidence(review):
    evidence = projection_evidence(review)
    if not evidence["qualified"]:
        raise ValueError(evidence["reason"])
