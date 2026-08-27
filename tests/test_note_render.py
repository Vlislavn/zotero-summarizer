"""Tests for the self-sufficient Zotero note template."""
from __future__ import annotations

import json
import re

from zotero_summarizer.models import SummarizeResponse, TriageDimensions
from zotero_summarizer.services.triage.feeds._daily_materialize import (
    _materialize_summary,
    _PendingScoredRow,
)
from zotero_summarizer.services.zotero.pending import build_provenance_comment, build_triage_note_html


def _summary(**overrides) -> SummarizeResponse:
    base = {
        "executive_summary": "Paper introduces approach X.",
        "should_deep_read": "Yes.",
        "key_sections_to_read": ["Section 3", "Table 4"],
        "relevance_to_research": "Directly maps to multiagent goals.",
        "controversial_points": "The comparison baseline may be weak.",
        "industry_academy_impact": "Could reduce inference cost.",
        "unknown_unknowns": "External validity remains unknown.",
        "implementation_quickstart": "Start from the released checkpoint.",
        "key_findings": [
            "Reaches 87% F1 on benchmark B.",
            "5x speedup over baseline.",
            "Releases dataset of 10k items.",
        ],
        "methods": "A blinded benchmark comparison.",
        "limitations": "Single-centre evaluation.",
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


def test_note_provenance_is_v3_and_comment_safe():
    comment = build_provenance_comment(run_id="bad-->run<!--")
    assert comment.count("-->") == 1 and "zs:note_type=triage;version=3" in comment
    assert build_triage_note_html("T", _summary()).startswith("<!--")
    assert not build_triage_note_html("T", _summary(), include_provenance=False).startswith("<!--")


def test_note_renders_available_decision_sections():
    html = build_triage_note_html("Test paper", _summary())
    section_headers = re.findall(r"<h2>([^<]+)</h2>", html)
    assert any("Should read" in h or "Read" in h for h in section_headers)
    assert any("Key findings" in h for h in section_headers)
    for expected in ("What this paper", "Approach", "Why it matters", "Limitations", "What to read"):
        assert any(expected in heading for heading in section_headers)


def test_note_preserves_core_decision_artifact():
    html = build_triage_note_html("Title", _summary())
    for expected in ("blinded benchmark", "Single-centre", "multiagent goals"):
        assert expected in html


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
    fallback = build_triage_note_html("<b>Title</b>", _summary(triage_rationale="", should_deep_read="", executive_summary=""))
    assert "<b>Title</b>" not in fallback and "&lt;b&gt;Title&lt;/b&gt;" in fallback


def test_note_caps_findings_at_six():
    s = _summary(
        key_findings=["one", "two", "three", "four", "five", "six", "seven"],
    )
    html = build_triage_note_html("T", s)
    li_count = html.count("<li>")
    assert li_count == 8  # six findings + two reading sections
    assert "six" in html
    assert "seven" not in html


def test_delayed_materialization_restores_summary_and_marks_legacy_fallback():
    summary = _summary(methods="Persisted method after restart.")
    row = {"shap_contribs_json": json.dumps({"summary": summary.model_dump()})}
    pick = _PendingScoredRow(4.2, 0.0, False, row, "paper")
    restored, source = _materialize_summary(pick)
    assert source == "persisted_summary"
    assert restored.methods == "Persisted method after restart."

    pick.row = {"title": "Legacy", "reading_priority": "could_read", "composite_score": 1}
    restored, source = _materialize_summary(pick)
    assert source == "legacy_sparse"
    assert restored.executive_summary == ""
