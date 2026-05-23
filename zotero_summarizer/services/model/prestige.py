"""Prestige scoring from OpenAlex signals.

Maps a normalized :class:`OpenAlexWork` into a single ``[1.0, 5.0]`` score
suitable for blending into :func:`zotero_summarizer.services.model.scoring.compute_composite_score`.

Design choices:

* **Log scale** for h-index, venue size, and citations — open-ended counts
  with long tails. ``log1p`` keeps zero stable.
* **No author** → neutral ``3.0`` (don't punish unknown).
* **Weights** sum to 1.0 across the three sub-signals; an h-index of 0 with
  unknown venue/citations still maps to 1.0 (the floor), not 3.0.
"""

from __future__ import annotations

import logging
import math

from zotero_summarizer.integrations.openalex import OpenAlexClient, OpenAlexWork


LOGGER = logging.getLogger(__name__)


# Reference ceilings — values above these saturate to 1.0 in the unit-mapped term.
_H_INDEX_REF = 100.0
_VENUE_WORKS_REF = 50_000.0
_CITES_REF = 1_000.0

# Sub-weights (must sum to 1.0).
_W_H = 0.50
_W_VENUE = 0.30
_W_CITES = 0.20


def compute_prestige_score(work: OpenAlexWork | None, *, neutral: float = 3.0) -> float:
    """Map OpenAlex signals to [1.0, 5.0].

    Returns ``neutral`` (default 3.0) when ``work`` is None — i.e., OpenAlex
    had no record. A work with all-zero metrics maps to 1.0, not neutral.
    """
    if work is None:
        return neutral
    h = _log_ratio(work.max_author_h_index, _H_INDEX_REF)
    venue = _log_ratio(work.venue_works_count, _VENUE_WORKS_REF)
    cites = _log_ratio(work.cited_by_count, _CITES_REF)
    blend = _W_H * h + _W_VENUE * venue + _W_CITES * cites
    return round(1.0 + 4.0 * blend, 2)


def _log_ratio(value: int, reference: float) -> float:
    """Log-scaled value in [0, 1]."""
    if value <= 0:
        return 0.0
    return min(1.0, math.log1p(float(value)) / math.log1p(reference))


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
