"""Frozen result types for :mod:`services.daily_select`.

Kept tiny and dependency-free so the API-route subagent can import them
without pulling in sqlite or the rest of the service layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SlatePaper:
    """One card in the daily slate."""

    item_key: str
    item_id: int
    title: str
    authors: str
    venue: str
    role: str
    composite_score: float
    surprise_score: float
    corpus_affinity: float
    prestige_score: float
    rationale: str
    shap_top: list[dict[str, Any]]
    decision: str
    # Phase 1.18: top-author h-index, pulled from OpenAlex via
    # ``shap_contribs_json.aux_context.max_author_h_index``. ``None`` when
    # the paper has no OpenAlex match yet — the UI hides the badge.
    max_author_h_index: int | None = None
    # Provenance: the RSS feed this item came from (``processed_feed_items.
    # feed_name``). The bucket/role is carried by ``role`` above. Empty when
    # the row predates feed_name capture.
    feed_name: str = ""
    # Full-text peer-review QualityReview (services.quality_review), persisted on
    # the top-K picks. Empty ``{}`` when not in the reviewed set.
    quality: dict[str, Any] = field(default_factory=dict)
    # Feed item abstract and publication year, stored at triage time from Zotero
    # feedItems. Empty string / None for rows predating this column rollout.
    abstract: str = ""
    pub_year: int | None = None
    # Heuristic, no-LLM plain-language reason chips ("why it matters"), built by
    # ``_relevance.attach_why`` from signals already on the card (goal match,
    # library affinity, model relevance, author prestige, citations, surprise).
    # Empty list when no signal cleared a threshold; the UI hides the row then.
    why: list[str] = field(default_factory=list)
    # Goal-text similarity: max cosine of the paper's title+abstract to the
    # user's research-goal texts (``aux_context.goal_sims``). ``None`` =
    # unavailable (no goals / corpus off / not yet rescored). Feeds the slate's
    # rank blend (ordering) and is surfaced to the user through the goal
    # why-chips ("Strong goal match" / "On-topic for you"), keyed to it via
    # ``_relevance.attach_why``; the raw cosine is intentionally not shown as a
    # number (only meaningful pool-relative — the card uses anchored words, not
    # bare decimals). Carried on the ``/api/daily`` payload per card.
    goal_sim: float | None = None


@dataclass(frozen=True)
class DailySlate:
    """Result of :func:`assemble_daily_slate`.

    Ordering contract: papers arrive role-grouped (model picks first, ordered
    by the shared relevance×goal×prestige blend, then surprise/diversity), so
    cards are intentionally NOT in displayed-composite order — same disclosed
    behaviour as the Library queue's blended sort.
    """

    papers: list[SlatePaper]
    pool_size: int
    lookback_hours: int
    empty_role_events: list[str] = field(default_factory=list)
    # True when the lookback window was empty and the slate fell back to the
    # most-recent scored rows regardless of age (so Today is never blank
    # while fresh triage hasn't run). The UI shows a "showing older items" note.
    fellback_to_recent: bool = False
    # Honest "weak feed week" signals for the Today banner (the model role hides
    # ``dont_read``-band papers — see ``_allocation.MODEL_RELEVANCE_FLOOR``):
    #   * ``low_relevance_hidden`` — count of ``dont_read``-band candidates in the
    #     pool that no role surfaced (excluded as below the user's reading bar).
    #   * ``weak_slate`` — no candidate in the pool reached the ``should_read``
    #     band, i.e. nothing strongly matches; the UI nudges a fresh triage.
    low_relevance_hidden: int = 0
    weak_slate: bool = False


__all__ = ["SlatePaper", "DailySlate"]
