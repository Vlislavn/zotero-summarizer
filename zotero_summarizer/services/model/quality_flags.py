"""Optional quality/ranking toggles: config ``quality_review.*`` overridden by ``ZS_*`` env.

No config / no state → the documented default, so each reader is safe when uninitialised.
"""
from __future__ import annotations

import os

from zotero_summarizer.services._common import state


def _quality_toggle_enabled(env_var: str, attr: str) -> bool:
    env = os.environ.get(env_var)
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    app_state = getattr(state(), "app_state", None)
    config = getattr(app_state, "config", None) if app_state is not None else None
    qr = getattr(config, "quality_review", None) if config is not None else None
    return bool(getattr(qr, attr, False))


def band_primary_enabled() -> bool:
    """Order-time deep-review QUALITY mode, shared by BOTH rank consumers (the
    Library queue ``_ranking`` and the Today slate allocator): grade-only (default
    — the shipped, measured behaviour) vs band-primary (highlight ↑ / flag ↓;
    neutral & uncertain → exactly 0.0), a Phase-2-MEASURED arm. Config
    ``quality_review.quality_band_primary``, overridden by ``ZS_QUALITY_BAND_PRIMARY``.
    Optional-feature boundary: no config / no state → the grade-only default."""
    return _quality_toggle_enabled("ZS_QUALITY_BAND_PRIMARY", "quality_band_primary")


def rank_quality_first_enabled() -> bool:
    """Quality-FIRST Today slate ordering (``services/model/rank_blend_quality``):
    quality LEADS the key, topicality soft-gates — replacing the default
    relevance×goal×prestige blend so a high-quality off-topic paper out-ranks a
    low-quality on-topic one (the user's directive). Config
    ``quality_review.rank_quality_first``, overridden by ``ZS_RANK_QUALITY_FIRST``.
    OFF by default (pending the Track-C frontier eval). Optional-feature boundary:
    no config / no state → OFF."""
    return _quality_toggle_enabled("ZS_RANK_QUALITY_FIRST", "rank_quality_first")


def rank_interleave_enabled() -> bool:
    """P3 ONLINE interleave (ADR-A9 / GAP-G11): the Today slate builds BOTH
    ranking arms (A0 control blend + A2 quality-first), team-draft-merges them
    into the one slate the user sees (``services/model/team_draft``) and logs
    per-item arm attribution (``storage/interleave``) so the user's normal
    verdicts decide the flip via SPRT (``tools/eval_interleave.py``). Blind: the
    UI never shows the arm. Takes PRECEDENCE over ``rank_quality_first`` (the
    interleave already contains both arms). Config
    ``quality_review.rank_interleave``, overridden by ``ZS_RANK_INTERLEAVE``.
    OFF by default. Optional-feature boundary: no config / no state → OFF."""
    return _quality_toggle_enabled("ZS_RANK_INTERLEAVE", "rank_interleave")


def quality_promote_enabled() -> bool:
    """Quality → must_read band promotion toggle (the inverse of the auto_quality
    hide gate). The gate's compressed regressor never reaches the must_read band on
    its own; promotion lifts a high-quality + on-goal + gate-confirmed paper to
    must_read. ON by default (``tools/eval_quality_promote.py`` measured 0 flooding +
    1.00 must/should precision on firewalled user verdicts). Config
    ``quality_review.quality_promote``, overridden by ``ZS_QUALITY_PROMOTE=0``.
    Optional-feature boundary: no config / no state → OFF (safe when uninitialised)."""
    return _quality_toggle_enabled("ZS_QUALITY_PROMOTE", "quality_promote")
