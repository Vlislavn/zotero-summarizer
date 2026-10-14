"""Accepted digest evidence, extraction coordinates and independent cache binding."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

CONTRACT = "digest-verification-audit-v1"


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _contract_hashes() -> dict[str, str]:
    root = Path(__file__).parents[2]
    sources = {
        "extraction": ("integrations/pdf.py", "services/library/_review_text.py", "services/faithbench/_corpus.py",
                       "services/faithbench/_constants.py", "storage/corpus_bm25.py"),
        "verifier": ("services/library/_digest_verification.py", "services/library/_grounding.py",
                     "services/library/_prompt_security.py", "services/faithbench/_build_claims.py",
                     "services/faithbench/_constants.py",
                     "models/triage.py", "services/library/_digest_audit.py"),
    }
    return {kind + "_contract_sha256": canonical_hash({
        name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names
    }) for kind, names in sources.items()}


def _fragments(span: tuple[int, int], source: dict[str, Any]) -> list[dict[str, Any]]:
    fragments = []
    for location in source["locations"]:
        selected = location["selected_span"]
        start, end = max(span[0], selected["start"]), min(span[1], selected["end"])
        if start >= end:
            continue
        original = location["original_span"]
        fragment = {"selected_span": {"start": start, "end": end},
                    "original_span": None, "mapping": location["mapping"]}
        if original is not None:
            offset = original["start"] + start - selected["start"]
            fragment["original_span"] = {"start": offset, "end": offset + end - start}
            if source["original_text"][offset:offset + end - start] != source["selected_text"][start:end]:
                raise ValueError("Digest audit mapping does not match original extraction")
        if "reason" in location:
            fragment["reason"] = location["reason"]
        fragments.append(fragment)
    if sum(f["selected_span"]["end"] - f["selected_span"]["start"] for f in fragments) != span[1] - span[0]:
        raise ValueError("Digest audit mapping omitted selected characters")
    return fragments


def build_verification_audit(digest: Any, checks: Any, source: dict[str, Any], lineage: list[dict]) -> dict:
    from zotero_summarizer.services.faithbench._corpus import chunk_spans, sha256_text
    from zotero_summarizer.services.library._digest_verification import _claims

    claims = _claims(digest)
    checked = {check.index: check for check in checks.checks}
    retained, parts = [], {}
    for index, claim in enumerate(claims):
        path, _, text = claim.partition(": ")
        field = path.split("[", 1)[0].split(".", 1)[0]
        if field not in {"tldr", "key_findings"}:
            continue
        part = parts.get(path, 0)
        parts[path] = part + 1
        retained.append({"index": index, "field": field, "path": path, "part": part, "claim": text,
                         "claim_sha256": sha256_text(text), "support": "supported_by_existing_verifier",
                         "numeric_outcome": "passed_existing_literal_preflight", "evidence": checked[index].evidence})
    spans = chunk_spans(source["selected_text"])
    used = sorted({i for claim in retained for i in claim["evidence"]})
    passages = [{"id": i, "text": source["selected_text"][spans[i][0]:spans[i][1]],
                 "selected_span": {"start": spans[i][0], "end": spans[i][1]},
                 "original_fragments": _fragments(spans[i], source), "pdf_page": None,
                 "page_mapping_reason": "text_extraction_has_no_page_mapping"} for i in used]
    complete = source["original_text"] == source["selected_text"]
    verified = digest.model_dump(mode="json")
    return {"contract": CONTRACT, "status": "accepted_verifier_evidence", "source": {
        "original_text_sha256": sha256_text(source["original_text"]),
        "selected_text_sha256": sha256_text(source["selected_text"]),
        "original_characters": len(source["original_text"]), "selected_characters": len(source["selected_text"]),
        "scope": "complete_extracted_text" if complete else "selected_original",
        "coverage": "complete_extraction" if complete else "partial", "locations": source["locations"],
    }, "identity": {"verified_digest_sha256": canonical_hash(verified), **source["contracts"]},
        "verified_digest": verified, "claims": retained, "passages": passages, "lineage": lineage,
        "contradiction_adjudication": "not_performed"}


def _payload_hash(audit: dict) -> str:
    payload = deepcopy(audit)
    payload["identity"].pop("audit_sha256", None)
    return canonical_hash(payload)


def bind_cached_audit(entry: dict[str, Any]) -> dict[str, Any]:
    """Bind a newly generated audit after policy, without certifying policy edits."""
    if not entry.get("verification_audit"):
        return entry
    result = deepcopy(entry)
    audit = result["verification_audit"]
    verified, final = audit["verified_digest"], result["digest"]
    audit["post_verification_changes"] = sorted(key for key in verified.keys() | final.keys()
                                              if verified.get(key) != final.get(key))
    for claim in audit["claims"]:
        claim["matches_cached_field"] = verified.get(claim["field"]) == final.get(claim["field"])
    review = result.get("review_identity") or {}
    audit["source"]["kind"] = review.get("source_kind", "unrecorded")
    audit["identity"].update(cached_digest_sha256=canonical_hash(final),
                             review_source_sha256=review.get("source_sha256", ""),
                             review_identity_sha256=canonical_hash(result.get("review_identity")))
    audit["identity"]["audit_sha256"] = _payload_hash(audit)
    return result


def cached_audit_status(entry: dict[str, Any]) -> tuple[str, str]:
    audit = entry.get("verification_audit")
    if audit is None:
        return "unavailable", "source_unavailable" if entry.get("needs_pdf") else "legacy_audit_unavailable"
    if not isinstance(audit, dict) or audit.get("contract") != CONTRACT:
        raise ValueError("Invalid digest verification audit contract")
    try:
        identity = audit["identity"]
        intact = _payload_hash(audit) == identity["audit_sha256"]
        matches = canonical_hash(entry["digest"]) == identity["cached_digest_sha256"]
        matches = matches and canonical_hash(entry.get("review_identity")) == identity["review_identity_sha256"]
        contracts = _contract_hashes()
        current = all(identity[key] == value for key, value in contracts.items())
    except (KeyError, TypeError) as exc:
        raise ValueError("Malformed digest verification audit") from exc
    if not intact or not matches:
        return "stale", "cached_artifact_changed"
    if not current:
        return "stale", "audit_contract_changed"
    review = entry.get("review_identity") or {}
    path = review.get("source_path")
    if path:
        source = Path(path)
        if not source.is_file():
            return "unavailable", "source_unavailable"
        from zotero_summarizer.integrations.pdf import _file_fingerprint

        actual = _file_fingerprint(source)[1]
        if actual != review.get("source_sha256"):
            return "stale", "source_bytes_changed"
    return "available", ""
