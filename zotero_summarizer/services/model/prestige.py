"""Prestige scoring from OpenAlex signals.

Maps a normalized :class:`OpenAlexWork` into a single ``[1.0, 5.0]`` score
suitable for blending into :func:`zotero_summarizer.services.model.scoring.compute_composite_score`.

Design choices:

* **Field- AND year-normalized citation percentile** (OpenAlex
  ``citation_normalized_percentile``) is the signal — robust and comparable
  across fields and years, unlike raw citation counts, author h-index, or venue
  size (gameable, field-biased, and unfair to new work). SOTA per the Leiden
  Manifesto / OpenAlex guidance.
* **No record / no percentile yet** → neutral ``3.0`` (cold-start protection:
  never floor a young or uncited paper at 1.0).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from zotero_summarizer.integrations.openalex import OpenAlexClient, OpenAlexWork


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ColdStartPrestigePolicy:
    """Knobs for the cold-start author-reputation prior (see
    :func:`cold_start_author_score`). ``enabled=False`` (the default) reproduces
    the pre-existing behaviour exactly: cold-start → flat ``neutral``."""

    enabled: bool = False
    max_lift: float = 1.0
    gamma: float = 1.5


def cold_start_policy_from_config(prestige_cfg: object | None) -> ColdStartPrestigePolicy:
    """Build a :class:`ColdStartPrestigePolicy` from a ``PrestigeConfig`` (or
    None). Returns a disabled policy when prestige is off / unconfigured, so the
    lift is inert unless explicitly enabled."""
    if prestige_cfg is None or not getattr(prestige_cfg, "enabled", False):
        return ColdStartPrestigePolicy()
    return ColdStartPrestigePolicy(
        enabled=bool(getattr(prestige_cfg, "cold_start_author_lift", True)),
        max_lift=float(getattr(prestige_cfg, "cold_start_max_lift", 1.0)),
        gamma=float(getattr(prestige_cfg, "cold_start_gamma", 1.5)),
    )


def percentile_to_score(percentile: float | None, *, neutral: float = 3.0) -> float:
    """Map a field-normalized citation percentile ∈ [0, 1] to [1.0, 5.0].

    Linear ``1 + 4·p`` — no tuned blend weights. ``None`` (no percentile yet:
    too new / uncited) → ``neutral``: cold-start work is never penalised. This is
    the single source of the percentile→score mapping, shared by the gate
    feature (:func:`compute_prestige_score`) and the Library prestige floor
    (``reading_queue.scoring_from_prediction``)."""
    if percentile is None:
        return neutral
    pct = min(1.0, max(0.0, float(percentile)))
    return round(1.0 + 4.0 * pct, 2)


def cold_start_author_score(
    author_field_percentile: float | None,
    *,
    neutral: float = 3.0,
    max_lift: float = 1.0,
    gamma: float = 1.5,
) -> float:
    """Cold-start author-reputation PRIOR → [neutral, neutral + max_lift].

    Used ONLY when a paper has no field-normalized citation percentile of its
    own yet (too new / uncited), so citation prestige is structurally
    unavailable. The signal is the author's FIELD-normalized standing
    (``OpenAlexWork.max_author_field_percentile`` ∈ [0,1] — the median of the
    author's works' field+year-normalized percentile), NOT raw h-index / venue /
    citation counts: those are field- and career-biased (Leiden Manifesto
    principle 6) and gameable. This reuses the SAME normalized percentile the
    work-level prestige trusts, extended to the author.

    Three deliberate properties (all grounded in cold-start-fairness +
    Matthew-effect literature):

    * **lift-only** — never returns below ``neutral``; an unknown/junior author
      is never a penalty (missing evidence is never punished, mirroring the
      quality floor's "unknown → don't demote" rule);
    * **capped** — bounded by ``max_lift`` so a superstar coauthor cannot
      dominate (dosage control against rich-get-richer);
    * **convex in p** (``p ** gamma``, gamma ≥ 1) — only genuinely high-standing
      authors approach the cap; mid-tier authors get little lift, avoiding the
      "false precision" the Leiden Manifesto (principle 8) warns against.

    ``None`` percentile (author too new / no normalized works) → ``neutral``.
    """
    if author_field_percentile is None or max_lift <= 0:
        return neutral
    p = min(1.0, max(0.0, float(author_field_percentile)))
    g = max(1.0, float(gamma))
    return round(neutral + max_lift * (p ** g), 2)


def compute_prestige_score(
    work: OpenAlexWork | None,
    *,
    neutral: float = 3.0,
    cold_start_policy: ColdStartPrestigePolicy | None = None,
) -> float:
    """Map a paper's OpenAlex prestige signals to [1.0, 5.0].

    Established work → field+year-normalized ``citation_normalized_percentile``.
    A paper with no percentile yet (too new / uncited) is cold-start: when
    ``cold_start_policy`` is enabled it falls back to the asymmetric, capped
    author-reputation prior (:func:`cold_start_author_score`); otherwise it
    returns ``neutral`` (the historical behaviour). The work being missing
    entirely (no OpenAlex record) always returns ``neutral``."""
    if work is None:
        return neutral
    if work.citation_percentile is not None:
        return percentile_to_score(work.citation_percentile, neutral=neutral)
    if cold_start_policy is not None and cold_start_policy.enabled:
        return cold_start_author_score(
            work.max_author_field_percentile,
            neutral=neutral,
            max_lift=cold_start_policy.max_lift,
            gamma=cold_start_policy.gamma,
        )
    return neutral


def lookup_prestige(
    client: OpenAlexClient | None,
    *,
    doi: str | None,
    title: str | None = None,
    year: int | None = None,
    require_doi: bool = False,
    neutral: float = 3.0,
    cold_start_policy: ColdStartPrestigePolicy | None = None,
) -> tuple[float, OpenAlexWork | None]:
    """Best-effort prestige lookup with safe failure modes.

    Returns ``(prestige_score, work_or_none)``. Falls back to ``neutral`` when
    no client is configured, lookups fail, or the resulting work payload is
    unusable. ``cold_start_policy`` (when enabled) turns on the author-reputation
    prior for papers with no percentile yet. Never raises — prestige must not
    block triage.
    """
    if client is None:
        return neutral, None
    work: OpenAlexWork | None = None
    if doi and doi.strip():
        try:
            work = client.fetch_work_by_doi(doi.strip())
        except Exception as exc:  # pragma: no cover — defensive
            LOGGER.debug("prestige: DOI lookup failed: %s", exc)
    if work is None and not require_doi and title:
        try:
            work = client.fetch_work_by_title(title, year=year)
        except Exception as exc:  # pragma: no cover — defensive
            LOGGER.debug("prestige: title lookup failed: %s", exc)
    score = compute_prestige_score(work, neutral=neutral, cold_start_policy=cold_start_policy)
    return score, work
