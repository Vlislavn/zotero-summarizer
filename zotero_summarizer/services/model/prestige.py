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

from zotero_summarizer.integrations.openalex import OpenAlexClient, OpenAlexWork


LOGGER = logging.getLogger(__name__)


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


def compute_prestige_score(work: OpenAlexWork | None, *, neutral: float = 3.0) -> float:
    """Map a paper's OpenAlex ``citation_normalized_percentile`` to [1.0, 5.0].

    Returns ``neutral`` (default 3.0) when the work is missing (no OpenAlex
    record) OR has no percentile yet (too new / uncited)."""
    if work is None:
        return neutral
    return percentile_to_score(work.citation_percentile, neutral=neutral)


def lookup_prestige(
    client: OpenAlexClient | None,
    *,
    doi: str | None,
    title: str | None = None,
    year: int | None = None,
    require_doi: bool = False,
    neutral: float = 3.0,
) -> tuple[float, OpenAlexWork | None]:
    """Best-effort prestige lookup with safe failure modes.

    Returns ``(prestige_score, work_or_none)``. Falls back to ``neutral`` when
    no client is configured, lookups fail, or the resulting work payload is
    unusable. Never raises — prestige must not block triage.
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
    return compute_prestige_score(work, neutral=neutral), work
