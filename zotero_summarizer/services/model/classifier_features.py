"""Classifier features functions (split from classifier.py)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


def current_feature_year() -> int:
    """UTC calendar year shared by recency features and training-input identity."""
    return datetime.now(timezone.utc).year


def _resolve_embedding_cache(corpus_db_path: Path, model_name: str) -> Any:
    """Return the process-wide :class:`EmbeddingCache` instead of a fresh one.

    Constructing a new ``EmbeddingCache`` per ``predict()`` batch made the
    MiniLM weights reload once per 50-item batch (the 178×-reload perf bug:
    model memoization is instance-local). The runtime singleton is ALSO the
    corpus write instance; matrix caches observe database fingerprints across
    instances. Creates a fresh instance when no runtime is
    wired (training / tests) or the db/model don't match (post model-swap)."""
    from zotero_summarizer.storage.corpus import EmbeddingCache
    from zotero_summarizer.services._common import state

    shared = getattr(state(), "embedding_cache", None)
    if (
        shared is not None
        and str(getattr(shared, "db_path", "")) == str(corpus_db_path)
        and getattr(shared, "model_name", None) == model_name
    ):
        return shared
    return EmbeddingCache(corpus_db_path, model_name)


def _build_aux_providers(
    corpus_db_path: Path,
    goals_config: Any | None,
    *,
    allow_network: bool = True,
) -> tuple[Any, Any, Any]:
    """Lazy-init the corpus EmbeddingCache + OpenAlex client when configured.

    Returns ``(embed_cache_or_None, openalex_client_or_None, cold_start_policy)``.
    A disabled provider contributes neutral defaults. Configured provider errors
    propagate. ``cold_start_policy`` is a :class:`ColdStartPrestigePolicy` derived
    from the prestige config (disabled when prestige is off), threaded to BOTH
    training and prediction so the ``prestige_score`` feature stays consistent.

    ``allow_network=False`` builds a CACHE-ONLY OpenAlex client (no network) for
    interactive request paths that must not block on a lookup — see
    :class:`OpenAlexClient`.
    """
    from zotero_summarizer.services.model.prestige import cold_start_policy_from_config

    embed_cache = None
    openalex_client = None
    cold_start_policy = cold_start_policy_from_config(
        getattr(goals_config, "prestige", None) if goals_config is not None else None
    )
    if goals_config is None:
        return embed_cache, openalex_client, cold_start_policy

    corpus_cfg = getattr(goals_config, "corpus", None)
    if corpus_cfg is not None and getattr(corpus_cfg, "enabled", False):
        embed_cache = _resolve_embedding_cache(corpus_db_path, corpus_cfg.embedding_model)

    prestige_cfg = getattr(goals_config, "prestige", None)
    if prestige_cfg is not None and getattr(prestige_cfg, "enabled", False):
        from zotero_summarizer.integrations.openalex import OpenAlexClient
        from zotero_summarizer.integrations.openalex_cache import OpenAlexCache

        cache = OpenAlexCache(corpus_db_path, ttl_seconds=int(prestige_cfg.cache_ttl_days) * 86400)
        mailto = (getattr(prestige_cfg, "user_agent_email", "") or "").strip() or None
        openalex_client = OpenAlexClient(cache, mailto=mailto, allow_network=allow_network)

    return embed_cache, openalex_client, cold_start_policy


def _compute_aux(
    embed_cache: Any,
    openalex_client: Any,
    *,
    title: str,
    abstract: str,
    doi: str,
    year: int | None,
    prestige_neutral: float = 3.0,
    stale_days: int = 30,
    cold_start_policy: Any | None = None,
) -> tuple[float, float]:
    """Return ``(corpus_affinity, prestige_score)`` for one paper.

    Disabled providers contribute 0.0 / 3.0 (neutral); provider failures propagate.
    """
    affinity, prestige, _ctx = _compute_aux_with_context(
        embed_cache, openalex_client,
        title=title, abstract=abstract, doi=doi, year=year,
        prestige_neutral=prestige_neutral, stale_days=stale_days,
        cold_start_policy=cold_start_policy,
    )
    return affinity, prestige


def _populate_work_context(
    ctx: dict[str, float | None], work: Any, prestige: float, cold_start_policy: Any | None
) -> None:
    """Fill ``ctx`` from an OpenAlex Work (early-returns keep the nesting shallow)."""
    max_author_h = getattr(work, "max_author_h_index", None)
    ctx["max_author_h_index"] = float(max_author_h) if max_author_h is not None else None
    ctx["venue_works_count"] = float(getattr(work, "venue_works_count", 0) or 0)
    ctx["cited_by_count"] = float(getattr(work, "cited_by_count", 0) or 0)
    pct = getattr(work, "citation_percentile", None)
    ctx["citation_percentile"] = float(pct) if pct is not None else None
    if pct is not None:
        return
    # Cold-start only (no percentile of its own): record the author field-percentile
    # and the provisional prestige the lift produced, so the badge can show an
    # author-based prior instead of "unknown". ``citation_percentile`` stays None →
    # the quality floor still treats the paper as UNKNOWN and never demotes it.
    afp = getattr(work, "max_author_field_percentile", None)
    if afp is None:
        return
    ctx["max_author_field_percentile"] = float(afp)
    if cold_start_policy is not None and getattr(cold_start_policy, "enabled", False):
        ctx["cold_start_prestige"] = prestige


def _compute_aux_with_context(
    embed_cache: Any,
    openalex_client: Any,
    *,
    title: str,
    abstract: str,
    doi: str,
    year: int | None,
    prestige_neutral: float = 3.0,
    stale_days: int = 30,
    cold_start_policy: Any | None = None,
) -> tuple[float, float, dict[str, float]]:
    """Same as :func:`_compute_aux` but also returns raw OpenAlex Work stats.

    The third element is an ``aux_context`` dict consumed by the review UI:

      ``max_author_h_index`` — highest h-index across all authors (int)
      ``venue_works_count``  — host journal/conference output count (int)
      ``cited_by_count``     — citations of THIS work to date (int)
      ``max_author_field_percentile`` — authors' field-normalized standing [0,1]
        (only populated at cold-start; the cold-start prior's input)
      ``cold_start_prestige`` — provisional author-based prestige [1,5] for a
        cold-start paper (None unless the lift fired), surfaced in the badge
      ``goal_sims`` — ``{research-goal text: cosine}`` for THIS candidate's
        title+abstract (None = no goals stored / corpus off). The goal-anchored
        signal the slate/queue blend orders by; NOT a model feature (kept out of
        the feature vector on purpose — the gate is engagement-trained and would
        re-weight it back toward "similar to what I've saved").

    Missing author h-index defaults to ``None``; zero is not useful evidence and
    often means OpenAlex supplied no resolvable author IDs. Count fields retain
    numeric zero.
    """
    affinity = 0.0
    prestige = float(prestige_neutral)
    ctx: dict[str, float | None] = {
        "max_author_h_index": None,
        "venue_works_count": 0.0,
        "cited_by_count": 0.0,
        # Field-normalized citation percentile [0,1] (None = unknown / cold-start).
        # The quality signal the Library prestige + floor use downstream.
        "citation_percentile": None,
        "max_author_field_percentile": None,
        "cold_start_prestige": None,
        "goal_sims": None,
    }
    if embed_cache is not None:
        affinity_f, goal_sims = embed_cache.affinity_and_goals(
            title, abstract, doi=doi, stale_days_for_weak_negative=stale_days
        )
        affinity = float(affinity_f)
        ctx["goal_sims"] = goal_sims
    if openalex_client is not None:
        from zotero_summarizer.services.model.prestige import lookup_prestige

        score, work = lookup_prestige(
            openalex_client,
            doi=doi or None,
            title=title,
            year=year,
            neutral=prestige_neutral,
            cold_start_policy=cold_start_policy,
        )
        prestige = float(score)
        if work is not None:
            _populate_work_context(ctx, work, prestige, cold_start_policy)
    return affinity, prestige, ctx


def _extra_features(
    row: dict[str, str],
    title: str,
    abstract: str,
    *,
    reference_year: int | None = None,
    corpus_affinity: float = 0.0,
    prestige_score: float = 3.0,
    nearest_kept_cosine: float = 0.0,
    positive_centroid_cosine: float = 0.0,
    recent_centroid_cosine: float = 0.0,
    topic_drift: float = 0.0,
    author_overlap_count: float = 0.0,
) -> np.ndarray:
    """Tabular features alongside the SPECTER2 embedding (12 dims).

    See ``classifier_const.N_EXTRA_FEATURES`` for the layout table.
    Indices 0-6 are content/provenance-based; 7-11 are personalised over
    the user's positive-engagement subset P (computed by
    :mod:`library_features`). Engagement-derived signals that ARE the
    labels (emoji tags, notes, annotations counts) are deliberately
    excluded from features to prevent leakage.
    """
    has_doi = 1.0 if (row.get("doi") or "").strip() else 0.0
    has_venue = 1.0 if (row.get("venue") or "").strip() else 0.0
    year_str = (row.get("year") or "").strip()
    if year_str[:4].isdigit():
        year = int(year_str[:4])
    else:
        year = 0
    if reference_year is None:
        reference_year = current_feature_year()
    recency = float(min(20, max(0, reference_year - year))) if year else 20.0
    title_log_len = float(np.log1p(len(title or "")))
    abstract_log_len = float(np.log1p(len(abstract or "")))
    return np.asarray(
        [
            has_doi, has_venue, recency, title_log_len, abstract_log_len,
            float(corpus_affinity), float(prestige_score),
            float(nearest_kept_cosine), float(positive_centroid_cosine),
            float(recent_centroid_cosine), float(topic_drift),
            float(author_overlap_count),
        ],
        dtype=np.float32,
    )


__all__ = [
    "_build_aux_providers",
    "_compute_aux",
    "_compute_aux_with_context",
    "_extra_features",
]
