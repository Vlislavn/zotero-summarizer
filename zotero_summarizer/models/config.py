"""Configuration models loaded from goals.yaml (LLM, corpus, gate, …)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from zotero_summarizer.models.providers import (
    DefaultModelConfig,
    LLMRoutingConfig,
    ProviderConfig,
    ProviderType,
)


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
    # Hybrid Library search (BM25 lexical + dense cosine + cross-encoder rerank),
    # all local. BM25 + dense reuse already-cached corpus data; the reranker
    # (cross-encoder) downloads once on first semantic search. Disable the
    # reranker to fall back to BM25+dense fusion order.
    bm25_enabled: bool = Field(default=True)
    reranker_enabled: bool = Field(default=True)
    reranker_model: str = Field(default="BAAI/bge-reranker-v2-m3", min_length=1)


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
    # --- Cold-start author-reputation prior --------------------------------
    # A brand-new preprint has no field-normalized citation percentile yet, so
    # citation prestige is structurally unavailable. When enabled, fall back to
    # the authors' FIELD-NORMALIZED standing (median of their works' OpenAlex
    # citation_normalized_percentile — the SAME signal the work-level prestige
    # trusts, NOT raw h-index, which is field/career-biased per the Leiden
    # Manifesto). The lift is asymmetric (can only raise above neutral, never
    # demote) and capped (Matthew-effect dosage control). It applies ONLY at
    # cold-start; once the paper accrues its own percentile, that takes over.
    cold_start_author_lift: bool = Field(default=True)
    cold_start_max_lift: float = Field(default=1.0, ge=0.0, le=2.0)
    # Convexity of the percentile→lift map (p**gamma, gamma>=1): higher gamma
    # means only genuinely top-standing authors approach the cap.
    cold_start_gamma: float = Field(default=1.5, ge=1.0, le=4.0)


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
    # Launch-time prewarm: on startup, background-compute deep reviews for the top-N
    # not-yet-cached unread picks so the FIRST open is instant (not just the second).
    # Skip-if-cached keeps re-launches cheap. 0 disables. Concurrency is inherited
    # from the deep_review job (serial on a local provider, parallel on a remote one).
    # Env override: ZS_DEEP_REVIEW_PREWARM_K. See services/library/deep_review_prewarm.py.
    prewarm_on_startup_k: int = Field(default=5, ge=0, le=20)
    max_pdf_bytes: int = Field(default=20_000_000, ge=1_000_000)
    fetch_timeout_secs: float = Field(default=30.0, ge=1.0, le=300.0)
    # Hard cap on full-text chars fed to the reviewer (context safety).
    max_text_chars: int = Field(default=60_000, ge=2_000)
    # Tier-aware deep-review cost. A provider flagged `lean_deep_review` (e.g. ollama,
    # which is prefill-bound on long prompts) uses the smaller `lean_max_text_chars`
    # + `lean_self_consistency_runs` (and batched goal summaries) to stay usable
    # (~few min); any other provider — incl. MLX, which is loopback but fast — uses the
    # full `max_text_chars` + `self_consistency_runs`. The tier is keyed on the
    # provider's `lean_deep_review` flag, NOT `is_local` (loopback ≠ lean).
    # `batch_goal_summaries` collapses the per-goal LLM calls into one (the biggest
    # call-count saving) and only applies on the lean tier.
    self_consistency_runs: int = Field(default=3, ge=1, le=7)
    lean_self_consistency_runs: int = Field(default=1, ge=1, le=7)
    lean_max_text_chars: int = Field(default=12_000, ge=2_000)
    batch_goal_summaries: bool = Field(default=True)
    unpaywall_email: str = Field(default="")
    # Phase A (SHADOW): also run the MiniCheck ENCODER claim-checker alongside the
    # LLM overstatement judge and record its per-claim support probs for an A/B —
    # NO behavior change (the band/overstatements stay LLM-decided). Off by default;
    # needs the optional `minicheck` dep. See services/model/claim_checker.py.
    shadow_claim_check: bool = Field(default=False)
    claim_check_model: str = Field(default="flan-t5-large")
    # Self-verification 2nd pass: one extra (short) LLM call that re-checks the CRITICAL
    # items a first pass marked met — does the grounding quote actually establish the
    # criterion? Overturns over-claims (the LLM positivity bias). On by default; set
    # false to skip the extra call on a slow local model.
    self_verification: bool = Field(default=True)
    # Use the IBM Docling PDF parser (structured tables + figure captions) instead of
    # the light fitz path. Off by default — needs the optional `docling` dep
    # (`uv pip install docling`) + downloads layout models on first use.
    use_docling: bool = Field(default=False)


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
    # ML-first backlog drain: when True (default) the bulk drain runs gate-only
    # (the classifier scores every survivor, NO per-item LLM call) and the LLM
    # is reserved for an on-demand full-text review per paper. Set False to keep
    # the legacy gate→LLM scoring of every survivor during the drain.
    bulk_drain_gate_only: bool = Field(default=True)
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
    # Per-stage provider+model routing. Optional in goals.yaml: when absent it is
    # synthesized from the legacy ``llm:`` block below (see ``_synthesize_routing``)
    # so existing configs keep working with zero edits.
    llm_routing: Optional[LLMRoutingConfig] = None
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

    @model_validator(mode="after")
    def _synthesize_routing(self) -> "GoalsConfig":
        """Back-compat: when goals.yaml has no ``llm_routing:`` block, build one
        from the legacy flat ``llm:`` block — a single ``default`` provider that
        all three stages inherit. Existing configs keep booting unchanged; the
        first PUT /api/config then persists the explicit ``llm_routing`` block.
        """
        if self.llm_routing is None:
            self.llm_routing = LLMRoutingConfig(
                providers=[
                    ProviderConfig(
                        name="default",
                        type=ProviderType.openai,
                        base_url=self.llm.api_base,
                        api_key_env=self.llm.api_key_env,
                        extra_body=self.llm.extra_body,
                    )
                ],
                default=DefaultModelConfig(provider="default", model=self.llm.refine_model),
            )
        return self
