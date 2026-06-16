"""Typed verdicts for the faithfulness benchmark (ARE/Gaia2 judgment discipline).

A verdict is never a bare bool: ``Judgment.success`` is **tri-state**
(``True`` / ``False`` / ``None`` = could-not-judge) and the reason is a value
of the **closed** :class:`FailureReason` enum so failure modes are countable
across a run without string normalization. Harness faults (bad ground truth,
frozen-text drift) are their own reason and never deflate the model's score —
``success=None`` rows are excluded from every accuracy denominator.

Judgments are built at the exact rejection site inside ``_judge`` — the
structured fields are captured where the broken invariant is known, never
reconstructed later from a log string.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FailureReason(str, Enum):
    """Closed taxonomy of structurally distinct failure modes (one enum value
    per *mechanism*, never collapsed into a generic "wrong")."""

    # success=False — the model was judged and found wrong
    WRONG_ANSWER = "wrong_answer"                  # answerable QA, wrong content
    WRONG_ABSTAIN = "wrong_abstain"                # abstained on an answerable QA
    HALLUCINATED_ON_TRAP = "hallucinated_on_trap"  # answered an unanswerable trap
    UNSUPPORTED_CLAIM = "unsupported_claim"        # review claim not grounded in text
    JUDGE_REJECT = "judge_reject"                  # LLM judge rejected the residual
    MALFORMED_RESPONSE = "malformed_response"      # unparseable even after retry
    MODEL_ERROR = "model_error"                    # the trial itself raised

    # success=None — could not judge; excluded from accuracy denominators
    JUDGE_ERROR = "judge_error"                    # judge call failed after retry
    HARNESS_FAULT = "harness_fault"                # ground-truth/extraction defect

    @property
    def is_unjudgeable(self) -> bool:
        return self in (FailureReason.JUDGE_ERROR, FailureReason.HARNESS_FAULT)


class JudgeMethod(str, Enum):
    """Which rung of the hard-before-soft ladder decided the verdict."""

    EXACT = "exact"               # normalized exact match
    NUMERIC = "numeric"           # numeric parse + tolerance
    CONTAINMENT = "containment"   # normalized gold span ⊆ answer (capped length)
    TRAP_RULE = "trap_rule"       # deterministic abstain-on-trap rule
    ABSTAIN_RULE = "abstain_rule" # deterministic wrong-abstain rule
    VERBATIM = "verbatim"         # claim found verbatim (normalized) in paper
    LLM_JUDGE = "llm_judge"       # soft judge adjudicated the residual band
    NONE = "none"                 # decided before any comparison (errors/faults)


@dataclass
class Judgment:
    """One judged trial (or one judged claim).

    ``success`` is tri-state: ``True`` (judged correct), ``False`` (judged
    wrong), ``None`` (unjudgeable — judge error or harness fault).
    """

    success: bool | None
    method: JudgeMethod = JudgeMethod.NONE
    failure_reason: FailureReason | None = None
    details: str = ""
    judge_model: str | None = None   # set only when method == LLM_JUDGE
    judge_raw: str | None = None     # truncated raw judge output, for triage
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.success is False and self.failure_reason is None:
            raise ValueError("a failed Judgment must carry a FailureReason")
        if self.success is None and (
            self.failure_reason is None or not self.failure_reason.is_unjudgeable
        ):
            raise ValueError("an unjudgeable Judgment needs JUDGE_ERROR or HARNESS_FAULT")
        if self.success is True and self.failure_reason is not None:
            raise ValueError("a passing Judgment must not carry a FailureReason")

    def to_row(self) -> dict[str, Any]:
        """Serializable long-form row fragment (caller adds trial identity)."""
        return {
            "success": self.success,
            "method": self.method.value,
            "failure_reason": self.failure_reason.value if self.failure_reason else None,
            "details": self.details,
            "judge_model": self.judge_model,
            "judge_raw": (self.judge_raw[:500] if self.judge_raw else None),
            **({"extra": self.extra} if self.extra else {}),
        }

    def __str__(self) -> str:
        if self.success is True:
            return f"PASS via {self.method.value}"
        if self.success is False:
            return f"FAIL [{self.failure_reason.value}] via {self.method.value}: {self.details[:200]}"
        return f"UNJUDGEABLE [{self.failure_reason.value}]: {self.details[:200]}"
