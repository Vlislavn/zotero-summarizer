"""Experimental, explicit sufficient-answer promotion; unknown is never a penalty."""
from __future__ import annotations

import json
import math
import os

from zotero_summarizer.services.search._models import Candidate, ResearchSession
from zotero_summarizer.services.search._source_context import raw_digest


def source_identity(candidate: Candidate) -> str:
    """Invalidate acquired evidence when the candidate's source addresses change."""
    return raw_digest(json.dumps([candidate.candidate_id, candidate.identifier_keys(), candidate.url], ensure_ascii=False))


def evidence_limits() -> dict[str, int | float]:
    """Provisional work bounds, not calibrated quality/latency thresholds."""
    limits = {}
    for name, default, maximum in (("candidates", 12, 30), ("calls", 24, 120)):
        value = int(os.environ.get(f"ZS_SEARCH_EVIDENCE_{name.upper()}", default))
        if not 0 <= value <= maximum:
            raise ValueError(f"Search evidence {name} must be between 0 and {maximum}")
        limits[name] = value
    floor = float(os.environ.get("ZS_SEARCH_EVIDENCE_TOPIC_FLOOR", "0.35"))
    if not math.isfinite(floor) or not 0 <= floor <= 1:
        raise ValueError("Search evidence topic floor must be between 0 and 1")
    return {**limits, "topic_floor": floor}


def _sufficient(candidate: Candidate, sess: ResearchSession) -> int:
    covered = set()
    for row in candidate.question_evidence:
        index = row.get("question_index")
        if type(index) is not int or not 0 <= index < len(sess.questions):
            continue
        if (row.get("candidate_id") != candidate.candidate_id or row.get("state") != "sufficient"
                or row.get("supported") is not True or row.get("reason") != "verified"
                or row.get("question_id") != raw_digest(sess.questions[index])
                or row.get("canonical_question_id") != raw_digest(sess.intent.canonical_question)
                or not row.get("verification_attempted") or not row.get("evidence")):
            continue
        if row.get("source_kind") == "acquired_original" and row.get("source_id") != source_identity(candidate):
            continue
        if any(e.get("source_id") != row.get("source_id") or e.get("source_version_id") != row.get("source_version_id")
               for e in row["evidence"]):
            continue
        covered.add(index)
    return len(covered)


def apply_question_policy(sess: ResearchSession) -> None:
    """Stable promotion over the existing ranking, never a new quality score."""
    if sess.evidence_policy != "question_evidence" or not sess.questions:
        return
    if sess.evidence_status.get("state") != "complete":
        return
    floor = sess.evidence_status["topic_floor"]
    counts = {c.candidate_id: _sufficient(c, sess) for c in sess.candidates
              if not c.is_retracted and c.query_score is not None and math.isfinite(c.query_score)
              and c.query_score >= floor}
    sess.candidates.sort(key=lambda c: counts.get(c.candidate_id, 0), reverse=True)
    sess.evidence_status["promoted_candidate_ids"] = [c.candidate_id for c in sess.candidates if counts.get(c.candidate_id)]
