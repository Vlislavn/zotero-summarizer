"""OpenAlex prestige scoring — field-normalized citation percentile → [1, 5].

The prestige signal is OpenAlex ``citation_normalized_percentile`` (field- AND
year-normalized), NOT the old gameable h-index/venue/raw-citation blend. These
tests pin the new mapping and the cold-start guarantee (no record OR no
percentile yet → neutral, never floored to 1.0)."""

from __future__ import annotations

import pytest

from zotero_summarizer.integrations.openalex import OpenAlexWork
from zotero_summarizer.services.model.prestige import (
    compute_prestige_score,
    percentile_to_score,
)


def _work(
    *,
    percentile: float | None = None,
    h: int = 0,
    venue: int = 0,
    cites: int = 0,
) -> OpenAlexWork:
    return OpenAlexWork(
        openalex_id="W123",
        max_author_h_index=h,
        venue_works_count=venue,
        venue_display_name="Test Venue",
        cited_by_count=cites,
        is_oa=False,
        oa_url=None,
        citation_percentile=percentile,
    )


# --------------------------------------------------------------- percentile_to_score


@pytest.mark.parametrize(
    "pct,expected",
    [(0.0, 1.0), (0.25, 2.0), (0.5, 3.0), (0.75, 4.0), (1.0, 5.0)],
)
def test_percentile_maps_linearly(pct: float, expected: float):
    """Linear 1 + 4·p across the whole [0,1] range — no tuned blend weights."""
    assert percentile_to_score(pct) == expected


def test_percentile_none_is_neutral():
    """Cold-start / uncited (no percentile) → neutral, never 1.0."""
    assert percentile_to_score(None) == 3.0
    assert percentile_to_score(None, neutral=2.5) == 2.5


def test_percentile_clamped_out_of_range():
    """Defensive clamp so a malformed payload can't escape [1,5]."""
    assert percentile_to_score(1.7) == 5.0
    assert percentile_to_score(-0.3) == 1.0


def test_percentile_monotonic():
    lo = percentile_to_score(0.2)
    hi = percentile_to_score(0.8)
    assert hi > lo


# ------------------------------------------------------------- compute_prestige_score


def test_none_work_returns_neutral():
    assert compute_prestige_score(None) == 3.0
    assert compute_prestige_score(None, neutral=2.5) == 2.5


def test_cold_start_work_returns_neutral():
    """A real OpenAlex record with no percentile yet (too new / uncited) is
    NOT penalised — it floors to neutral, fixing the old floor=1.0 bug."""
    assert compute_prestige_score(_work(percentile=None)) == 3.0


def test_work_uses_percentile():
    assert compute_prestige_score(_work(percentile=0.9)) == pytest.approx(4.6)
    assert compute_prestige_score(_work(percentile=0.0)) == 1.0


def test_score_ignores_gameable_signals():
    """A huge h-index / venue / citation count does NOT move the score: only the
    field-normalized percentile does. This is the whole point of the upgrade —
    the metrics are no longer gameable."""
    base = compute_prestige_score(_work(percentile=0.5, h=0, venue=0, cites=0))
    gamed = compute_prestige_score(
        _work(percentile=0.5, h=200, venue=100_000, cites=50_000)
    )
    assert base == gamed == 3.0
