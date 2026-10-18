"""Transient F/S/U pointers from one already-qualified C bound snapshot.

This is presentation, not another reader, qualifier or scientific approval.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


def build_interpretation_view(qualified_review: dict[str, Any]) -> dict[str, Any]:
    """Project qualified JSON losslessly, without resolving refs or copying bodies."""
    qualification = qualified_review["interpretation_qualification"]
    status, reason = qualification["status"], qualification["reason"]
    if (status not in {"available", "unavailable"} or not isinstance(reason, str)
        or qualification["audit_status"] != "available" or qualification["audit_reason"] != ""
        or (status == "available" and reason != "") or (status == "unavailable" and not reason)
        or not isinstance(qualified_review["digest"], dict)):
        raise ValueError("Expected a current qualified C review")
    view = {
        "availability": status,
        "reason": reason,
        "origin": "ai_generated",
        "scientific_verification": "unverified",
        "contradiction_adjudication": "not_performed",
        "evidence_state": "bound_snapshot",
        "groups": {"Finding": [], "Scope": [], "Unproven": []},
        "passage_support": [],
    }
    if status == "unavailable":
        if "interpretation" in qualified_review or "verification_audit" in qualified_review:
            raise ValueError("Unavailable qualified C review must omit optional content and audit")
        return view

    payload, audit = qualified_review["interpretation"], qualified_review["verification_audit"]
    items, evidence = payload["items"], audit["interpretation_evidence"]
    if (not isinstance(items, list) or not items
        or payload["status"] != "schema_validated_ai_draft"
        or payload["origin"] != "ai_generated" or payload["scientific_verification"] != "unverified"
        or audit["status"] != "accepted_verifier_evidence" or evidence["status"] != "accepted_native_support"):
        raise ValueError("Expected qualified native interpretation content")
    view["groups"] = {
        name: [deepcopy({field: item[field] for field in fields}) for item in items]
        for name, fields in (
            ("Finding", ("finding_ref", "inference_strength", "rationale")),
            ("Scope", ("finding_ref", "scope", "basis_refs")),
            ("Unproven", ("finding_ref", "uncertainties", "unsupported_extrapolations", "basis_refs")),
        )
    }
    rows = audit["claims"] + evidence["basis_claims"] + evidence["claims"]
    view["passage_support"] = [
        {"claim_index": row["index"], "scientific_ref": row["path"], "part": row["part"],
         "availability": "available", "reason": "", "passage_refs": list(row["evidence"])}
        for row in sorted(rows, key=lambda row: row["index"])
    ]
    return view


def current_review_view(item_key: str, *, interpretation_schema: str) -> dict[str, Any] | None:
    """Use the existing qualification once; attach only to an independent copy."""
    from zotero_summarizer.services.library import deep_review

    review = deep_review.get_current_review(item_key, interpretation_schema=interpretation_schema)
    if review is None:
        return None
    return {**deepcopy(review), "interpretation_view": build_interpretation_view(review)}
