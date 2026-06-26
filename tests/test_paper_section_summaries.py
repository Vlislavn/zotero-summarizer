"""Per-section 'what it covers' one-liners: index→id mapping, body-text gating,
empty/out-of-range dropping. The LLM is stubbed (the real call needs a model)."""
from __future__ import annotations

from zotero_summarizer.services.library import _paper_section_summaries as ss

SECTIONS = [
    {"id": "sec-1", "title": "Introduction", "page": 1, "text": "Framing the clinical-agent gap and prior triage baselines in detail."},
    {"id": "sec-2", "title": "Methods", "page": 4, "text": "How the triage gate is trained on labelled abstracts and evaluated."},
    {"id": "sec-3", "title": "Empty", "page": 9, "text": "   "},  # no body → excluded from the call
]


class _FakeLLM:
    def __init__(self, lines):
        self.lines = lines
        self.calls = 0

    def pydantic_prompt(self, *, prompt, pydantic_model):  # noqa: ARG002 - signature parity
        self.calls += 1
        return pydantic_model(sections=self.lines)


def test_maps_index_to_section_id_and_drops_blanks():
    llm = _FakeLLM([
        {"index": 0, "summary": "Frames the clinical-agent gap."},
        {"index": 1, "summary": "  Describes the gate training and eval.  "},
    ])
    out = ss.summarize_sections(SECTIONS, llm)
    assert llm.calls == 1                                    # ONE batched call
    assert out == {"sec-1": "Frames the clinical-agent gap.",
                   "sec-2": "Describes the gate training and eval."}  # whitespace-collapsed


def test_empty_summary_and_out_of_range_index_are_dropped():
    llm = _FakeLLM([
        {"index": 0, "summary": ""},      # empty → dropped
        {"index": 9, "summary": "x y z"}, # out of range (only 2 usable) → dropped
    ])
    assert ss.summarize_sections(SECTIONS, llm) == {}


def test_no_usable_sections_makes_no_call():
    llm = _FakeLLM([])
    assert ss.summarize_sections([{"id": "s", "title": "t", "text": ""}], llm) == {}
    assert llm.calls == 0
