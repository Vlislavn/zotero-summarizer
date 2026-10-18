"""Experimental sufficient-answer promotion; unknown never changes baseline order."""
from __future__ import annotations

import hashlib
import json
import math
import os
from typing import Any


def digest(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_identity(candidate: Any) -> str:
    return digest([candidate.candidate_id, candidate.identifier_keys(), candidate.url])


def input_identity(session: Any) -> str:
    return digest([session.raw_query, session.questions, session.intent.to_dict()])


def evidence_limits() -> dict:
    limits = {}
    for name, default, maximum in (("candidates", 12, 30), ("calls", 24, 120)):
        try:
            value = int(os.environ.get(f"ZS_SEARCH_EVIDENCE_{name.upper()}", default))
        except ValueError as exc:
            raise ValueError(f"invalid_{name}_budget") from exc
        if not 0 <= value <= maximum:
            raise ValueError(f"invalid_{name}_budget")
        limits[name] = value
    try:
        floor = float(os.environ.get("ZS_SEARCH_EVIDENCE_TOPIC_FLOOR", "0.35"))
    except ValueError as exc:
        raise ValueError("invalid_topic_floor") from exc
    if not math.isfinite(floor) or not 0 <= floor <= 1:
        raise ValueError("invalid_topic_floor")
    return {**limits, "topic_floor": floor}


def current_binding() -> dict[str, str] | None:
    """Use loaded configuration metadata only; never resolve clients or credentials."""
    from zotero_summarizer.services._common import state
    from zotero_summarizer.services.search._question_evidence import _evaluators

    config = getattr(getattr(state(), "app_state", None), "config", None)
    if (config is None or not getattr(config, "llm_enabled", False)
            or getattr(config, "llm_routing", None) is None):
        return None
    try:
        return _evaluators(config)
    except (KeyError, ValueError):  # Invalid/unavailable route metadata cannot certify historical evidence.
        return None


def current_receipt(row: dict, candidate: Any, session: Any, binding: dict | None) -> bool:
    """Single current-evidence authority for projection, invalidation and promotion."""
    index = row.get("question_index")
    return (binding is not None and candidate.candidate_id in session.evidence_baseline_order
            and all(row.get(key) == value for key, value in binding.items())
            and type(index) is int and 0 <= index < len(session.questions)
            and row.get("candidate_id") == candidate.candidate_id
            and row.get("question") == session.questions[index]
            and row.get("question_id") == digest(session.questions[index])
            and row.get("input_id") == input_identity(session)
            and row.get("source_id") == source_identity(candidate)
            and (row.get("state") not in {"sufficient", "partial"} or verified_evidence(row)))


def verified_evidence(row: dict) -> bool:
    sha = row.get("source_sha256", "")
    scope = row.get("checked_scope") or {}
    if (row.get("source_kind") != "acquired_original" or row.get("source_version_id") != sha
            or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha)
            or row.get("reason") != "verified" or not row.get("verification_attempted")
            or not row.get("evidence")):
        return False
    for span in row["evidence"]:
        start, end = span.get("start"), span.get("end")
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= scope.get("end", 0)
                or not isinstance(span.get("quote"), str) or len(span["quote"]) != end - start
                or span.get("passage_id") != f"chars:{start}:{end}"
                or span.get("source_id") != row.get("source_id") or span.get("source_version_id") != sha
                or not any(p["start"] <= start < end <= p["end"] for p in row.get("checked_passages", []))):
            return False
    return True


def evidence_view(candidate: Any, session: Any, binding: dict | None) -> list[dict]:
    """Suppress stale answers only in display; stored receipts stay exact."""
    return [row if current_receipt(row, candidate, session, binding) else {
        "candidate_id": candidate.candidate_id, "question_index": row.get("question_index"),
        "question": row.get("question"), "state": "unknown", "reason": "stale_or_invalid_evidence",
    } for row in candidate.question_evidence]


def rank_baseline(session: Any) -> None:
    """Restore the observed pre-promotion tie input, then reuse current constrained keys."""
    from zotero_summarizer.services.search.rank import rank_candidates

    order = {key: index for index, key in enumerate(session.evidence_baseline_order)}
    if order:
        session.candidates.sort(key=lambda candidate: order.get(candidate.candidate_id, len(order)))
    rank_candidates(session.candidates)


def invalidate_stale_evidence(session: Any) -> dict | None:
    """Return one metadata snapshot for validation/display; reading never writes a file."""
    if not any(candidate.question_evidence for candidate in session.candidates):
        return None
    binding = current_binding()
    if any(not current_receipt(row, candidate, session, binding)
           for candidate in session.candidates for row in candidate.question_evidence):
        rank_baseline(session)
        session.evidence_status.update(state="unavailable", promoted_candidate_ids=[])
        session.evidence_status.setdefault("reason", "stale_or_invalid_evidence")
    return binding


def apply_question_policy(session: Any) -> None:
    if (session.evidence_policy != "question_evidence" or not session.questions
            or session.evidence_status.get("state") != "complete"):
        return
    binding = invalidate_stale_evidence(session)
    if session.evidence_status.get("state") != "complete":
        return
    rank_baseline(session)
    session.evidence_baseline_order = [candidate.candidate_id for candidate in session.candidates]
    floor = session.evidence_status["topic_floor"]
    counts = {}
    for candidate in session.candidates:
        if (candidate.is_retracted or candidate.query_score is None
                or not math.isfinite(candidate.query_score) or candidate.query_score < floor):
            continue
        covered = set()
        for row in candidate.question_evidence:
            if row.get("state") == "sufficient" and current_receipt(row, candidate, session, binding):
                covered.add(row["question_index"])
        counts[candidate.candidate_id] = len(covered)
    session.candidates.sort(key=lambda c: counts.get(c.candidate_id, 0), reverse=True)
    session.evidence_status["promoted_candidate_ids"] = [c.candidate_id for c in session.candidates
                                                       if counts.get(c.candidate_id)]
