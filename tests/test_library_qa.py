"""library.qa: generated-artifact Q&A, deterministic counts, abstention and
retrieval mode."""
from __future__ import annotations

import json
import types

import pytest

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.library import qa

PAPER_TEXT = (
    "We evaluated GlassNet on the ImageNet dataset. "
    "Training used 1,281,167 images over 90 epochs. "
    "The top-1 accuracy reached 85.3 percent. "
    + " ".join(f"Background filler sentence {i}." for i in range(400))
)


class _Extractor:
    def __init__(self):
        self.calls = 0

    def extract_text(self, pdf_path):
        self.calls += 1
        return PAPER_TEXT


class _LLM:
    def __init__(self, answer="ImageNet"):
        self.answer = answer
        self.prompts = []

    def prompt(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return json.dumps({"answer": self.answer, "quote": "We evaluated GlassNet on the ImageNet dataset."})


def _fake_state(tmp_path, pdf, extractor, llm, monkeypatch):
    class _Reader:
        def get_item_detail(self, key):
            if key == "GONE":
                return None
            if key == "NOPDF":
                return {"title": "t", "pdf_path": ""}
            return {"title": "GlassNet", "pdf_path": str(pdf)}

    routing = types.SimpleNamespace()
    config = types.SimpleNamespace(
        quality_review=types.SimpleNamespace(max_text_chars=60_000),
        llm_routing=routing,
    )
    app = types.SimpleNamespace(
        zotero_reader=_Reader(),
        pdf_extractor=extractor,
        app_state=types.SimpleNamespace(config=config),
        resolve_stage_client=lambda stage: llm,
    )
    monkeypatch.setattr(qa, "state", lambda: app)
    monkeypatch.setattr(qa, "settings", lambda: types.SimpleNamespace(pdf_root=tmp_path))
    monkeypatch.setattr(
        qa, "resolve_stage",
        lambda routing, stage: types.SimpleNamespace(model="local-35b"),
    )
    qa._TEXT_CACHE.clear()
    def _artifact(item_key):
        if item_key == "GONE":
            raise APIError(error="not_found", message="gone", status_code=404)
        if item_key == "NOPDF":
            raise APIError(error="needs_pdf", message="no pdf", status_code=404)
        return {
            "title": "GlassNet",
            "n_pages": 12,
            "figures_count": 3,
            "references_count": 42,
            "sections_count": 8,
            "outputs": {},
            "full_text": PAPER_TEXT,
        }

    monkeypatch.setattr(qa.paper_render, "ensure_artifact", _artifact)
    monkeypatch.setattr(qa.paper_render, "artifact_text", lambda artifact, max_chars: PAPER_TEXT[:max_chars])
    return app


def test_ask_paper_comprehensive_answers_from_artifact(tmp_path, monkeypatch):
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-fake")
    extractor, llm = _Extractor(), _LLM()
    _fake_state(tmp_path, pdf, extractor, llm, monkeypatch)

    out = qa.ask_paper("KEY1", "Which dataset was used?")
    assert out["answer"] == "ImageNet" and out["abstained"] is False
    assert out["mode"] == "comprehensive" and out["chunks_used"] == 0
    assert out["model"] == "local-35b" and out["latency_seconds"] >= 0
    assert "ImageNet" in llm.prompts[0]

    qa.ask_paper("KEY1", "How many epochs?", mode="retrieval")
    assert extractor.calls == 1  # memoized per (pdf, mtime)


def test_ask_paper_answers_counts_without_llm(tmp_path, monkeypatch):
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-fake")
    extractor, llm = _Extractor(), _LLM()
    _fake_state(tmp_path, pdf, extractor, llm, monkeypatch)

    out = qa.ask_paper("KEY1", "How many pages are in the paper?")
    assert out["answer"] == "12 pages"
    assert out["mode"] == "metadata"
    assert not llm.prompts


def test_ask_paper_abstention_passes_through(tmp_path, monkeypatch):
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-fake")

    class _AbstainLLM:
        def prompt(self, prompt, **kwargs):
            return json.dumps({"answer": None, "quote": None})

    _fake_state(tmp_path, pdf, _Extractor(), _AbstainLLM(), monkeypatch)
    out = qa.ask_paper("KEY1", "What was the cohort size?")
    assert out["abstained"] is True and out["answer"] is None


def test_ask_paper_empty_llm_output_abstains_not_500(tmp_path, monkeypatch):
    """A1 regression: an empty / unparseable LLM completion (0 output tokens, or a
    reasoning model emptying `content`) must ABSTAIN at the qa boundary, not raise
    an unhandled ValueError → 500. answer_with_retry re-feeds the empty body and
    raises; ask_paper must catch it and return the abstain payload."""
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-fake")

    class _EmptyLLM:
        def prompt(self, prompt, **kwargs):
            return ""  # no JSON recoverable

    _fake_state(tmp_path, pdf, _Extractor(), _EmptyLLM(), monkeypatch)
    out = qa.ask_paper("KEY1", "What was the cohort size?")
    assert out["abstained"] is True and out["answer"] is None and out["quote"] is None


def test_ask_paper_full_text_mode_sends_whole_capped_text(tmp_path, monkeypatch):
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-fake")
    llm = _LLM()
    _fake_state(tmp_path, pdf, _Extractor(), llm, monkeypatch)
    out = qa.ask_paper("KEY1", "Which dataset was used?", mode="full_text")
    assert out["mode"] == "full_text" and out["chunks_used"] == 0
    assert "Background filler sentence 399." in llm.prompts[0]


def test_ask_paper_rejects_ungrounded_quotes(tmp_path, monkeypatch):
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-fake")

    class _BadQuoteLLM:
        def prompt(self, prompt, **kwargs):
            return json.dumps({"answer": "CIFAR-10", "quote": "This quote is not in the paper."})

    _fake_state(tmp_path, pdf, _Extractor(), _BadQuoteLLM(), monkeypatch)
    out = qa.ask_paper("KEY1", "Which dataset was used?")
    assert out["abstained"] is True and out["answer"] is None


def test_ask_paper_rejects_short_grounded_quote(tmp_path, monkeypatch):
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-fake")

    class _ShortQuoteLLM:
        def prompt(self, prompt, **kwargs):
            # "GlassNet" IS a substring of the paper but is a single word — too
            # short to be a real supporting quote, so the answer must be rejected.
            return json.dumps({"answer": "GlassNet", "quote": "GlassNet"})

    _fake_state(tmp_path, pdf, _Extractor(), _ShortQuoteLLM(), monkeypatch)
    out = qa.ask_paper("KEY1", "What is the model called?")
    assert out["abstained"] is True and out["answer"] is None


def test_comprehensive_and_full_text_contexts_differ(tmp_path, monkeypatch):
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-fake")
    llm = _LLM()
    _fake_state(tmp_path, pdf, _Extractor(), llm, monkeypatch)
    # Comprehensive wraps the body in metadata+notes; full_text is the raw body.
    monkeypatch.setattr(
        qa.paper_render, "artifact_text",
        lambda artifact, max_chars: "NOTES_AND_METADATA " + PAPER_TEXT[:max_chars],
    )
    qa.ask_paper("KEY1", "Which dataset was used?", mode="comprehensive")
    qa.ask_paper("KEY1", "Which dataset was used?", mode="full_text")
    comp_prompt, full_prompt = llm.prompts[0], llm.prompts[1]
    assert "NOTES_AND_METADATA" in comp_prompt
    assert "NOTES_AND_METADATA" not in full_prompt  # raw body only, no notes wrapper


def test_scoped_count_question_falls_through_to_llm(tmp_path, monkeypatch):
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-fake")
    llm = _LLM(answer="a few")
    _fake_state(tmp_path, pdf, _Extractor(), llm, monkeypatch)
    # "how many ... Figure 3 ..." is scoped to a figure, not a whole-doc count.
    out = qa.ask_paper("KEY1", "How many references does Figure 3 cite?")
    assert out["mode"] != "metadata"  # not the deterministic count path
    assert llm.prompts  # the LLM actually answered


def test_ask_paper_boundary_errors(tmp_path, monkeypatch):
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-fake")
    _fake_state(tmp_path, pdf, _Extractor(), _LLM(), monkeypatch)

    with pytest.raises(APIError) as not_found:
        qa.ask_paper("GONE", "q?")
    assert not_found.value.status_code == 404

    with pytest.raises(APIError) as needs_pdf:
        qa.ask_paper("NOPDF", "q?")
    assert needs_pdf.value.error == "needs_pdf"

    with pytest.raises(APIError):
        qa.ask_paper("KEY1", "   ")  # empty question
    with pytest.raises(APIError):
        qa.ask_paper("KEY1", "q?", mode="telepathy")
