"""Configuration models loaded from goals.yaml (LLM, corpus, gate, …)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


__all__ = [
    "LLMConfig",
    "PromptOverrides",
    "CorpusConfig",
    "PrestigeConfig",
    "FullTextRefineConfig",
    "QualityReviewConfig",
    "ClassifierGateConfig",
    "GoalsConfig",
]


class LLMConfig(BaseModel):
    draft_model: str = Field(..., min_length=1)
    refine_model: str = Field(..., min_length=1)
    api_base: str = Field(..., min_length=1)
    api_key_env: str = Field(..., min_length=1)
    # Provider-specific kwargs forwarded to OpenAI-compatible endpoints as `extra_body`.
    # vLLM-served reasoning models accept `chat_template_kwargs`; real OpenAI rejects it.
    # Leave None/empty for OpenAI; set for OnPrem/qwen3/etc.
    extra_body: Optional[Dict[str, Any]] = None


class PromptOverrides(BaseModel):
    map: Optional[str] = None
    reduce: Optional[str] = None
    refine: Optional[str] = None
    triage: Optional[str] = None
    quality_review: Optional[str] = None
    paper_digest: Optional[str] = None


class CorpusConfig(BaseModel):
    enabled: bool = Field(default=True)
    embedding_model: str = Field(default="sentence-transformers/all-MiniLM-L6-v2", min_length=1)
    similarity_threshold: float = Field(default=-0.30, ge=-1.0, le=1.0)
    stale_days_for_weak_negative: int = Field(default=30, ge=1, le=3650)


class PrestigeConfig(BaseModel):
    """OpenAlex-backed author/venue prestige enrichment.

    Disabled by default to avoid surprise network calls; enable to blend
    h-index + venue impact + citations into the composite score (weight
    controlled by ``weight``; rest of the LLM component is rebalanced).
    """

    enabled: bool = Field(default=False)
    weight: float = Field(default=0.15, ge=0.0, le=1.0)
    cache_ttl_days: int = Field(default=30, ge=1, le=365)
    fallback_neutral: float = Field(default=3.0, ge=1.0, le=5.0)
    user_agent_email: str = Field(default="")
    require_doi: bool = Field(default=False)


class FullTextRefineConfig(BaseModel):
    """Two-stage triage: fetch PDF + re-score top plateau picks with full text."""

    enabled: bool = Field(default=False)
    top_k: int = Field(default=2, ge=1, le=10)
    max_pdf_bytes: int = Field(default=20_000_000, ge=1_000_000)
    fetch_timeout_secs: float = Field(default=30.0, ge=1.0, le=300.0)
    unpaywall_email: str = Field(default="")


class QualityReviewConfig(BaseModel):
    """Full-text, peer-review-style quality assessment for the top-K Today picks.

    Distinct from ``full_text_refine`` (which re-scores *relevance*): this reads
    the PDF and judges the paper's intrinsic quality, independent of the user's
    research goals."""

    enabled: bool = Field(default=True)
    top_k: int = Field(default=5, ge=1, le=20)
    max_pdf_bytes: int = Field(default=20_000_000, ge=1_000_000)
    fetch_timeout_secs: float = Field(default=30.0, ge=1.0, le=300.0)
    # Hard cap on full-text chars fed to the reviewer (context safety).
    max_text_chars: int = Field(default=60_000, ge=2_000)
    unpaywall_email: str = Field(default="")


class ClassifierGateConfig(BaseModel):
    """Phase 1.13 hybrid daemon: classifier as fast-reject before LLM.

    When ``enabled``, the daemon trains (or loads cached) a classifier from the
    golden CSV at startup. For every dedup'd feed item the gate predicts a
    4-class priority; items whose priority is in ``drop_priorities`` skip the
    LLM entirely and land in `processed_feed_items` with decision
    ``gate_rejected``. Everything else flows through the existing pipeline.
    """

    enabled: bool = Field(default=False)
    model_name: str = Field(default="tabpfn")           # tabpfn | lightgbm | logreg
    drop_priorities: List[str] = Field(default_factory=lambda: ["dont_read"])
    pca_dim: int = Field(default=100, ge=2, le=500)
    n_folds: int = Field(default=5, ge=2, le=10)
    # Deprecated in Sprint-1 redesign (May 2026): kept for config-forward-
    # compat but no longer applied. The regression-based classifier emits
    # priorities through `domain.score_to_priority` and the deterministic
    # bucketing is the single source of truth. Will be removed in a future
    # major-version bump.
    raw_score_dont_read_below: float = Field(default=0.0, ge=0.0, le=1.0)
    # Phase 1.15 (2.3): counterfactual gate audit. At end of each
    # `_apply_classifier_gate`, resurrect N random rows that the gate
    # just dropped and push them through the rest of the pipeline as
    # if the gate had let them through (marked with `_resurrected_for_audit`
    # so the UI shows a 🎲 chip). User's verdict on resurrected rows is
    # a clean unbiased estimate of gate false-negative rate. 0 disables.
    audit_sample_per_tick: int = Field(default=1, ge=0, le=20)

    @field_validator("model_name")
    @classmethod
    def _validate_model_name(cls, value: str) -> str:
        v = (value or "").strip().lower()
        if v not in {"tabpfn", "lightgbm", "logreg"}:
            raise ValueError(
                f"model_name must be one of tabpfn/lightgbm/logreg, got {value!r}"
            )
        return v

    @field_validator("drop_priorities")
    @classmethod
    def _validate_drop_priorities(cls, value: List[str]) -> List[str]:
        allowed = {"must_read", "should_read", "could_read", "dont_read"}
        cleaned = [p.strip() for p in value if p and p.strip()]
        bad = [p for p in cleaned if p not in allowed]
        if bad:
            raise ValueError(
                f"drop_priorities entries must be a subset of {sorted(allowed)}; got {bad}"
            )
        return cleaned


class GoalsConfig(BaseModel):
    research_goals: List[str] = Field(default_factory=list)
    triage_criteria: List[str] = Field(default_factory=list)
    relevance_scale: Dict[int, str]
    reading_priority_scale: Dict[str, str] = Field(default_factory=dict)
    summary_structure: List[str] = Field(default_factory=list)
    output_language: str = Field(default="English")
    llm: LLMConfig
    prompts: PromptOverrides = Field(default_factory=PromptOverrides)
    corpus: CorpusConfig = Field(default_factory=CorpusConfig)
    prestige: PrestigeConfig = Field(default_factory=PrestigeConfig)
    full_text_refine: FullTextRefineConfig = Field(default_factory=FullTextRefineConfig)
    quality_review: QualityReviewConfig = Field(default_factory=QualityReviewConfig)
    classifier_gate: ClassifierGateConfig = Field(default_factory=ClassifierGateConfig)

    @field_validator("research_goals", "triage_criteria", "summary_structure")
    @classmethod
    def _non_empty_strings(cls, value: List[str]) -> List[str]:
        cleaned = [v.strip() for v in value if v and v.strip()]
        if not cleaned:
            raise ValueError("list must contain at least one non-empty item")
        return cleaned

    @field_validator("relevance_scale", mode="before")
    @classmethod
    def _normalize_relevance_scale_keys(cls, value: Any) -> Dict[int, str]:
        if not isinstance(value, dict):
            raise ValueError("relevance_scale must be a map of score to description")

        normalized: Dict[int, str] = {}
        for key, text in value.items():
            score = int(key)
            normalized[score] = str(text).strip()
        return normalized

    @field_validator("relevance_scale")
    @classmethod
    def _validate_relevance_scale(cls, value: Dict[int, str]) -> Dict[int, str]:
        expected = {1, 2, 3, 4, 5}
        if set(value.keys()) != expected:
            raise ValueError("relevance_scale must include keys 1,2,3,4,5")
        if any(not v for v in value.values()):
            raise ValueError("relevance_scale descriptions must be non-empty")
        return value
