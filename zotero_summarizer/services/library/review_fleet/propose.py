"""Pure, deterministic mapping: cached deep-review signals -> a ``ProposedVerdict``.

This is the fleet's brain, and it makes **no LLM call** — the expensive judgement
(read/skim/skip, the A-D grade, the abstract-vs-body overstatement check, the
3-band quality verdict) ALREADY happened inside ``deep_review`` and is cached. This
module is the cheap, glassbox truth-table that folds those pre-computed signals
into one reading verdict the human can Confirm/Override.

Truth table (effective digest ``read_decision`` × ``grade``):

    read  + grade A/B   -> must_read        skim -> should/could (by grade)
    read  + grade C/D/? -> should_read      skip + grade D -> dont_read
    (no digest)         -> could_read       skip otherwise -> could_read
                                            (a safe "look later", never a hide)

ASYMMETRY (the load-bearing rule): a wrong HIDE costs more than a wrong KEEP, so a
``skip`` may propose ``dont_read`` ONLY on an explicit grade D — the quality rubric
found the paper weak on its own terms. The GOAL BOARD IS NOT A GATE: measured against
the owner's 3,173 library verdicts, "any goal relevant" was true for 89% of the
papers he wanted and 89% of the ones he rejected (AUC 0.50, see
``data/perf-20261107/speed/goal-gate.md``), and hiding on ``skip + goal-miss`` was no
more precise than hiding on ``skip + grade D`` while costing 6 LLM calls a paper. The
board therefore only informs — it is shown as a badge and never changes a verdict, a
read decision or Today eligibility. CONFIDENCE is lowered and a flag added whenever
the underlying quality signal is shaky (``quality_band == "uncertain"``; overstatements are display-only), so the UI foregrounds exactly the proposals a human should check.

Deterministic + side-effect-free → fully unit-testable without any model or I/O.
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.models.triage import ProposedVerdict, QualityEval
from zotero_summarizer.services._common import now_iso_z

# Grades that read as "this is a strong paper" for the verdict lift.
_HIGH_GRADES = frozenset({"A", "B"})


def _goal_evidence(goal_summaries: Any) -> str:
    """A confirmed hit keeps; only a complete, explicit miss board licenses hiding.

    Abstention is about the generated summary: a retrieval miss can legitimately
    abstain, but a hit with a withheld summary does not establish a confirmed match.
    """
    if not isinstance(goal_summaries, list) or not goal_summaries:
        return "unknown"
    cells = [g for g in goal_summaries if isinstance(g, dict)]
    if any(g.get("retrieval_state") == "hit" and g.get("relevant") is True
           and g.get("abstained") is False for g in cells):
        return "match"
    if len(cells) == len(goal_summaries) and all(
        g.get("retrieval_state") == "miss" and g.get("relevant") is False for g in cells
    ):
        return "miss"
    return "unknown"


def _grade(digest: dict[str, Any] | None, quality: dict[str, Any] | None) -> str:
    """Independent quality owns the grade, including an explicit ungraded result."""
    if (quality or {}).get("basis") in {"non_paper", "not_assessed"}:
        return ""
    independent = quality is not None and "grade" in quality
    src = quality if independent else digest
    grade = str((src or {}).get("grade") or "").strip().upper()[:1]
    return grade if grade in {"A", "B", "C", "D"} else ""


def _base_verdict(read_decision: str, grade: str) -> str:
    """Map (read_decision, grade) -> a reading priority BEFORE quality adjustment.

    ``read``  -> must (strong grade) / should
    ``skim``  -> should (strong grade) / could
    ``skip``  -> dont ONLY on an explicit grade D; could otherwise
    no digest -> could (a safe "look later")
    """
    high = grade in _HIGH_GRADES
    if read_decision == "read":
        return "must_read" if high else "should_read"
    if read_decision == "skim":
        return "should_read" if high else "could_read"
    if read_decision == "skip":
        # Asymmetric: a HIDE still needs the rubric's own verdict (grade D). The goal
        # board adds no precision (measured), so it never licenses a hide on its own.
        return "dont_read" if grade == "D" else "could_read"
    # No digest read_decision (e.g. the paper had no PDF, or the digest layer was
    # skipped): never propose a hide off no evidence — a neutral "look later".
    return "could_read"


def _quality_flags(quality: dict[str, Any] | None) -> tuple[list[str], bool]:
    """``(flags, shaky)`` from the cached quality_eval signals.

    ``shaky`` (band ``uncertain``) drives the confidence cut. Overstatements are
    display-only (low reliability, #189) and never flag or cut anything.
    The flags are short, human-readable reasons for the UI's "double-check" hint."""
    q = quality or {}
    flags: list[str] = []
    band = str(q.get("quality_band") or "")
    red_flags = [r for r in (q.get("red_flags") or []) if str(r).strip()]
    if band == "uncertain":
        flags.append("quality_uncertain")
    if band == "flag":
        flags.append("quality_flag")
    if red_flags:
        flags.append("red_flags")
    shaky = band == "uncertain"
    return flags, shaky


def effective_read_decision(
    digest: dict[str, Any] | None,
    quality: dict[str, Any] | None,
) -> tuple[str, list[str]]:
    """Reserve full reading for the intersection of idea, evidence, and effort.

    The standing-goal board deliberately plays no part: it is displayed, not gating
    (see the module docstring for the measurement behind that).
    """
    d, q = digest or {}, quality or {}
    decision = str(d.get("read_decision") or "").strip().lower()
    flags: list[str] = []
    band = str(q.get("quality_band") or "")
    if q.get("basis") != "non_paper" and (
        band == "flag" or bool(q.get("missing_critical"))
    ):
        flags.append("weak_evidence")
    if decision != "read":
        return decision if decision in {"skim", "skip"} else "", flags
    if d.get("basis") != "full_text":
        flags.append("full_text_not_confirmed")
    if not str(d.get("original_value") or "").strip():
        flags.append("original_value_not_assessed")
    friction = str(d.get("writing_friction") or "")
    if friction == "high":
        flags.append("writing_friction")
    elif friction not in {"low", "moderate"}:
        flags.append("writing_not_assessed")
    idea_score = max(int(d.get("novelty") or 0), int(d.get("significance") or 0))
    if idea_score < 4:
        flags.append("idea_value_not_high")
    if q.get("basis") != "non_paper" and (q.get("basis") != "full_text" or band != "highlight"):
        flags.append("strong_evidence_not_confirmed")
    if not any(str(part).strip() for part in (d.get("read_parts") or [])):
        return "", [*flags, "reading_targets_not_assessed"]
    return ("skim", flags) if flags else ("read", [])


def apply_reading_policy(
    digest: dict[str, Any],
    quality: dict[str, Any] | None,
) -> tuple[dict[str, Any], str, list[str]]:
    """Project reading and scientific grade; the caller retains the raw model digest."""
    grade = _grade(digest, quality)
    raw = str(digest.get("read_decision") or "")
    effective, flags = effective_read_decision(digest, quality)
    warnings = []
    if not effective and flags:
        warnings.append("Reading decision withheld; assessment or exact targets unavailable.")
    elif effective != raw:
        warnings.append("Full reading threshold not met; skim targeted parts.")
    if "weak_evidence" in flags:
        warnings.append("Concept/hypothesis source only; claims not established.")
    if "writing_friction" in flags:
        warnings.append("Idea preserved; writing friction is high.")
    reason = str(digest.get("read_why") or "")
    warnings = [warning for warning in warnings if warning not in reason]
    if effective != raw or warnings or grade != str(digest.get("grade") or ""):
        digest = {**digest, "grade": grade, "read_decision": effective,
                  "read_why": " ".join([*warnings, reason]).strip()}
    return digest, raw, flags


def project_review(entry: dict[str, Any]) -> dict[str, Any]:
    """Derive presentation from retained model output without modifying either source.
    ``previous_review`` is a stored backup, never part of the projected review."""
    entry = {key: value for key, value in entry.items() if key != "previous_review"}
    source = entry.get("model_digest") or entry.get("digest")
    if not source:
        return entry
    quality = entry.get("quality")
    if entry.get("model_digest"):
        if quality is None:
            quality = QualityEval(quality_band="uncertain", basis="not_assessed").model_dump()
        elif "grade" not in quality:
            quality = {**quality, "grade": ""}
    digest, raw, flags = apply_reading_policy(source, quality)
    retained_flags = [] if entry.get("model_digest") else entry.get("reading_policy_flags", [])
    return {**entry, "digest": digest, "quality": quality, "model_read_decision": entry.get("model_read_decision", raw),
            "reading_policy_flags": list(dict.fromkeys([*retained_flags, *flags]))}


def _confidence(read_decision: str, grade: str, *, shaky: bool) -> float:
    """A bounded [0,1] confidence for the proposal.

    Higher when the signals AGREE and are strong (a graded read/skip), lower when
    there is no digest, no grade, or the quality signal is shaky. Deterministic —
    a fixed additive model, not a learned score (this is a glassbox suggestion)."""
    if not read_decision:
        score = 0.35  # no digest decision: a weak "look later"
    elif read_decision == "read":
        score = 0.85 if grade in _HIGH_GRADES else 0.65
    elif read_decision == "skim":
        score = 0.7 if grade else 0.55
    else:  # skip
        # A graded-D skip is the most confident call; any other skip is kept as
        # could_read and stays below the card's 0.6 one-tap-Confirm floor so a human
        # sees it as a proposal rather than a decision.
        score = 0.75 if grade == "D" else 0.5
    if not grade and read_decision:
        score -= 0.1
    if shaky:
        score -= 0.2
    return round(max(0.0, min(1.0, score)), 2)


def _rationale(read_decision: str, grade: str, verdict: str, *, goal_evidence: str,
               shaky: bool) -> str:
    """One short plain-language sentence explaining the proposal (for the UI)."""
    decision_txt = {
        "read": "read the original",
        "skim": "skim targeted parts",
        "skip": "the digest is enough",
        "": "reading decision unavailable",
    }[read_decision]
    grade_txt = f"grade {grade}" if grade else "ungraded"
    base = f"{decision_txt}, {grade_txt} → {verdict.replace('_', ' ')}"
    # The goal board is a badge, not a cause: it is named after the verdict it did
    # not influence, so a reader can tell information from reasoning.
    goal_txt = {"match": "matched a goal", "miss": "no goal match",
                "unknown": "goals not assessed"}[goal_evidence]
    base += f" (goal board: {goal_txt})"
    if shaky:
        base += " (quality signal uncertain — worth a check)"
    return base


def propose_verdict(
    digest: dict[str, Any] | None,
    quality: dict[str, Any] | None,
    *,
    goal_summaries: Any = None,
) -> ProposedVerdict:
    """Fold the cached deep-review signals for ONE paper into a ``ProposedVerdict``.

    Pure + deterministic — NO LLM call, NO I/O. ``digest`` is the cached
    ``PaperDigest`` dump (its ``read_decision``/``grade``), ``quality`` the cached
    ``QualityEval`` dump (``quality_band``/``overstatements``/``red_flags``), and
    ``goal_summaries`` the cached per-goal board (a non-abstained, relevant hit
    confirms a match). Any of these may be ``None`` (a layer was skipped) — the mapping
    degrades to a safe ``could_read`` rather than guessing a hide.
    """
    read_decision = str((digest or {}).get("read_decision") or "").strip().lower()
    if read_decision not in {"read", "skim", "skip"}:
        read_decision = ""
    grade = _grade(digest, quality)
    goal_evidence = _goal_evidence(goal_summaries)

    flags, shaky = _quality_flags(quality)
    effective_decision, policy_flags = effective_read_decision(digest, quality)
    flags.extend(flag for flag in policy_flags if flag not in flags)
    verdict = _base_verdict(effective_decision, grade)
    confidence = _confidence(effective_decision, grade, shaky=shaky)
    rationale = _rationale(effective_decision, grade, verdict, goal_evidence=goal_evidence, shaky=shaky)

    return ProposedVerdict(
        proposed=verdict,
        confidence=confidence,
        rationale=rationale,
        flags=flags,
        digest_read_decision=read_decision,
        grade=grade,
        proposed_at=now_iso_z(),
        source="review_fleet",
    )


__all__ = ["apply_reading_policy", "effective_read_decision", "project_review", "propose_verdict"]
