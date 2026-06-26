"""OpenAlex prestige scoring — field-normalized citation percentile → [1, 5].

The prestige signal is OpenAlex ``citation_normalized_percentile`` (field- AND
year-normalized), NOT the old gameable h-index/venue/raw-citation blend. These
tests pin the new mapping and the cold-start guarantee (no record OR no
percentile yet → neutral, never floored to 1.0)."""

from __future__ import annotations

import pytest

from zotero_summarizer.integrations.openalex import OpenAlexWork
from zotero_summarizer.services.model.prestige import (
    ColdStartPrestigePolicy,
    cold_start_author_score,
    cold_start_policy_from_config,
    compute_prestige_score,
    percentile_to_score,
)


def _work(
    *,
    percentile: float | None = None,
    h: int = 0,
    venue: int = 0,
    cites: int = 0,
    author_field_percentile: float | None = None,
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
        max_author_field_percentile=author_field_percentile,
    )


_ON = ColdStartPrestigePolicy(enabled=True, max_lift=1.0, gamma=1.5)


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


# ----------------------------------------------------- cold_start_author_score


def test_cold_start_lift_is_one_directional():
    """No author signal (None) → exactly neutral; never below. An unknown/junior
    author is never penalised (mirrors the quality floor's 'unknown→keep')."""
    assert cold_start_author_score(None) == 3.0
    assert cold_start_author_score(None, neutral=2.5) == 2.5
    # The lift is strictly >= neutral for any real percentile.
    for p in (0.0, 0.01, 0.3, 0.6, 0.99, 1.0):
        assert cold_start_author_score(p) >= 3.0


def test_cold_start_lift_is_capped():
    """A top-standing author (p=1) reaches exactly neutral + max_lift, never more
    — bounded dose against the Matthew effect."""
    assert cold_start_author_score(1.0, neutral=3.0, max_lift=1.0) == 4.0
    assert cold_start_author_score(1.0, neutral=3.0, max_lift=0.5) == 3.5
    # Out-of-range percentile is clamped, so the cap holds.
    assert cold_start_author_score(1.9, neutral=3.0, max_lift=1.0) == 4.0


def test_cold_start_lift_is_convex_in_p():
    """gamma > 1 ⇒ mid-tier authors get LITTLE lift; only top authors approach the
    cap (avoids 'false precision', Leiden principle 8)."""
    mid = cold_start_author_score(0.5, gamma=1.5) - 3.0   # 0.5**1.5 ≈ 0.354
    top = cold_start_author_score(0.9, gamma=1.5) - 3.0   # 0.9**1.5 ≈ 0.854
    assert mid < 0.5 * 1.0          # mid-tier lift is sub-linear
    assert top > 2 * mid           # convex: top pulls away from mid


def test_cold_start_lift_disabled_via_max_lift_zero():
    assert cold_start_author_score(1.0, max_lift=0.0) == 3.0


# ----------------------------- compute_prestige_score with a cold-start policy


def test_cold_start_policy_off_keeps_neutral():
    """Default (no policy) reproduces the historical behaviour: cold-start with a
    strong author still scores neutral — author signal is inert unless enabled."""
    assert compute_prestige_score(_work(percentile=None, author_field_percentile=0.95)) == 3.0


def test_cold_start_policy_on_lifts_with_author_percentile():
    """With the policy enabled, a cold-start paper from a top-standing author is
    lifted above neutral (but only via the FIELD-NORMALIZED author percentile)."""
    score = compute_prestige_score(
        _work(percentile=None, author_field_percentile=0.95), cold_start_policy=_ON
    )
    assert score == cold_start_author_score(0.95, max_lift=1.0, gamma=1.5)
    assert 3.0 < score <= 4.0


def test_cold_start_policy_on_but_no_author_signal_stays_neutral():
    assert compute_prestige_score(
        _work(percentile=None, author_field_percentile=None), cold_start_policy=_ON
    ) == 3.0


def test_cold_start_does_not_touch_established_work():
    """A paper WITH its own percentile ignores the author prior entirely — the
    prior decays to zero the moment real citation signal exists (no double-count)."""
    with_author = compute_prestige_score(
        _work(percentile=0.4, author_field_percentile=0.99), cold_start_policy=_ON
    )
    assert with_author == percentile_to_score(0.4)  # author pct ignored


def test_cold_start_still_ignores_raw_h_index():
    """Raw h-index is NOT the lever even at cold-start (Leiden #6: field-biased).
    Only the field-normalized author percentile lifts."""
    assert compute_prestige_score(
        _work(percentile=None, h=200, author_field_percentile=None), cold_start_policy=_ON
    ) == 3.0


# --------------------------------------------------- cold_start_policy_from_config


class _Cfg:
    def __init__(self, **kw):
        self.enabled = kw.get("enabled", True)
        self.cold_start_author_lift = kw.get("cold_start_author_lift", True)
        self.cold_start_max_lift = kw.get("cold_start_max_lift", 1.0)
        self.cold_start_gamma = kw.get("cold_start_gamma", 1.5)


def test_policy_from_config_none_is_disabled():
    assert cold_start_policy_from_config(None).enabled is False


def test_policy_from_config_prestige_disabled_is_disabled():
    assert cold_start_policy_from_config(_Cfg(enabled=False)).enabled is False


def test_policy_from_config_reads_knobs():
    pol = cold_start_policy_from_config(_Cfg(cold_start_max_lift=0.5, cold_start_gamma=2.0))
    assert (pol.enabled, pol.max_lift, pol.gamma) == (True, 0.5, 2.0)


def test_policy_from_config_respects_lift_toggle():
    assert cold_start_policy_from_config(_Cfg(cold_start_author_lift=False)).enabled is False
