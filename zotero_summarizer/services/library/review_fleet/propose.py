"""Pure, deterministic mapping: cached deep-review signals -> a ``ProposedVerdict``.

This is the fleet's brain, and it makes **no LLM call** — the expensive judgement
(read/skim/skip, the A-D grade, the abstract-vs-body overstatement check, the
3-band quality verdict) ALREADY happened inside ``deep_review`` and is cached. This
module is the cheap, glassbox truth-table that folds those pre-computed signals
into one reading verdict the human can Confirm/Override.

Truth table (digest ``read_decision`` × ``grade``, then quality-adjusted):

    read  + grade A/B   -> must_read        skim -> should/could (by grade)
    read  + grade C/D/? -> should_read      skip -> could (match/unknown) / dont (real miss)
    (no digest)         -> could_read       (a safe "look later", never a hide)

ASYMMETRY (the load-bearing rule): a wrong HIDE costs more than a wrong KEEP, so a
``skip`` may propose ``dont_read`` ONLY on a REAL goal-MISS — the goal board was
evaluated and no standing goal fired. A goal MATCH, or an UNKNOWN board (the
goal-summary layer was skipped or its LLM call errored, which ``deep_review``
swallows to ``None``), keeps ``could_read``: absence of evidence is never evidence
to hide. CONFIDENCE is lowered and a flag added whenever the underlying quality
signal is shaky (``quality_band == "uncertain"`` or any overstatement); an
unknown-goal skip is also kept below the card's one-tap-Confirm floor, so the UI
foregrounds exactly the proposals a human should double-check.

Deterministic + side-effect-free → fully unit-testable without any model or I/O.
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.models.triage import ProposedVerdict
from zotero_summarizer.services._common import now_iso_z

# Grades that read as "this is a strong paper" for the verdict lift.
_HIGH_GRADES = frozenset({"A", "B"})


def _goal_evidence(goal_summaries: Any) -> str:
    """Tri-state goal signal: ``"match"`` (≥1 standing goal fired), ``"miss"``
    (goals WERE evaluated and none fired), or ``"unknown"`` (no usable goal board).

    The distinction is load-bearing for the no-wrong-hide asymmetry: only a REAL
    miss may license a ``dont_read`` proposal on a skip. An ``unknown`` board —
    ``None``/empty/malformed, e.g. the goal-summary LLM call errored and
    ``deep_review`` swallowed it to ``None`` — is NOT a miss: a swallowed infra
    error must never nudge a paper toward a hide. A board "matched" when any of its
    ``GoalSummary`` cells is ``relevant``; cells must be dicts to count as evaluated.
    """
    if not isinstance(goal_summaries, list):
        return "unknown"
    cells = [g for g in goal_summaries if isinstance(g, dict)]
    if not cells:
        return "unknown"
    return "match" if any(bool(g.get("relevant")) for g in cells) else "miss"


def _grade(digest: dict[str, Any] | None, quality: dict[str, Any] | None) -> str:
    """The A-D grade, preferring the digest's then the quality_eval's (they echo
    each other); ``""`` when neither assessed it."""
    for src in (digest, quality):
        grade = str((src or {}).get("grade") or "").strip().upper()[:1]
        if grade in {"A", "B", "C", "D"}:
            return grade
    return ""


def _base_verdict(read_decision: str, grade: str, *, goal_evidence: str) -> str:
    """Map (read_decision, grade) -> a reading priority BEFORE quality adjustment.

    ``read``  -> must (strong grade) / should
    ``skim``  -> should (strong grade) / could
    ``skip``  -> dont ONLY on a REAL goal-miss; could on a match OR unknown board
    no digest -> could (a safe "look later")
    """
    high = grade in _HIGH_GRADES
    if read_decision == "read":
        return "must_read" if high else "should_read"
    if read_decision == "skim":
        return "should_read" if high else "could_read"
    if read_decision == "skip":
        # Asymmetric: a HIDE requires a REAL goal-miss (board evaluated, none
        # fired). A match OR an unknown board keeps could_read — absence of
        # evidence (e.g. a swallowed goal-summary error) is never evidence to hide.
        return "dont_read" if goal_evidence == "miss" else "could_read"
    # No digest read_decision (e.g. the paper had no PDF, or the digest layer was
    # skipped): never propose a hide off no evidence — a neutral "look later".
    return "could_read"


def _quality_flags(quality: dict[str, Any] | None) -> tuple[list[str], bool]:
    """``(flags, shaky)`` from the cached quality_eval signals.

    ``shaky`` (band ``uncertain`` OR any overstatement) drives the confidence cut.
    The flags are short, human-readable reasons for the UI's "double-check" hint."""
    q = quality or {}
    flags: list[str] = []
    band = str(q.get("quality_band") or "")
    overstatements = [o for o in (q.get("overstatements") or []) if str(o).strip()]
    red_flags = [r for r in (q.get("red_flags") or []) if str(r).strip()]
    if band == "uncertain":
        flags.append("quality_uncertain")
    if overstatements:
        flags.append("overstatements")
    if band == "flag":
        flags.append("quality_flag")
    if red_flags:
        flags.append("red_flags")
    shaky = band == "uncertain" or bool(overstatements)
    return flags, shaky


def _confidence(read_decision: str, grade: str, *, goal_evidence: str, shaky: bool) -> float:
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
        # A clean goal-MISS is the most confident skip call; a goal-matched keep is
        # softer; an UNKNOWN-board keep is least sure (kept only because the goal
        # signal was unavailable) — deliberately below the card's 0.6 Confirm floor.
        score = {"miss": 0.75, "match": 0.6, "unknown": 0.5}[goal_evidence]
    if not grade and read_decision:
        score -= 0.1
    if shaky:
        score -= 0.2
    return round(max(0.0, min(1.0, score)), 2)


def _rationale(read_decision: str, grade: str, verdict: str, *, goal_evidence: str, shaky: bool) -> str:
    """One short plain-language sentence explaining the proposal (for the UI)."""
    decision_txt = {
        "read": "the digest says read it",
        "skim": "the digest says skim it",
        "skip": "the digest says skip it",
        "": "no full-text digest yet",
    }[read_decision]
    grade_txt = f"grade {grade}" if grade else "ungraded"
    goal_txt = {"match": "matched a goal", "miss": "no goal match", "unknown": "goals not assessed"}[goal_evidence]
    base = f"{decision_txt}, {grade_txt}, {goal_txt} → {verdict.replace('_', ' ')}"
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
    ``goal_summaries`` the cached per-goal board (any ``relevant`` cell = goal
    match). Any of these may be ``None`` (a layer was skipped) — the mapping
    degrades to a safe ``could_read`` rather than guessing a hide.
    """
    read_decision = str((digest or {}).get("read_decision") or "").strip().lower()
    if read_decision not in {"read", "skim", "skip"}:
        read_decision = ""
    grade = _grade(digest, quality)
    goal_evidence = _goal_evidence(goal_summaries)

    verdict = _base_verdict(read_decision, grade, goal_evidence=goal_evidence)
    flags, shaky = _quality_flags(quality)
    confidence = _confidence(read_decision, grade, goal_evidence=goal_evidence, shaky=shaky)
    rationale = _rationale(read_decision, grade, verdict, goal_evidence=goal_evidence, shaky=shaky)

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


__all__ = ["propose_verdict"]
