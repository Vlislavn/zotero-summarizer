"""Triage / summarization / batch / corpus / calibration models."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from zotero_summarizer.domain import ReadingPriority, normalize_reading_priority


__all__ = [
    "SummarizeRequest",
    "TriageDimensions",
    "TriageResult",
    "QualityReview",
    "PaperDigest",
    "GoalSummary",
    "QualityEval",
    "ProposedVerdict",
    "RefinedSummary",
    "SummarizeResponse",
    "CorpusItem",
    "CorpusImportRequest",
    "TriageFeedbackRequest",
    "TriageFeedbackResponse",
    "TriageDimensionOverrideRequest",
    "CalibrationPeriodMetrics",
    "CalibrationMetricsResponse",
]


class SummarizeRequest(BaseModel):
    title: str = Field(..., min_length=1)
    doi: Optional[str] = None
    # pdf_path is required for the PDF pipeline (run_pipeline) but feed items
    # have no PDF when run_abstract_pipeline is called — empty string is the
    # documented sentinel for "abstract-only triage". The pipeline functions
    # validate presence themselves.
    pdf_path: str = ""
    abstract: Optional[str] = None


class TriageDimensions(BaseModel):
    goal_alignment: int = Field(default=3, ge=1, le=5)
    novelty_for_goals: int = Field(default=3, ge=1, le=5)
    methodological_rigor: int = Field(default=3, ge=1, le=5)
    actionability: int = Field(default=3, ge=1, le=5)
    evidence_strength: int = Field(default=3, ge=1, le=5)


class TriageResult(BaseModel):
    score: int = Field(..., ge=1, le=5)
    reading_priority: str = Field(default=ReadingPriority.COULD_READ.value)
    tags: List[str] = Field(default_factory=list)
    rationale: str = Field(..., min_length=1)
    dimensions: TriageDimensions | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("reading_priority")
    @classmethod
    def _validate_reading_priority(cls, value: str) -> str:
        return normalize_reading_priority(str(value or "").strip())


class QualityReview(BaseModel):
    """Peer-review-style quality assessment of a paper's FULL TEXT, independent
    of personal relevance. Produced by ``services.quality_review``. ``basis`` is
    set by the service (not the LLM): ``full_text`` when the PDF was read,
    ``not_assessed`` when no open-access PDF was available."""

    grade: str = Field(default="")  # A | B | C | D ("" = not assessed)
    soundness: int = Field(default=3, ge=1, le=5)
    novelty: int = Field(default=3, ge=1, le=5)
    significance: int = Field(default=3, ge=1, le=5)
    reproducibility: int = Field(default=3, ge=1, le=5)
    clarity: int = Field(default=3, ge=1, le=5)
    verdict: str = Field(default="")
    key_strength: str = Field(default="")
    key_weakness: str = Field(default="")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    basis: str = Field(default="full_text")

    @field_validator("grade", mode="before")
    @classmethod
    def _norm_grade(cls, value: Any) -> str:
        v = str(value or "").strip().upper()[:1]
        return v if v in {"A", "B", "C", "D"} else ""


class PaperDigest(QualityReview):
    """Condensed, scannable analysis of a paper's FULL TEXT — what it's about and
    how to use it (the user's 7-point investigation) — plus the inherited quality
    grade/dimensions. The prompt keeps every text field short so the result reads
    as a note, not a wall of text. Produced by ``services.quality_review``."""

    tldr: str = Field(default="")
    read_decision: str = Field(default="")  # read | skim | skip
    read_why: str = Field(default="")
    read_parts: List[str] = Field(default_factory=list)
    relevance: str = Field(default="")
    controversies: str = Field(default="")
    impact: str = Field(default="")
    unknown_unknowns: str = Field(default="")
    implementation: List[str] = Field(default_factory=list)
    # Remaining parts of the user's 11-part summary_structure (goal-aligned brief).
    executive_summary: str = Field(default="")
    key_findings: List[str] = Field(default_factory=list)
    methods: str = Field(default="")
    limitations: str = Field(default="")
    industry_impact: str = Field(default="")
    academy_impact: str = Field(default="")

    @field_validator("read_decision", mode="before")
    @classmethod
    def _norm_read_decision(cls, value: Any) -> str:
        v = str(value or "").strip().lower()
        return v if v in {"read", "skim", "skip"} else ""


class GoalSummary(BaseModel):
    """Per-goal grounded relevance result for the Goal Match Board.

    Produced by ``services.library._paper_goal_summaries`` via goal-conditioned
    hybrid retrieval over the paper's own chunks. One per standing research goal
    (the board always renders all cells). ``retrieval_state`` distinguishes a
    grounded negative (``miss`` = gate ran, nothing cleared the floor) from a
    degraded-retrieval neutral (``not_retrieved``) so a confident "not addressed"
    is never a false negative. ``summary`` is ``None`` unless the goal fired."""

    goal: str = Field(default="")
    relevant: bool = Field(default=False)
    retrieval_state: Literal["hit", "miss", "not_retrieved"] = Field(default="not_retrieved")
    score: float = Field(default=0.0)  # 0-3 best fused/reranked chunk score
    summary: Optional[str] = Field(default=None)  # <=3 sentences; None when abstained
    key_sections: List[str] = Field(default_factory=list)  # distinct evidence section titles
    supporting_quotes: List[str] = Field(default_factory=list)  # verbatim grounded spans
    abstained: bool = Field(default=True)


class QualityEval(BaseModel):
    """Reference-free, AUTHOR-BLIND full-text quality assessment computed by
    ``services.library.quality_eval`` (not the digest LLM). A coarse 3-band
    verdict — never a fine score — with the decomposed rubric + red flags that
    justify it, so it stays glassbox. ``uncertain`` = self-consistency runs
    disagreed (human look)."""

    quality_band: Literal["flag", "neutral", "highlight", "uncertain", ""] = Field(default="")
    grade: str = Field(default="")  # A-D (may echo the digest grade)
    rubric: Dict[str, str] = Field(default_factory=dict)  # check_name -> "yes"|"no"|"na"
    evidence: Dict[str, str] = Field(default_factory=dict)  # check_name -> verbatim quote
    red_flags: List[str] = Field(default_factory=list)
    overstatements: List[str] = Field(default_factory=list)  # abstract claims unsupported in body
    claim_grounding_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    # SHADOW (Phase A): MiniCheck encoder support prob per headline claim, recorded
    # for the encoder-vs-LLM A/B. Empty unless `quality_review.shadow_claim_check`
    # is on; does NOT affect the band or overstatements.
    claim_support_probs: Dict[str, float] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    passes_agreed: int = Field(default=0)  # self-consistency runs that voted the final band
    passes_total: int = Field(default=0)   # total self-consistency runs (renders "N/M agree")
    domain: str = Field(default="")  # clinical_bio | agentic | general
    basis: str = Field(default="full_text")
    # Type-aware coverage (the honest replacement for the unvalidated 1-5 self-scores):
    # which recognized-standard checklist was applied, and the fraction of APPLICABLE
    # items met (grounded "yes"). ``grade`` above is DERIVED from ``coverage_fraction``.
    paper_type: str = Field(default="")          # PaperType value the checklist was chosen for
    coverage_standard: str = Field(default="")   # e.g. "TRIPOD+AI / PROBAST+AI"
    coverage_met: int = Field(default=0)
    coverage_applicable: int = Field(default=0)
    coverage_fraction: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_critical: List[str] = Field(default_factory=list)  # critical items not met
    # Critical items the self-verification 2nd pass OVERTURNED (first pass over-claimed
    # them as met; the quote didn't establish the criterion) — transparency on the check.
    self_verification_demoted: List[str] = Field(default_factory=list)


class ProposedVerdict(BaseModel):
    """A PRE-DECIDED reading verdict the review-fleet computes in the background
    from a paper's already-cached deep-review signals (digest + quality_eval) — a
    SUGGESTION the human Confirms or Overrides, never an auto-applied label.

    Computed by ``services.library.review_fleet.propose.propose_verdict`` (a pure,
    deterministic function — NO LLM call; it only reads pre-computed signals).
    ``proposed`` is one of the four reading priorities; ``confidence`` is lowered
    (and a flag added) when the underlying quality signal is uncertain. It is
    attached to a reading-queue row as ``proposed_verdict`` and is NEVER routed
    through the hide/pin verdict logic — a ``dont_read`` suggestion must not
    auto-hide a paper."""

    proposed: Literal["must_read", "should_read", "could_read", "dont_read"]
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = Field(default="")
    flags: List[str] = Field(default_factory=list)
    # Provenance of the signals the proposal was derived from (for the UI + audit).
    digest_read_decision: str = Field(default="")  # read | skim | skip | "" (no digest)
    grade: str = Field(default="")  # A-D from the digest/quality_eval ("" = unknown)
    proposed_at: str = Field(default="")
    source: str = Field(default="review_fleet")


class RefinedSummary(BaseModel):
    executive_summary: str = Field(..., min_length=1)
    should_deep_read: str = Field(default="")
    key_sections_to_read: List[str] = Field(default_factory=list)
    relevance_to_research: str = Field(default="")
    controversial_points: str = Field(default="")
    industry_academy_impact: str = Field(default="")
    unknown_unknowns: str = Field(default="")
    implementation_quickstart: str = Field(default="")
    key_findings: List[str] = Field(default_factory=list)
    methods: str = Field(default="")
    limitations: str = Field(default="")

    @staticmethod
    def _coerce_text(value: Any, field_name: str, allow_bool: bool = False) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            if allow_bool:
                return "Yes" if value else "No"
            raise ValueError(f"{field_name} must be a string or list of strings")
        if isinstance(value, list):
            cleaned: List[str] = []
            for item in value:
                if not isinstance(item, str):
                    raise ValueError(f"{field_name} list items must be strings")
                stripped = item.strip()
                if stripped:
                    cleaned.append(stripped)
            return "; ".join(cleaned)
        if isinstance(value, str):
            return value.strip()
        raise ValueError(f"{field_name} must be a string or list of strings")

    @staticmethod
    def _coerce_text_list(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        text = str(value).strip()
        return [text] if text else []

    @field_validator("should_deep_read", mode="before")
    @classmethod
    def _coerce_should_deep_read(cls, value: Any) -> str:
        return cls._coerce_text(value, "should_deep_read", allow_bool=True)

    @field_validator("controversial_points", "unknown_unknowns", mode="before")
    @classmethod
    def _coerce_list_backed_string_fields(cls, value: Any) -> str:
        return cls._coerce_text(value, "field")

    @field_validator("key_sections_to_read", mode="before")
    @classmethod
    def _coerce_key_sections(cls, value: Any) -> List[str]:
        return cls._coerce_text_list(value)

    @field_validator("key_findings")
    @classmethod
    def _limit_key_findings(cls, value: List[str]) -> List[str]:
        cleaned = [v.strip() for v in value if v and v.strip()]
        return cleaned[:10]

    @field_validator("key_findings", mode="before")
    @classmethod
    def _coerce_key_findings(cls, value: Any) -> List[str]:
        return cls._coerce_text_list(value)


class SummarizeResponse(BaseModel):
    executive_summary: str
    should_deep_read: str = ""
    key_sections_to_read: List[str] = Field(default_factory=list)
    relevance_to_research: str = ""
    controversial_points: str = ""
    industry_academy_impact: str = ""
    unknown_unknowns: str = ""
    implementation_quickstart: str = ""
    key_findings: List[str] = Field(default_factory=list)
    methods: str = ""
    limitations: str = ""
    relevance_score: int = Field(..., ge=1, le=5)
    composite_relevance_score: float = Field(default=0.0, ge=0.0, le=5.0)
    reading_priority: str = ReadingPriority.COULD_READ.value
    tags: List[str] = Field(default_factory=list)
    triage_rationale: str
    triage_dimensions: TriageDimensions | None = None
    triage_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    corpus_affinity_score: float = Field(default=0.0, ge=-1.0, le=1.0)
    corpus_positive_similarity: float = Field(default=0.0, ge=0.0, le=1.0)
    corpus_negative_similarity: float = Field(default=0.0, ge=0.0, le=1.0)
    matched_goal: str = ""
    matched_goal_similarity: float = Field(default=0.0, ge=0.0, le=1.0)
    suggested_collections: List[str] = Field(default_factory=list)
    top_similar_items: List[str] = Field(default_factory=list)
    prestige_score: Optional[float] = Field(default=None, ge=1.0, le=5.0)
    prestige_venue: str = ""


class CorpusItem(BaseModel):
    item_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    abstract: str = ""
    tags: List[str] = Field(default_factory=list)
    collections: List[str] = Field(default_factory=list)
    annotation_count: int = Field(default=0, ge=0)
    manual_note_count: int = Field(default=0, ge=0)
    created_at: str | None = None


class CorpusImportRequest(BaseModel):
    items: List[CorpusItem] = Field(default_factory=list, max_length=5000)


class TriageFeedbackRequest(BaseModel):
    verdict: Literal["approve", "reject"]


class TriageFeedbackResponse(BaseModel):
    item_id: str
    verdict: Literal["approve", "reject"]
    signal: str
    queued: int = Field(default=0, ge=0)


class TriageDimensionOverrideRequest(BaseModel):
    goal_alignment: int | None = Field(default=None, ge=1, le=5)
    novelty_for_goals: int | None = Field(default=None, ge=1, le=5)
    methodological_rigor: int | None = Field(default=None, ge=1, le=5)
    actionability: int | None = Field(default=None, ge=1, le=5)
    evidence_strength: int | None = Field(default=None, ge=1, le=5)

    @model_validator(mode="after")
    def _ensure_any_override(self) -> "TriageDimensionOverrideRequest":
        if all(
            value is None
            for value in [
                self.goal_alignment,
                self.novelty_for_goals,
                self.methodological_rigor,
                self.actionability,
                self.evidence_strength,
            ]
        ):
            raise ValueError("at least one triage dimension override is required")
        return self


class CalibrationPeriodMetrics(BaseModel):
    total_feedback: int = Field(default=0, ge=0)
    approved_count: int = Field(default=0, ge=0)
    rejected_count: int = Field(default=0, ge=0)
    with_prediction_count: int = Field(default=0, ge=0)
    agreement_count: int = Field(default=0, ge=0)
    false_positive_count: int = Field(default=0, ge=0)
    false_negative_count: int = Field(default=0, ge=0)
    predicted_positive_count: int = Field(default=0, ge=0)
    actual_positive_count: int = Field(default=0, ge=0)
    true_positive_count: int = Field(default=0, ge=0)
    agreement_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    precision: float | None = Field(default=None, ge=0.0, le=1.0)
    recall: float | None = Field(default=None, ge=0.0, le=1.0)


class CalibrationMetricsResponse(BaseModel):
    periods: Dict[str, CalibrationPeriodMetrics] = Field(default_factory=dict)
