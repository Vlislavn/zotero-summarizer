"""Configuration models loaded from goals.yaml (LLM, corpus, gate, …)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from zotero_summarizer.models.enrichment_config import OpenReviewConfig, PrestigeConfig
from zotero_summarizer.models.feeds_config import FeedsConfig
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
    "FeedsConfig",
    "PrestigeConfig",
    "OpenReviewConfig",
    "FullTextRefineConfig",
    "QualityReviewConfig",
    "ClassifierGateConfig",
    "UniversityAccessConfig",
    "GoalsConfig",
    "USER_OWNED_KEYS",
]


def _validate_choice(value: str, allowed: frozenset[str], field: str) -> str:
    v = (value or "").strip().lower()
    if v not in allowed:
        raise ValueError(f"{field} must be one of {sorted(allowed)}, got {value!r}")
    return v


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
    # PROVISIONAL embedder: goal_sim via MiniLM is the strongest ranking lever
    # (blind-judge Spearman 0.72 vs the gate's 0.40), but MiniLM has NOT been measured
    # head-to-head vs BGE-M3 / SPECTER2 — run `tools/eval_goal_embedder.py` (Phase 5).
    # Override: ZS_CORPUS_EMBEDDING_MODEL.
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


class FullTextRefineConfig(BaseModel):
    """Two-stage triage: fetch PDF + re-score top plateau picks with full text."""

    enabled: bool = Field(default=False)
    top_k: int = Field(default=2, ge=1, le=10)
    max_pdf_bytes: int = Field(default=50_000_000, ge=1_000_000)  # figure-heavy clinical PDFs run >20 MB
    fetch_timeout_secs: float = Field(default=30.0, ge=1.0, le=300.0)
    unpaywall_email: str = Field(default="")


class RecoverAbstractConfig(BaseModel):
    """Acquire-before-score rescue for abstract-less, high-goal feed items.

    Prestige-journal RSS (Nature/Science/Cell/NEJM/Annals) ships a title-only
    boilerplate "abstract", so the classifier gate scores the paper on no real
    content and drops it to ``dont_read``. When such a dropped item's strongest
    research-goal cosine (already computed by the gate, stashed on
    ``aux_context['goal_sims']``) clears ``goal_sim_threshold``, fetch its full
    text via the review-fleet acquisition chain and re-score it on the PDF
    BEFORE the gate verdict stands. ``max_per_tick`` caps the browser/paywall
    fetch so it never runs across a whole journal backlog. See
    ``services/triage/README.md``.
    """

    enabled: bool = Field(default=True)
    goal_sim_threshold: float = Field(default=0.45, ge=0.0, le=1.0)
    max_per_tick: int = Field(default=3, ge=0, le=20)
    min_abstract_chars: int = Field(default=120, ge=0, le=2000)


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
    # Auto-review newly-materialized Today picks at the END of a daily-selection
    # tick (not just the launch-time prewarm). The full-text quality review only
    # fired on startup / manual trigger before; this closes the gap so papers
    # materialized during a tick get reviewed without a restart or button press.
    # Fire-and-forget on the deep_review job pool (provider-aware: parallel on a
    # remote provider, queued on a local one) — never blocks the tick. The items
    # are already in Zotero at this point (daily selection materialized them), so
    # their PDFs are fetchable. 0 disables. See services/triage/feeds/_tick.py.
    auto_on_tick_k: int = Field(default=10, ge=0, le=20)
    # How many of the TOP Today feed papers (by composite rank) also get the full,
    # heavy paper RENDER (notes.md / presentation.html / figures) per daemon tick —
    # built in-place from the already-cached in-place review + cached PDF (no extra LLM),
    # so a top feed paper opens with the same beautiful brief as a library item. Renders
    # only keys already reviewed (one tick after the review, no race) + skips already-
    # rendered ones. 0 disables. Env: ZS_QUALITY_REVIEW_RENDER_ON_TICK_K.
    render_on_tick_k: int = Field(default=3, ge=0, le=10)
    # Order-time quality-lift mode. OFF (default) = grade-only A/B/C/D bonus (the
    # shipped, measured behaviour). ON = band-primary (highlight ↑ / flag ↓; neutral
    # & uncertain resolve to exactly 0.0) — a Phase-2-MEASURED arm, not flipped on
    # ahead of its gate. Env override (consumer-side): ZS_QUALITY_BAND_PRIMARY.
    # See services/library/_ranking._band_primary_enabled + model/rank_blend.quality_bonus.
    quality_band_primary: bool = Field(default=False)
    # Quality-FIRST Today slate ordering (services/model/rank_blend_quality): quality
    # LEADS the order key (key = q·(0.5+0.5·t)), topicality soft-gates — so a
    # high-quality off-topic paper out-ranks a low-quality on-topic one (the user's
    # directive). Replaces the default relevance×goal×prestige blend for the Today
    # slate only (Library queue unchanged). OFF by default until the Track-C frontier
    # eval (contamination@10 / q-lift@10) clears the ship gate. Env: ZS_RANK_QUALITY_FIRST.
    rank_quality_first: bool = Field(default=False)
    # P3 ONLINE interleave (ADR-A9/GAP-G11): build BOTH ranking arms (A0 control
    # blend + A2 quality-first) and team-draft-merge them into the one Today slate,
    # logging per-item arm attribution (storage/interleave) so the user's normal
    # verdicts decide the flip via SPRT (tools/eval_interleave.py). Blind — the UI
    # never shows the arm. Takes precedence over rank_quality_first (the interleave
    # already contains both arms). OFF by default. Env: ZS_RANK_INTERLEAVE.
    rank_interleave: bool = Field(default=False)
    # Quality → must_read PROMOTION (the inverse of the auto_quality hide gate).
    # The gate's regressor scores are compressed toward the mean → never reach the
    # must_read band (≥4.5) on their own (0/69 must recall), so must_read is empty
    # even for genuinely great papers. Promotion lifts a high-quality + on-goal +
    # gate-confirmed paper to must_read — the band the gate can't reach. ON by
    # default: tools/eval_quality_promote.py, on 68 firewalled user-verdict rows,
    # measured 0 flooding (no dont_read promoted) + 1.00 must/should precision at the
    # floors below. Env override: ZS_QUALITY_PROMOTE=0. Rule: rank_blend.promote_band.
    quality_promote: bool = Field(default=True)
    # Strict multi-signal AND (precision-first): must_read requires grade in {A,B} AND
    # goal_sim ≥ this AND gate relevance_score ≥ this. relevance_floor 3.0 (NOT the 3.5
    # should-band boundary): the eval showed 3.5 promotes just 1 paper (gate compression),
    # 3.0 promotes 3 must + 9 should — still 1.00 precision, 0 flooding; below 3.0 both
    # decay. Re-run the eval as verdicts accrue.
    quality_promote_goal_sim: float = Field(default=0.55, ge=0.0, le=1.0)
    quality_promote_relevance_floor: float = Field(default=3.0, ge=1.0, le=5.0)
    max_pdf_bytes: int = Field(default=50_000_000, ge=1_000_000)  # figure-heavy clinical PDFs run >20 MB
    fetch_timeout_secs: float = Field(default=30.0, ge=1.0, le=300.0)
    # Hard cap on full-text chars fed to the reviewer (context safety).
    # validated-GOOD: faithbench run 20260612_193120 (commit 55bdd09) — 96.4% full-text
    # QA accuracy, 0% trap-hallucination, 93.9% claim support @ 60k on MLX-35B. NOT a
    # head-to-head budget sweep (60k vs 30k vs 12k) — Phase 5 (bench_deep_review).
    # Override: ZS_QUALITY_REVIEW_MAX_TEXT_CHARS.
    max_text_chars: int = Field(default=60_000, ge=2_000)
    # Tier-aware deep-review cost. A provider flagged `lean_deep_review` (e.g. ollama,
    # which is prefill-bound on long prompts) uses the smaller `lean_max_text_chars`
    # + `lean_self_consistency_runs` (and batched goal summaries) to stay usable
    # (~few min); any other provider — incl. MLX, which is loopback but fast — uses the
    # full `max_text_chars` + `self_consistency_runs`. The tier is keyed on the
    # provider's `lean_deep_review` flag, NOT `is_local` (loopback ≠ lean).
    # `batch_goal_summaries` collapses the per-goal LLM calls into one (the biggest
    # call-count saving) and only applies on the lean tier.
    # PROVISIONAL: operational baseline, NOT independently knob-swept (no recorded
    # 1-vs-3-vs-7 head-to-head). Phase 5 adds a self_consistency sweep axis to bench_deep_review.
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
    # validated: paper_quality_bench (qwen3_5_4b, 2026-06-17) — precision 0.857 /
    # recall 1.0 / F1 0.923 (n=72) catching overclaims. ZS_QUALITY_REVIEW_SELF_VERIFICATION.
    self_verification: bool = Field(default=True)
    # Use the IBM Docling PDF parser (structured tables + figure captions) instead of
    # the light fitz path. Off by default — needs the optional `docling` dep
    # (`uv pip install docling`) + downloads layout models on first use.
    # MEASURED-better but kept OFF for the dep cost: paper_quality_bench (2026-06-17)
    # docling 1.0 table+figure recall vs fitz 0.0 (~7-9s/paper). ZS_QUALITY_REVIEW_USE_DOCLING=1.
    use_docling: bool = Field(default=False)
    # Review web ARTICLES (blogs/Substack/news/docs with HTML full text but no PDF) by
    # rendering the page to a PDF (headless `page.pdf`) so the review pipeline can digest
    # them. ON by default: a non-paper is reviewed for RELEVANCE only (NON_PAPER type, no
    # scientific A–D grade — see `quality_eval`/`paper_type`). Needs the `browser` extra.
    # The review fleet's web-article rung is gated on this. See
    # `services/library/_pdf_acquire.py`.
    review_web_articles: bool = Field(default=True)
    # Chunking strategy for the deep-review digest. A/B'd by tools/eval_chunking.py
    # (coverage/latency/tokens). MEASURED 2026-06-27: rank ≈ map_reduce on coverage (both ~0.003
    # late_recall — noise floor) + completeness (1.00); map_reduce is 6-13× SLOWER (18s→116-249s)
    # → rank stays default. map_reduce only wins if a faithfulness JUDGE A/B (not token-recall)
    # shows a real coverage gain — deferred (judge 503).
    #   rank       = BM25-select chunks to fit max_text_chars, ONE digest call (shipped default)
    #   prefix     = naive truncation to max_text_chars (baseline)
    #   map_reduce = summarise each chunk on the cheap/LOCAL model, synthesise on the API model
    #                (chunk-local / synthesis-API — whole-paper coverage, higher call count)
    chunk_strategy: str = Field(default="rank")
    # MEASURED 2026-06-27 (4k/8k/16k sweep): chunk size only affects map_reduce LATENCY (larger =
    # fewer map calls: 16k 116s < 8k 184s < 4k 249s); coverage/completeness identical. 8000 is a
    # mid-range default; only matters when chunk_strategy=map_reduce (rank ignores it). Capped by
    # the MAP model's num_ctx if map_reduce is enabled with a small local map model.
    map_chunk_chars: int = Field(default=8000, ge=1000)  # chunk size for the map_reduce map step
    # Auto QUALITY GATE (precision mode) — quality as a HARD filter, not an additive
    # bonus. A bad-quality paper on-topic is hidden (dont_read, source=auto_quality,
    # one-tap reversible) even if goal-matched; match stays the ranker among survivors.
    # Two cascaded layers: L1 = the feed-stage LLM's relevance_score floor (abstract-
    # based, 99% coverage); L2 = the deep-review grade/band (full-text, top-K). ON by
    # default (the user's explicit precision-mode ask); set false to disable. Fires on
    # the triage tick (whole surface) + per deep-review settle. See
    # services/library/quality_gate.py. Env overrides below.
    auto_quality_gate: bool = Field(default=True)
    # L1 floor: a relevance_score <= this is hidden (1-5 scale; 2 = only the clearest
    # misses). Conservative — hides ~2% of shown rows; raise to hide more.
    auto_quality_llm_floor: int = Field(default=2, ge=1, le=4)
    # L2 hide-set: deep-review grades (A/B/C/D) that trigger a hide. Default {D} only
    # (the full-text "bad quality" verdict); C is borderline and left for the human.
    auto_quality_hide_grades: tuple[str, ...] = Field(default=("D",))
    # L2 hide-set: quality bands that trigger a hide. Default {flag} (the weakest band);
    # `uncertain`/`neutral` are NOT hidden (they already cut the fleet confidence).
    auto_quality_hide_bands: tuple[str, ...] = Field(default=("flag",))

    @field_validator("chunk_strategy")
    @classmethod
    def _validate_chunk_strategy(cls, value: str) -> str:
        return _validate_choice(value, frozenset({"rank", "prefix", "map_reduce"}), "chunk_strategy")


class UniversityAccessConfig(BaseModel):
    """Institutional full-text access for the review fleet's PDF acquisition.

    For non-arXiv / paywalled papers (Cloudflare-protected like bioRxiv, or behind a
    journal subscription) a headless download can't pass. When ``enabled``, the fleet
    drives a real browser (a persistent profile the user logs into once via the
    ``login_url``) to fetch the PDF using the user's institutional session. Disabled
    by default — the optional ``patchright`` browser dependency must be installed.

    ``ezproxy_prefix`` is OPTIONAL: set it for an EZproxy library (the prefix is
    prepended to the DOI/publisher URL); leave it empty for SSO/OpenAthens, where the
    persisted login session carries access without a URL rewrite. ``browser_profile_dir``
    blank means the app-owned default under ``data/`` (see Settings)."""

    enabled: bool = Field(default=False)
    ezproxy_prefix: str = Field(default="")
    login_url: str = Field(default="")
    browser_profile_dir: str = Field(default="")
    headless: bool = Field(default=True)
    fetch_timeout_secs: float = Field(default=60.0, ge=5.0, le=600.0)
    # Reuse an EXISTING browser's session instead of a separate in-app login: read that
    # browser's cookie store (``browser-cookie3``) and inject it into the fetch. ``""``
    # = off. NOTE: ``safari`` does NOT work on macOS 15+/26 — Apple hardened Safari's
    # container so its cookies are unreadable even with Full Disk Access; use ``chrome``
    # / ``firefox``. The in-app login (``login_url``) remains the fallback.
    cookie_browser: str = Field(default="")
    # Browser DISTRIBUTION CHANNEL to drive: ``chrome`` (default) launches the REAL
    # Google Chrome binary, whose fingerprint/UA match the ``cf_clearance`` cookie a
    # cookie-source Chrome earned — so Cloudflare publishers (Nature/npj) accept the
    # session, unlike bundled chromium (``""``). Any Playwright channel
    # (chrome/chrome-beta/msedge/…); ``""`` = bundled chromium (no Chrome install needed).
    browser_channel: str = Field(default="chrome")

    @field_validator("cookie_browser")
    @classmethod
    def _validate_cookie_browser(cls, value: str) -> str:
        allowed = frozenset({"", "chrome", "chromium", "firefox", "edge", "brave", "safari", "opera", "vivaldi"})
        return _validate_choice(value, allowed, "cookie_browser")

    @field_validator("browser_channel")
    @classmethod
    def _validate_browser_channel(cls, value: str) -> str:
        allowed = frozenset({"", "chromium", "chrome", "chrome-beta", "chrome-dev", "chrome-canary",
                              "msedge", "msedge-beta", "msedge-dev", "msedge-canary"})
        return _validate_choice(value, allowed, "browser_channel")


class ClassifierGateConfig(BaseModel):
    """Phase 1.13 hybrid daemon: classifier as fast-reject before LLM.

    When ``enabled``, the daemon trains (or loads cached) a classifier from the
    golden CSV at startup. For every dedup'd feed item the gate predicts a
    4-class priority; items whose priority is in ``drop_priorities`` skip the
    LLM entirely and land in `processed_feed_items` with decision
    ``gate_rejected``. Everything else flows through the existing pipeline.
    """

    # The gate is ON by default and uses lightgbm (fast, low-RAM). Cold-start safe —
    # when the golden CSV is absent the gate stays off (lifecycle._init_classifier_gate).
    # PROVENANCE: the on-disk lightgbm model (trained 2026-06-26) scores oof_spearman
    # 0.707 (>> logreg 0.568); reports/ (2026-05-13) AUC: lightgbm 0.711 CV / 0.758
    # holdout. tabpfn is HIGHER-accuracy (AUC 0.796 CV / 0.833 holdout) but heavier on
    # RAM — set ZS_CLASSIFIER_GATE_MODEL_NAME=tabpfn if memory allows. CAVEAT/Phase-5:
    # logreg beats lightgbm on the TEMPORAL hold-out (0.292 vs 0.217), and no
    # head-to-head `goldenset eval-baseline` on the current golden set has been run.
    enabled: bool = Field(default=True)
    model_name: str = Field(default="lightgbm")         # lightgbm (fast) | tabpfn (best AUC) | logreg
    drop_priorities: List[str] = Field(default_factory=lambda: ["dont_read"])
    # ML-first backlog drain: when True (default) the bulk drain runs gate-only
    # (the classifier scores every survivor, NO per-item LLM call) and the LLM
    # is reserved for an on-demand full-text review per paper. Set False to keep
    # the legacy gate→LLM scoring of every survivor during the drain.
    bulk_drain_gate_only: bool = Field(default=True)
    # PROVISIONAL: pca_dim/n_folds are unswept defaults — no optuna `goldenset tune`
    # run exists. Phase 5 tunes them. (pca_dim only affects the tabpfn embedding path.)
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
        return _validate_choice(value, frozenset({"tabpfn", "lightgbm", "logreg"}), "model_name")

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
    feeds: FeedsConfig = Field(default_factory=FeedsConfig)
    prestige: PrestigeConfig = Field(default_factory=PrestigeConfig)
    openreview: OpenReviewConfig = Field(default_factory=OpenReviewConfig)
    full_text_refine: FullTextRefineConfig = Field(default_factory=FullTextRefineConfig)
    recover_abstract: RecoverAbstractConfig = Field(default_factory=RecoverAbstractConfig)
    quality_review: QualityReviewConfig = Field(default_factory=QualityReviewConfig)
    classifier_gate: ClassifierGateConfig = Field(default_factory=ClassifierGateConfig)
    university_access: UniversityAccessConfig = Field(default_factory=UniversityAccessConfig)

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


# The ONLY config keys a human authors: research intent + the unavoidable LLM
# connection + institutional access (authorization). Everything else in
# ``GoalsConfig`` is system-owned — a validated code default, refined per-user by
# ``data/calibration.json`` and overridable via ``ZS_*`` env vars (see
# ``services/config_overrides.py``). ``write_user_config`` persists ONLY these keys
# to ``goals.yaml`` so the file stays intent-only; the rest fall back to defaults.
USER_OWNED_KEYS: frozenset[str] = frozenset(
    {
        "research_goals",
        "triage_criteria",
        "relevance_scale",
        "reading_priority_scale",
        "summary_structure",
        "output_language",
        "llm",
        "llm_routing",
        "university_access",
    }
)
