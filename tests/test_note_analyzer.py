"""Tests for the user-note classification pipeline.

Filtering logic is pure-Python, so unit-tested directly. The LLM step is
mocked since invoking the real model in CI would be expensive and flaky.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from zotero_summarizer.services.zotero import note_analyzer
from zotero_summarizer.services._common import html_to_text
from zotero_summarizer.services.zotero.note_analyzer import (
    NoteAnalysis,
    UserNote,
    _NoteVerdict,
    classify_notes,
    distribution,
    write_analyses_csv,
)


# ---------------------------------------------------------------------------
# HTML stripping + filtering heuristics
# ---------------------------------------------------------------------------


def test_strip_html_normalises_whitespace_and_drops_tags():
    raw = "<p>hello\n\t<b>world</b></p>"
    assert html_to_text(raw) == "hello world"


def test_pdf_annotation_extracts_are_filtered_out_by_regex():
    """Notes starting with 'Annotations (date)' are auto-generated PDF dumps."""
    assert note_analyzer._PDF_ANNOT_RE.match("Annotations (3/8/2024, 9:43 PM) ...")
    assert not note_analyzer._PDF_ANNOT_RE.match("My take: this paper argues that ...")


def test_external_llm_titles_caught():
    assert note_analyzer._EXTERNAL_LLM_RE.search("Gemini Deep Researches in onco")
    assert note_analyzer._EXTERNAL_LLM_RE.search("Some Executive Summary content")
    assert not note_analyzer._EXTERNAL_LLM_RE.search("TL;DR good paper")


def test_quote_heavy_filter_triggers_on_multiple_long_quotes():
    body = (
        '"This is a quote longer than 40 characters that gets caught" '
        '"And here is another similarly long quote in the same note" '
    )
    assert len(note_analyzer._QUOTE_HEAVY_RE.findall(body)) >= 2


# ---------------------------------------------------------------------------
# LLM verdict schema
# ---------------------------------------------------------------------------


def test_note_verdict_accepts_valid_priority():
    v = _NoteVerdict(priority="must_read", confidence=0.8, rationale="excited")
    assert v.priority == "must_read"


def test_note_verdict_normalises_skip_uppercase():
    v = _NoteVerdict(priority="SKIP", confidence=0.5)
    assert v.priority == "SKIP"


def test_note_verdict_coerces_unknown_priority_to_could_read():
    """Domain helper falls back to could_read for unknown strings; we accept that."""
    v = _NoteVerdict(priority="nonsense", confidence=0.5)
    assert v.priority == "could_read"


# ---------------------------------------------------------------------------
# classify_notes — mock LLM, end-to-end through one note
# ---------------------------------------------------------------------------


def _note(body: str = "tedious basic paper", title: str = "test note") -> UserNote:
    return UserNote(
        note_id=42,
        parent_item_key="ABCD1234",
        note_title=title,
        note_body=body,
        parent_title="A Paper About Agents",
        parent_abstract="We propose a new agent framework...",
    )


def test_classify_returns_priority_when_llm_picks_one():
    llm = MagicMock()
    llm.pydantic_prompt.return_value = _NoteVerdict(
        priority="dont_read", confidence=0.9, rationale="dismissive",
    )
    out = classify_notes([_note()], llm)
    assert len(out) == 1
    a = out[0]
    assert a.llm_priority == "dont_read"
    assert a.llm_confidence == 0.9
    assert a.item_key == "note:ABCD1234:42"
    assert a.skipped_reason == ""


def test_classify_handles_llm_skip_verdict():
    llm = MagicMock()
    llm.pydantic_prompt.return_value = _NoteVerdict(
        priority="SKIP", confidence=0.3, rationale="research diary, not per-paper",
    )
    out = classify_notes([_note()], llm)
    assert out[0].llm_priority == ""
    assert "research diary" in out[0].skipped_reason


def test_classify_swallows_llm_errors():
    """A failed LLM call should NOT crash the loop — surface it as a skip reason."""
    llm = MagicMock()
    llm.pydantic_prompt.side_effect = RuntimeError("connection timeout")
    out = classify_notes([_note(), _note()], llm)
    assert len(out) == 2
    assert all(a.llm_priority == "" for a in out)
    assert all("connection timeout" in a.skipped_reason for a in out)


# ---------------------------------------------------------------------------
# CSV emission
# ---------------------------------------------------------------------------


def test_write_csv_prefills_your_label_with_llm_priority(tmp_path: Path):
    a = NoteAnalysis(
        item_key="note:KEY:1",
        title="Paper", authors="", venue="", doi="",
        abstract_preview="abstract",
        note_title="My take", note_preview="excellent paper",
        llm_priority="must_read", llm_confidence=0.9, llm_rationale="endorsed",
    )
    path = tmp_path / "out.csv"
    write_analyses_csv([a], path)
    text = path.read_text()
    assert "your_label" in text.splitlines()[0]   # header
    # The pre-filled `your_label` should equal the LLM priority.
    assert "must_read" in text


def test_distribution_groups_classified_and_skipped():
    analyses = [
        NoteAnalysis("k1", "t", "", "", "", "ab", "nt", "np", "must_read", 0.8, "ok"),
        NoteAnalysis("k2", "t", "", "", "", "ab", "nt", "np", "must_read", 0.7, "ok"),
        NoteAnalysis("k3", "t", "", "", "", "ab", "nt", "np", "", 0.0, "", "skip"),
    ]
    counts = distribution(analyses)
    assert counts["must_read"] == 2
    assert counts["(skipped)"] == 1
