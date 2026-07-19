"""External metadata-enrichment source configs (``prestige.*`` / ``openreview.*``),
split from ``config.py`` to stay under the LOC ceiling.

Both sections are network-dependent SEARCH/triage enrichment sources gated
identically by ``ZS_OFFLINE`` (``services/_common.read_config``'s
``_disable_prestige_when_offline`` / ``_disable_openreview_when_offline``).
"""
from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["PrestigeConfig", "OpenReviewConfig"]


class PrestigeConfig(BaseModel):
    """OpenAlex-backed author/venue prestige enrichment.

    Enabled by default: OpenAlex is keyless, cached (``cache_ttl_days``) and fails
    soft to ``fallback_neutral``, so the field-normalized citation percentile is
    worth having out of the box — leaving it off left the whole prestige subsystem
    (blend weight, cold-start author prior, the Library "Prestige" facet) dark, so
    every item read as "new". Air-gapped runs disable it: ``ZS_OFFLINE`` forces it
    off (see ``services/_common.read_config``) and ``ZS_PRESTIGE_ENABLED=0`` opts
    out explicitly. Blends h-index + venue impact + citations into the composite
    score (weight ``weight``; rest of the LLM component is rebalanced).
    """

    enabled: bool = Field(default=True)  # ZS_OFFLINE forces off (read_config); ZS_PRESTIGE_ENABLED=0 opts out
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


class OpenReviewConfig(BaseModel):
    """Authenticated OpenReview peer-review SEARCH source (Targeted Search only).

    SIGNAL-ONLY: a blind-judge eval refuted prestige-boost ranking, so this
    carries no promote/gate/rating knob — the tier/venue signal surfaces as a
    display chip (`services/search/_relevance.py`), never a re-rank input.
    Creds come from ``OPENREVIEW_USERNAME``/``OPENREVIEW_PASSWORD`` (env, read
    at point-of-use — see ``integrations/openreview.py``), never this config.
    """

    enabled: bool = Field(default=True)
    venues: list[str] = Field(
        default_factory=lambda: ["ICLR.cc", "NeurIPS.cc", "ICML.cc", "COLM.cc", "TMLR", "MIDL.io"]
    )
    year_min: int = Field(default=2024, ge=2000, le=2100)
    limit: int = Field(default=25, ge=1, le=100)
