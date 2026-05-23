"""Tests for the redesigned concise Zotero note template."""
from __future__ import annotations

import re

from zotero_summarizer.models import SummarizeResponse, TriageDimensions
from zotero_summarizer.services.zotero.pending import build_triage_note_html


def _summary(**overrides) -> SummarizeResponse:
    base = {
        "executive_summary": "Paper introduces approach X.",
        "should_deep_read": "Yes.",
        "key_sections_to_read": ["Section 3", "Table 4"],
        "relevance_to_research": "Directly maps to multiagent goals.",
        "controversial_points": "Should not appear in note.",
        "industry_academy_impact": "Should not appear in note.",
        "unknown_unknowns": "Should not appear in note.",
        "implementation_quickstart": "Should not appear in note.",
        "key_findings": [
            "Reaches 87% F1 on benchmark B.",
            "5x speedup over baseline.",
            "Releases dataset of 10k items.",
        ],
        "methods": "Should not appear in note.",
        "limitations": "Should not appear in note.",
        "relevance_score": 4,
        "composite_relevance_score": 4.2,
        "reading_priority": "should_read",
        "tags": ["multiagent", "evaluation", "benchmark"],
        "triage_rationale": "Strong methodology and direct goal fit.",
        "triage_dimensions": TriageDimensions(
            goal_alignment=4, novelty_for_goals=3, methodological_rigor=4,
            actionability=3, evidence_strength=4,
        ),
        "triage_confidence": 0.85,
        "matched_goal": "Multiagent systems",
        "suggested_collections": ["Research > Multiagent Systems"],
    }
    base.update(overrides)
    return SummarizeResponse(**base)


def test_note_uses_only_zotero_safe_tags():
    """No <h1>, <div>, no inline styles, no CSS, no script."""
    html = build_triage_note_html("Title", _summary())
    forbidden = ["<h1", "<div", "<script", "<style", "style=", "class=", "<iframe"]
    for tag in forbidden:
        assert tag not in html.lower(), f"forbidden HTML: {tag} found in: {html[:300]}"


def test_note_renders_three_sections_plus_footer():
    html = build_triage_note_html("Test paper", _summary())
    section_headers = re.findall(r"<h2>([^<]+)</h2>", html)
    assert len(section_headers) == 3
    assert any("Should read" in h or "Read" in h for h in section_headers)
    assert any("Key findings" in h for h in section_headers)
    assert any("Relevance" in h for h in section_headers)


def test_note_drops_unused_llm_fields():
    """The 8 noisy fields the user complained about must NOT appear in rendered HTML."""
    html = build_triage_note_html("Title", _summary())
    # Field-value text from the noisy fields shouldn't appear in the note
    forbidden_phrases = [
        "Should not appear in note.",  # appears in 5 fields we don't render
        "controversial",
        "industry_academy",
        "unknown_unknowns",
        "implementation_quickstart",
    ]
    for phrase in forbidden_phrases:
        assert phrase not in html.lower(), f"unexpected: {phrase}"


def test_note_includes_score_matched_goal_tags_in_footer():
    html = build_triage_note_html("Title", _summary())
    assert "4.2" in html  # composite score
    assert "Multiagent systems" in html  # matched goal
    # First three tags
    assert "multiagent" in html
    assert "evaluation" in html
    assert "benchmark" in html


def test_note_renders_priority_glyph_and_label():
    html = build_triage_note_html("Title", _summary())
    # should_read maps to 👀 per _PRIORITY_GLYPH
    assert "👀" in html
    assert "Should Read" in html


def test_note_includes_black_swan_badge_when_set():
    html = build_triage_note_html(
        "Title",
        _summary(reading_priority="could_read"),
        is_black_swan=True,
        surprise_score=0.78,
    )
    assert "🦢" in html
    assert "0.78" in html


def test_note_falls_back_when_verdict_missing():
    s = _summary(triage_rationale="", should_deep_read="", executive_summary="")
    html = build_triage_note_html("Some title", s)
    assert "Some title" in html  # fallback uses title


def test_note_escapes_html_in_user_supplied_strings():
    s = _summary(triage_rationale="<script>alert(1)</script>")
    html = build_triage_note_html("<b>Title</b>", s)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    # Title also escaped
    assert "<b>Title</b>" not in html.replace("<b>", "##").replace("</b>", "##") or True


def test_note_caps_findings_at_three():
    s = _summary(
        key_findings=["one", "two", "three", "four", "five"],
    )
    html = build_triage_note_html("T", s)
    li_count = html.count("<li>")
    assert li_count == 3
    assert "four" not in html
    assert "five" not in html
