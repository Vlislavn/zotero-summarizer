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


def _contract_hashes(*, interpretation_schema: str | None = None) -> dict[str, str]:
    if interpretation_schema is not None:
        from zotero_summarizer.services.library._review_identity import _validate_interpretation_schema

        _validate_interpretation_schema(interpretation_schema)
    root = Path(__file__).parents[2]
    sources = {
        "extraction": ("integrations/pdf.py", "services/library/_review_text.py", "services/faithbench/_corpus.py",
                       "services/faithbench/_constants.py", "storage/corpus_bm25.py"),
        "verifier": ("services/library/_digest_verification.py", "services/library/_grounding.py",
                     "services/library/_prompt_security.py", "services/faithbench/_build_claims.py",
                     "services/faithbench/_constants.py",
                     "models/triage.py", "services/library/_digest_audit.py"),
    }
    if interpretation_schema is not None:
        sources["interpretation"] = ("models/interpretation.py", "services/library/quality_review.py",
                                     "services/library/_review_identity.py", "services/library/_review_cache.py",
                                     "services/library/_digest_audit.py")
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


def _retained_claims(claims: list[str], checked: dict, indices: set[int]) -> list[dict]:
    from zotero_summarizer.services.faithbench._corpus import sha256_text

    retained, parts = [], {}
    for index, claim in enumerate(claims):
        if index not in indices:
            continue
        path, _, text = claim.partition(": ")
        field = path.split("[", 1)[0].split(".", 1)[0]
        part = parts.get(path, 0)
        parts[path] = part + 1
        retained.append({"index": index, "field": field, "path": path, "part": part, "claim": text,
                         "claim_sha256": sha256_text(text), "support": "supported_by_existing_verifier",
                         "numeric_outcome": "passed_existing_literal_preflight", "evidence": checked[index].evidence})
    return retained


def _passages(source: dict[str, Any], indices: set[int], *, include_hash: bool = False) -> list[dict]:
    from zotero_summarizer.services.faithbench._corpus import chunk_spans, sha256_text

    spans = chunk_spans(source["selected_text"])
    passages = [{"id": i, "text": source["selected_text"][spans[i][0]:spans[i][1]],
                 "selected_span": {"start": spans[i][0], "end": spans[i][1]},
                 "original_fragments": _fragments(spans[i], source), "pdf_page": None,
                 "page_mapping_reason": "text_extraction_has_no_page_mapping"} for i in sorted(indices)]
    if include_hash:
        for passage in passages:
            passage["text_sha256"] = sha256_text(passage["text"])
    return passages


def build_verification_audit(
    digest: Any, checks: Any, source: dict[str, Any], lineage: list[dict], *,
    interpretation_payload: dict[str, Any] | None = None,
) -> dict:
    from zotero_summarizer.services.faithbench._corpus import sha256_text
    from zotero_summarizer.services.library._digest_verification import _claims

    if interpretation_payload is not None:
        from zotero_summarizer.services.library.quality_review import _checked_attempt
        from zotero_summarizer.services.library._digest_verification import DigestVerifierUnavailable

        checks = _checked_attempt(checks, digest, (), source["selected_text"])
        if any(not check.supported for check in checks.checks):
            raise DigestVerifierUnavailable("C audit requires complete supported base checks")
    claims = _claims(digest)
    checked = {check.index: check for check in checks.checks}
    indices = {i for i, claim in enumerate(claims) if claim.partition(":")[0].split("[", 1)[0] in {"tldr", "key_findings"}}
    retained = _retained_claims(claims, checked, indices)
    used = {i for claim in retained for i in claim["evidence"]}
    complete = source["original_text"] == source["selected_text"]
    verified = digest.model_dump(mode="json")
    audit = {"contract": CONTRACT, "status": "accepted_verifier_evidence", "source": {
        "original_text_sha256": sha256_text(source["original_text"]),
        "selected_text_sha256": sha256_text(source["selected_text"]),
        "original_characters": len(source["original_text"]), "selected_characters": len(source["selected_text"]),
        "scope": "complete_extracted_text" if complete else "selected_original",
        "coverage": "complete_extraction" if complete else "partial", "locations": source["locations"],
    }, "identity": {"verified_digest_sha256": canonical_hash(verified), **source["contracts"]},
        "verified_digest": verified, "claims": retained, "passages": _passages(source, used), "lineage": lineage,
        "contradiction_adjudication": "not_performed"}
    if interpretation_payload is not None:
        _capture_interpretation(audit, interpretation_payload, digest, checks, source)
    return audit


def _validated_interpretation(payload: dict, verified: dict) -> list[dict]:
    from zotero_summarizer.models import PaperDigest
    from zotero_summarizer.models.interpretation import InterpretationDraft, interpretation_payload

    if not isinstance(payload, dict) or set(payload) != {"origin", "scientific_verification", "status", "items", "native_support"}:
        raise ValueError("interpretation_not_verified")
    draft = InterpretationDraft.model_validate({"items": payload["items"]})
    expected = interpretation_payload(draft, PaperDigest.model_validate(verified))
    if (canonical_hash(expected) != canonical_hash({key: value for key, value in payload.items() if key != "native_support"})
        or expected["status"] != "schema_validated_ai_draft"):
        raise ValueError("interpretation_not_verified")
    return expected["items"]


def _native_support(payload: dict, digest: Any) -> tuple[list[str], Any]:
    from zotero_summarizer.services.library.quality_review import _interpretation_claims
    from zotero_summarizer.services.library._digest_verification import _Checks, _claims

    _validated_interpretation(payload, digest.model_dump(mode="json"))
    optional = list(_interpretation_claims(payload))
    support = payload["native_support"]
    count = len(_claims(digest))
    indices = list(range(count, count + len(optional)))
    if not optional or not isinstance(support, dict) or set(support) != {
        "base_count", "base_digest_sha256", "optional_claims", "global_indices", "validated_checks",
    }:
        raise ValueError("interpretation_not_verified")
    checks = _Checks.model_validate({"checks": support["validated_checks"]})
    expected = {"base_count": count, "base_digest_sha256": canonical_hash(digest.model_dump(mode="json")),
                "optional_claims": optional, "global_indices": indices,
                "validated_checks": checks.model_dump(mode="json")["checks"]}
    if (canonical_hash(support) != canonical_hash(expected)
        or canonical_hash([check.index for check in checks.checks]) != canonical_hash(indices)
        or any(not check.supported or not check.evidence or len(set(check.evidence)) != len(check.evidence) for check in checks.checks)):
        raise ValueError("interpretation_not_verified")
    return optional, checks


def _reference_indices(ref: str, claims: list[str]) -> list[int]:
    path = f"key_findings[{ref.rsplit('/', 1)[1]}]" if ref.startswith("/key_findings/") else ref[1:].replace("/", ".")
    return [i for i, claim in enumerate(claims) if (claim.partition(": ")[0] == path or
            claim.partition(": ")[0].startswith((path + "[", path + ".")))]


def _reference_bindings(payload: dict, verified: dict) -> list[dict]:
    from zotero_summarizer.models.interpretation import interpretation_references

    items = _validated_interpretation(payload, verified)
    references = interpretation_references(verified)
    return [{"item": index, "finding_ref": item["finding_ref"],
             "finding_sha256": canonical_hash(references[item["finding_ref"]]),
             "basis_sha256": {ref: canonical_hash(references[ref]) for ref in item["basis_refs"]}}
            for index, item in enumerate(items)]


def _capture_interpretation(audit: dict, payload: dict, digest: Any, base_checks: Any, source: dict) -> None:
    from zotero_summarizer.services.library.quality_review import _checked_attempt
    from zotero_summarizer.services.library._digest_verification import _Checks, _claims, _unsupported_literals

    audit["interpretation_evidence"] = {"status": "unavailable", "reason": "interpretation_not_verified"}
    if "interpretation_contract_sha256" not in source["contracts"]:
        return
    try:
        optional, optional_checks = _native_support(payload, digest)
        base = _claims(digest)
        combined = base + optional
        checked = _checked_attempt(_Checks(checks=base_checks.checks + optional_checks.checks), digest,
                                   tuple(optional), source["selected_text"])
        if any(not check.supported for check in checked.checks) or _unsupported_literals(combined, source["selected_text"]):
            raise ValueError("interpretation_not_verified")
        bindings = _reference_bindings(payload, audit["verified_digest"])
        refs = dict.fromkeys(ref for item in payload["items"] for ref in [item["finding_ref"], *item["basis_refs"]])
        associations = {ref: _reference_indices(ref, base) for ref in refs}
        if any(not indices for indices in associations.values()):
            raise ValueError("interpretation_not_verified")
        by_index = {check.index: check for check in checked.checks}
        scientific = _retained_claims(combined, by_index, set(range(len(base), len(combined))))
        basis_indices = {i for indices in associations.values() for i in indices} - {c["index"] for c in audit["claims"]}
        basis = _retained_claims(base, by_index, basis_indices)
        used = {i for claim in scientific + basis for i in claim["evidence"]} - {p["id"] for p in audit["passages"]}
        evidence = {"status": "accepted_native_support", "claims": scientific, "basis_claims": basis,
                    "reference_claims": associations, "passages": _passages(source, used, include_hash=True)}
    except (KeyError, TypeError, ValueError):
        return
    audit["verified_interpretation"] = deepcopy(payload)
    audit["identity"]["verified_interpretation_sha256"] = canonical_hash(payload)
    audit["interpretation_bindings"] = bindings
    audit["interpretation_evidence"] = evidence


def _payload_hash(audit: dict) -> str:
    payload = deepcopy(audit)
    payload["identity"].pop("audit_sha256", None)
    return canonical_hash(payload)


def _audit_condition(entry: dict, interpretation_schema: str | None) -> bool:
    from zotero_summarizer.services.library._review_identity import _condition_matches

    stored = entry.get("review_identity") or {}
    if not isinstance(stored, dict) or not _condition_matches(stored, interpretation_schema):
        return False
    audit = entry.get("verification_audit")
    return interpretation_schema is not None or not ("interpretation" in entry or
        isinstance(audit, dict) and "interpretation_evidence" in audit)


def _bind_interpretation(audit: dict, entry: dict) -> None:
    from zotero_summarizer.models.interpretation import interpretation_references
    from zotero_summarizer.services.library._review_identity import assert_review_source_unchanged

    assert_review_source_unchanged(entry["review_identity"])
    audit["identity"]["interpretation_sha256"] = canonical_hash(entry.get("interpretation"))
    if "verified_interpretation" not in audit:
        return
    known = _reference_bindings(audit["verified_interpretation"], audit["verified_digest"])
    bindings = audit["interpretation_bindings"]
    if len(bindings) != len(known) or any(canonical_hash({key: stored[key] for key in expected}) != canonical_hash(expected)
                                         for stored, expected in zip(bindings, known)):
        raise ValueError("Invalid verification-time interpretation bindings")
    final = interpretation_references(entry["digest"])
    audit["interpretation_bindings"] = [{**item,
        "matches_cached_finding": item["finding_sha256"] == canonical_hash(final.get(item["finding_ref"])),
        "matches_cached_basis": {ref: digest_hash == canonical_hash(final.get(ref))
                                 for ref, digest_hash in item["basis_sha256"].items()}} for item in known]


def bind_cached_audit(entry: dict[str, Any], *, interpretation_schema: str | None = None) -> dict[str, Any]:
    """Bind after policy; only explicit CODE selection may bind optional content."""
    if not _audit_condition(entry, interpretation_schema):
        raise ValueError("review_condition_mismatch")
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
    if interpretation_schema is not None:
        _bind_interpretation(audit, result)
    audit["identity"]["audit_sha256"] = _payload_hash(audit)
    return result


def _record_checks(digest: Any, records: list[dict], indices: set[int]) -> Any:
    from zotero_summarizer.services.library._digest_verification import _Checks, _claims

    checks = _Checks.model_validate({"checks": [{"index": row["index"], "supported": True, "evidence": row["evidence"]} for row in records]})
    if len(checks.checks) != len(indices) or {check.index for check in checks.checks} != indices:
        raise ValueError("Invalid retained native indices")
    expected = _retained_claims(_claims(digest), {check.index: check for check in checks.checks}, indices)
    for row, known in zip(sorted(records, key=lambda r: r["index"]), expected):
        if (type(row["index"]) is not int or canonical_hash({key: row[key] for key in known}) != canonical_hash(known)
            or not row["evidence"]):
            raise ValueError("Invalid retained native claims")
    return checks


def _passage_ids(passages: list[dict]) -> set[int]:
    from zotero_summarizer.services.faithbench._corpus import sha256_text

    ids = set()
    for passage in passages:
        index, text, span = passage["id"], passage["text"], passage["selected_span"]
        if type(index) is not int or index < 0 or index in ids or not isinstance(text, str) or not text.strip():
            raise ValueError("Invalid retained passage")
        if (type(span["start"]) is not int or type(span["end"]) is not int
            or span["end"] - span["start"] != len(text) or span["start"] < 0):
            raise ValueError("Invalid retained passage span")
        if "text_sha256" in passage and passage["text_sha256"] != sha256_text(text):
            raise ValueError("Invalid retained passage hash")
        fragments = passage["original_fragments"]
        if sum(f["selected_span"]["end"] - f["selected_span"]["start"] for f in fragments) != len(text):
            raise ValueError("Invalid retained passage fragments")
        if passage["pdf_page"] is not None or passage["page_mapping_reason"] != "text_extraction_has_no_page_mapping":
            raise ValueError("Invalid retained page provenance")
        ids.add(index)
    return ids


def _validate_base_audit(entry: dict, audit: dict) -> None:
    from zotero_summarizer.models import PaperDigest
    from zotero_summarizer.services.library._digest_verification import _claims

    verified = PaperDigest.model_validate(audit["verified_digest"])
    final = PaperDigest.model_validate(entry["digest"])
    if (canonical_hash(verified.model_dump(mode="json")) != canonical_hash(audit["verified_digest"])
        or canonical_hash(final.model_dump(mode="json")) != canonical_hash(entry["digest"])
        or canonical_hash(audit["verified_digest"]) != audit["identity"]["verified_digest_sha256"]
        or audit["status"] != "accepted_verifier_evidence" or audit["contradiction_adjudication"] != "not_performed"):
        raise ValueError("Invalid verified base audit")
    claims = _claims(verified)
    indices = {i for i, claim in enumerate(claims) if claim.partition(":")[0].split("[", 1)[0] in {"tldr", "key_findings"}}
    checks = _record_checks(verified, audit["claims"], indices)
    passage_ids = _passage_ids(audit["passages"])
    if any(not set(check.evidence) <= passage_ids for check in checks.checks):
        raise ValueError("Invalid base passage references")


def cached_audit_status(entry: dict[str, Any], *, interpretation_schema: str | None = None) -> tuple[str, str]:
    if not _audit_condition(entry, interpretation_schema):
        return "unavailable", "review_condition_mismatch"
    audit = entry.get("verification_audit")
    if audit is None:
        return "unavailable", "source_unavailable" if entry.get("needs_pdf") else "legacy_audit_unavailable"
    if not isinstance(audit, dict) or audit.get("contract") != CONTRACT:
        raise ValueError("Invalid digest verification audit contract")
    try:
        identity = audit["identity"]
        if interpretation_schema is not None:
            _validate_base_audit(entry, audit)
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


def _current_reference_matches(payload: dict, audit: dict, raw: dict, current: dict) -> list[dict]:
    from zotero_summarizer.models.interpretation import interpretation_references

    known = _reference_bindings(payload, audit["verified_digest"])
    bindings = audit["interpretation_bindings"]
    if len(bindings) != len(known) or any(canonical_hash({key: stored[key] for key in expected}) != canonical_hash(expected)
                                         for stored, expected in zip(bindings, known)):
        raise ValueError("interpretation_binding_changed")
    raw_refs, current_refs = interpretation_references(raw), interpretation_references(current)
    return [{"item": item["item"], "finding_ref": item["finding_ref"],
             "finding_matches": item["finding_sha256"] == canonical_hash(raw_refs.get(item["finding_ref"]))
                                == canonical_hash(current_refs.get(item["finding_ref"])),
             "basis_matches": {ref: digest_hash == canonical_hash(raw_refs.get(ref)) == canonical_hash(current_refs.get(ref))
                               for ref, digest_hash in item["basis_sha256"].items()}}
            for item in known]


def _validate_optional_evidence(payload: dict, audit: dict) -> None:
    from zotero_summarizer.models import PaperDigest
    from zotero_summarizer.services.library._digest_verification import _claims

    digest = PaperDigest.model_validate(audit["verified_digest"])
    optional, checks = _native_support(payload, digest)
    base = _claims(digest)
    evidence = audit["interpretation_evidence"]
    expected = _retained_claims(base + optional, {check.index: check for check in checks.checks}, set(range(len(base), len(base) + len(optional))))
    if canonical_hash(evidence["claims"]) != canonical_hash(expected):
        raise ValueError("interpretation_evidence_changed")
    refs = dict.fromkeys(ref for item in payload["items"] for ref in [item["finding_ref"], *item["basis_refs"]])
    associations = {ref: _reference_indices(ref, base) for ref in refs}
    if canonical_hash(evidence["reference_claims"]) != canonical_hash(associations) or any(not indices for indices in associations.values()):
        raise ValueError("interpretation_evidence_changed")
    basis_indices = {i for indices in associations.values() for i in indices} - {c["index"] for c in audit["claims"]}
    basis_checks = _record_checks(digest, evidence["basis_claims"], basis_indices)
    ids = _passage_ids(audit["passages"] + evidence["passages"])
    if any(not set(check.evidence) <= ids for check in checks.checks + basis_checks.checks):
        raise ValueError("interpretation_evidence_changed")


def qualify_interpretation(
    entry: dict, current_digest: dict, *, interpretation_schema: str | None = None,
    audit_status: str, audit_reason: str,
) -> dict:
    result = {"status": "unavailable", "reason": "interpretation_not_verified",
              "audit_status": audit_status, "audit_reason": audit_reason, "item_matches": []}
    if not _audit_condition(entry, interpretation_schema) or interpretation_schema is None:
        return {**result, "reason": "review_condition_mismatch"}
    if audit_status != "available":
        return result
    audit = entry["verification_audit"]
    evidence = audit.get("interpretation_evidence")
    if not isinstance(evidence, dict) or evidence.get("status") != "accepted_native_support":
        return result
    try:
        payload = entry["interpretation"]
        identity = audit["identity"]
        if (canonical_hash(payload) != identity["interpretation_sha256"]
            or canonical_hash(payload) != canonical_hash(audit["verified_interpretation"])
            or canonical_hash(payload) != identity["verified_interpretation_sha256"]):
            return {**result, "reason": "interpretation_payload_changed"}
        contracts = _contract_hashes(interpretation_schema=interpretation_schema)
        if identity.get("interpretation_contract_sha256") != contracts["interpretation_contract_sha256"]:
            return {**result, "reason": "interpretation_contract_changed"}
        _validate_optional_evidence(payload, audit)
        matches = _current_reference_matches(payload, audit, entry["digest"], current_digest)
    except (KeyError, TypeError, ValueError):
        return result
    result["item_matches"] = matches
    if not all(item["finding_matches"] and all(item["basis_matches"].values()) for item in matches):
        return {**result, "reason": "interpretation_reference_changed"}
    return {**result, "status": "available", "reason": ""}
