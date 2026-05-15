"""Phase 1.8: OpenAlex prestige scoring."""

from __future__ import annotations

from zotero_summarizer.integrations.openalex import OpenAlexWork
from zotero_summarizer.services.prestige import compute_prestige_score


def _work(h: int = 0, venue: int = 0, cites: int = 0) -> OpenAlexWork:
    return OpenAlexWork(
        openalex_id="W123",
        max_author_h_index=h,
        venue_works_count=venue,
        venue_display_name="Test Venue",
        cited_by_count=cites,
        is_oa=False,
        oa_url=None,
    )


def test_none_returns_neutral():
    assert compute_prestige_score(None) == 3.0
    assert compute_prestige_score(None, neutral=2.5) == 2.5


def test_zero_metrics_floor_to_one():
    """All-zero signals map to 1.0, not the neutral fallback."""
    assert compute_prestige_score(_work(h=0, venue=0, cites=0)) == 1.0


def test_h_index_outweighs_other_signals():
    """h-index has the highest weight: same magnitude lifts score more than venue/cites."""
    h_only = compute_prestige_score(_work(h=80, venue=0, cites=0))
    venue_only = compute_prestige_score(_work(h=0, venue=40_000, cites=0))
    cites_only = compute_prestige_score(_work(h=0, venue=0, cites=800))
    assert h_only > venue_only, f"h-index should outweigh venue: {h_only} vs {venue_only}"
    assert h_only > cites_only, f"h-index should outweigh cites: {h_only} vs {cites_only}"


def test_strong_paper_approaches_five():
    """High h-index + large venue + many citations → close to 5.0."""
    score = compute_prestige_score(_work(h=100, venue=50_000, cites=1_000))
    assert score >= 4.5, f"strong paper should be >=4.5, got {score}"


def test_monotonic_in_citations():
    """Citations contribute positively to the blend."""
    low = compute_prestige_score(_work(h=10, venue=100, cites=0))
    high = compute_prestige_score(_work(h=10, venue=100, cites=500))
    assert high > low
