"""Budget-aware review-text selection: the guard that keeps short papers
byte-identical, equal-share water-filling that stops an early long section from
starving the referee-critical tail (Limitations/Conclusion), and the chunk-fill
fallback when no sections are parsed."""
from __future__ import annotations

from zotero_summarizer.services.library import _review_text as rt


def test_short_paper_is_byte_identical_to_old_truncation():
    # The common case (paper shorter than the cap): selection must be a no-op so
    # the digest/rubric prompt is unchanged vs the old full_text[:budget] slice.
    text = "Abstract. " * 50
    assert len(text) < 60_000
    assert rt.select_review_text([], text, budget=60_000) == text
    # Section input doesn't change the identity guarantee when it already fits.
    secs = [{"title": "Methods", "text": text}]
    assert rt.select_review_text(secs, text, budget=60_000) == text


def test_zero_budget_returns_empty():
    assert rt.select_review_text([{"title": "Methods", "text": "x"}], "x" * 100, budget=0) == ""


def test_early_long_section_does_not_starve_the_critical_tail():
    # Regression: a 30k Introduction must NOT eat the whole budget and drop
    # Limitations/Conclusion — the exact tail-loss the selector exists to fix.
    secs = [
        {"title": "Introduction", "text": "intro filler words " * 2000},
        {"title": "Methods", "text": "method detail words " * 1500},
        {"title": "Limitations", "text": "LIMIT_MARK " * 40},
        {"title": "Conclusion", "text": "CONCL_MARK " * 40},
    ]
    full = " ".join(s["text"] for s in secs)
    assert len(full) > 6_000  # over budget → selection engages
    out = rt.select_review_text(secs, full, budget=6_000)
    assert "LIMIT_MARK" in out and "CONCL_MARK" in out  # tail survives
    assert "method detail" in out                        # methods survive
    assert len(out) <= 6_100                              # respects budget (+ join slack)


def test_references_section_is_skipped():
    secs = [
        {"title": "Methods", "text": "METHOD_BODY " * 200},
        {"title": "References", "text": "REF_NOISE " * 5000},
    ]
    full = " ".join(s["text"] for s in secs)
    out = rt.select_review_text(secs, full, budget=3_000)
    assert "METHOD_BODY" in out and "REF_NOISE" not in out


def test_no_sections_falls_back_to_ranked_chunks_within_budget():
    # No parsed sections → chunk-ranking over the whole text, never exceeding budget.
    long = ("Methodology and evaluation. " * 4000) + ("Limitations and conclusion. " * 1000)
    out = rt.select_review_text([], long, budget=4_000)
    assert 0 < len(out) <= 4_300
    assert len(out) < len(long)  # genuinely selected a subset


def test_over_cap_paper_fills_the_budget_not_a_sliver():
    # Regression: _fill_from_chunks once capped candidates at 4 queries x 6 = 24
    # chunks, so a paper only slightly over budget kept ~40% — WORSE than the old
    # prefix truncation it replaced. It must now FILL the budget with the most
    # relevant chunks (overlap-safe, never exceeding the cap).
    para = "This section discusses methodology, results, evaluation and limitations in detail. "
    paper = para * 820  # ~65k chars, just over a 60k cap
    out = rt.select_review_text([], paper, budget=60_000)
    assert len(out) <= 60_000             # overlap-safe: never exceeds the budget
    assert len(out) >= 0.8 * 60_000       # genuinely fills it, not a 24-chunk sliver


def test_water_fill_gives_short_chunks_whole_and_caps_long_ones():
    # 3 chunks, budget 1000: short ones taken whole, the slack redistributed so the
    # long one is capped — every chunk gets >= floor(budget/n) and the sum fits.
    allocs = rt._water_fill(["x" * 100, "y" * 50_000, "z" * 30], 1_000)
    assert allocs[0] == 100 and allocs[2] == 30          # short → whole
    assert allocs[1] == 1_000 - 100 - 30                 # long → remaining slack
    assert sum(allocs) <= 1_000


def test_water_fill_empty_and_zero_budget():
    assert rt._water_fill([], 1000) == []
    assert rt._water_fill(["abc"], 0) == [0]
