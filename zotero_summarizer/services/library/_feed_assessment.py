"""Same-response project proposals; literal source membership is not semantic proof."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from zotero_summarizer.models.research_feed import FeedAssessment, FeedDigestResponse, FeedReviewContext
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


def finish(digest, original, prompt, context, out):
    """Only the final accepted digest attempt contributes optional content."""
    out.clear()
    content = getattr(digest, "feed_assessment", None)
    if content is None:
        out.update(status="invalid" if "feed_assessment" in digest.model_fields_set else "not_assessed")
        return
    evidence, keys = {}, set()
    for index, record in enumerate(content.projects):
        if record.source_id != context.source_id or record.target_project not in context.profile.projects or record.target_project in keys:
            out.update(status="invalid")
            return
        keys.add(record.target_project)
        evidence[f"/projects/{index}"] = record.evidence_quote
    for field in ("reproduction", "impact"):
        record = getattr(content, field)
        if record is not None:
            evidence[f"/{field}"] = record.evidence_quote
    spans = {}
    for path, quote in evidence.items():
        start = original.find(quote) if quote.strip() else -1
        if quote and start < 0:
            out.update(status="invalid")
            return
        spans[path] = {"quote": quote, "start": start, "end": start + len(quote)} if quote else None
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
    if not entry or "feed_assessment" not in entry:
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
    complete = keys == set(context.profile.projects) and all(r.assessment != "unknown" for r in content.projects)
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

    entry = _review_cache.get_cached_review(key)
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


def review_evidence(review):
    from zotero_summarizer.services.library._review_cache import REVIEW_CONTRACT_VERSION

    if not review:
        raise ValueError("current review missing")
    identity = review.get("review_identity") or {}
    for field in ("source_sha256", "generation_sha256"):
        value = identity.get(field)
        if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("review source identity unqualified")
    if (review.get("needs_pdf") or identity.get("source_kind") not in {"library", "override"}
            or not identity.get("source_path") or review.get("review_contract_version") != REVIEW_CONTRACT_VERSION
            or not review.get("digest") or review["digest"].get("basis") not in {"full_text", "map_reduce"}):
        raise ValueError("review source/basis unqualified")
