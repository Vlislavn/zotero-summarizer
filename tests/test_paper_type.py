"""Tests for paper-type detection (offline — the LLM is a stub)."""
from __future__ import annotations

from zotero_summarizer.services.library import paper_type as pt
from zotero_summarizer.services.library._paper_type_checklists import PaperType


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload

    def pydantic_prompt(self, *, prompt, pydantic_model):
        return pydantic_model(**self.payload)


def test_structural_signals_detect_review_vs_empirical():
    rev = pt._structural_signals(["Introduction", "Search strategy", "Discussion"],
                                "We searched PubMed and Embase; inclusion criteria were…")
    assert rev["prisma"] and not rev["propose"]
    emp = pt._structural_signals(["Methods", "Results"], "We propose a new model and evaluate it…")
    assert emp["propose"] and emp["imrad"]


def test_high_confidence_llm_verdict_is_taken():
    llm = _FakeLLM({"paper_type": "systematic_review", "confidence": 0.92,
                    "reasoning": "PRISMA flow + search", "secondary": "narrative_review"})
    out = pt.detect(title="A systematic review", abstract="We systematically reviewed…",
                    headings=["Search strategy"], full_text="databases searched", llm=llm)
    assert out["type"] == "systematic_review" and out["source"] == "llm" and not out["uncertain"]


def test_low_confidence_falls_back_to_safe_supertype():
    llm = _FakeLLM({"paper_type": "empirical_ml", "confidence": 0.30, "reasoning": "unsure"})
    # review-shaped signals + no experiments → GENERIC_REVIEW, marked uncertain
    out = pt.detect(title="On the future of X", abstract="we argue that…",
                    headings=["Introduction", "Discussion"],
                    full_text="In this review we survey the literature; we recommend…", llm=llm)
    assert out["type"] == PaperType.GENERIC_REVIEW.value and out["uncertain"] is True
    assert out["source"] == "fallback"


def test_invalid_llm_type_is_normalised_then_falls_back():
    llm = _FakeLLM({"paper_type": "not_a_type", "confidence": 0.99})
    out = pt.detect(title="t", abstract="a", headings=["Methods", "Results"],
                    full_text="we propose and evaluate", llm=llm)
    assert out["type"] == PaperType.GENERIC_EMPIRICAL.value and out["uncertain"] is True


def test_override_wins_without_calling_llm():
    class _Boom:
        def pydantic_prompt(self, **_k):
            raise AssertionError("override must not call the LLM")

    out = pt.detect(title="t", abstract="a", headings=[], full_text="x",
                    llm=_Boom(), override="narrative_review")
    assert out["type"] == "narrative_review" and out["source"] == "override"


def test_itemtype_case_contradiction_forces_case_report_fallback():
    llm = _FakeLLM({"paper_type": "empirical_ml", "confidence": 0.95})
    out = pt.detect(title="t", abstract="a 67-year-old patient", headings=[],
                    full_text="patient presented", item_type="case", llm=llm)
    # LLM said empirical with high conf, but itemType=case contradicts → fallback
    assert out["uncertain"] is True and out["source"] == "fallback"
