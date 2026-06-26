"""Phase 1.5: note v3 format — provenance comment, no <div>, no [ZS TRIAGE v2] heading."""
from __future__ import annotations

import re

import pytest

from zotero_summarizer.models import SummarizeResponse, TriageDimensions
from zotero_summarizer.services.zotero.pending import (
    NOTE_VERSION,
    SYSTEM_TAG_FEEDS_V3,
    build_provenance_comment,
    build_triage_note_html,
)


def _fake_summary(**overrides) -> SummarizeResponse:
    base = dict(
        title="Test paper",
        doi="10.0/test",
        summary="",
        relevance_score=4,
        composite_relevance_score=4.0,
        reading_priority="should_read",
        tags=["agents", "policy", "multimodal"],
        triage_rationale="Strong methodology with concrete benchmarks.",
        triage_confidence=0.8,
        executive_summary="",
        should_deep_read="",
        key_sections_to_read=[],
        relevance_to_research="Aligns with your multiagent autonomy goal.",
        controversial_points="",
        industry_academy_impact="",
        unknown_unknowns="",
        implementation_quickstart="",
        key_findings=["F1 = 91% on benchmark X", "Latency = 23ms"],
        methods="",
        limitations="",
        suggested_collections=[],
        corpus_affinity_score=0.4,
        matched_goal="agent autonomy",
        triage_dimensions=TriageDimensions(),
    )
    base.update(overrides)
    return SummarizeResponse(**base)


def test_provenance_comment_has_v3_version():
    cmt = build_provenance_comment(run_id="feeds_test_001")
    assert cmt.startswith("<!--")
    assert cmt.endswith("-->")
    assert "zs:note_type=triage" in cmt
    assert f"version={NOTE_VERSION}" in cmt
    assert "version=3" in cmt
    assert "run_id=feeds_test_001" in cmt
    assert "source=feed-batch" in cmt


def test_provenance_comment_sanitizes_injection_attempts():
    """A malicious run_id with `-->` shouldn't break the comment."""
    cmt = build_provenance_comment(run_id="bad-->run<!--")
    assert "-->" in cmt[-3:]  # only the closing tag at the end
    # No premature comment close in the middle
    assert cmt.count("-->") == 1


def test_note_v3_includes_provenance_comment_by_default():
    summary = _fake_summary()
    html = build_triage_note_html("My Paper", summary, run_id="feeds_x")
    assert html.startswith("<!--")
    assert "zs:note_type=triage;version=3" in html


def test_note_v3_omits_div_wrapper():
    """v3 explicitly drops the `<div class='zotero-note znv1'>` wrapper."""
    summary = _fake_summary()
    html = build_triage_note_html("P", summary)
    assert "<div" not in html
    assert "zotero-note znv1" not in html


def test_note_v3_omits_zs_triage_heading():
    """v3 drops the noisy `[ZS TRIAGE v2] 🔴 Article Analysis` heading."""
    summary = _fake_summary()
    html = build_triage_note_html("P", summary)
    assert "[ZS TRIAGE" not in html
    assert "Article Analysis" not in html


def test_note_v3_only_uses_zotero_safe_tags():
    """TinyMCE-safe: only <h2>, <p>, <ul>, <li>, <strong>, <em>. No <h1>, no <div>, no CSS."""
    summary = _fake_summary()
    html = build_triage_note_html("P", summary)
    # Strip the HTML comment first.
    body = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    safe_tags = {"h2", "p", "ul", "li", "strong", "em"}
    used = set(re.findall(r"<(\w+)", body))
    forbidden = used - safe_tags
    assert not forbidden, f"v3 note uses forbidden tags: {forbidden}"


def test_note_v3_has_three_sections():
    summary = _fake_summary()
    html = build_triage_note_html("Reproducibility paper", summary)
    body = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    # Exactly 3 <h2> headings (priority + key findings + relevance).
    h2_count = body.count("<h2>")
    assert h2_count == 3, f"expected 3 sections, got {h2_count}"


def test_note_v3_black_swan_footer_only_when_marked():
    summary = _fake_summary()
    html_normal = build_triage_note_html("P", summary, is_black_swan=False)
    html_swan = build_triage_note_html("P", summary, is_black_swan=True, surprise_score=0.65)
    assert "🦢" not in html_normal
    assert "🦢" in html_swan


def test_note_v3_provenance_can_be_disabled():
    """include_provenance=False is for testing or for the legacy library-triage path."""
    summary = _fake_summary()
    html = build_triage_note_html("P", summary, include_provenance=False)
    assert not html.startswith("<!--")


def test_provenance_tag_is_slash_prefixed():
    """`/zs/feeds-v3` MUST start with `/` so ZoteroWriter._ensure_tag uses type=1."""
    assert SYSTEM_TAG_FEEDS_V3.startswith("/")
    assert "zs" in SYSTEM_TAG_FEEDS_V3
    assert "v3" in SYSTEM_TAG_FEEDS_V3
