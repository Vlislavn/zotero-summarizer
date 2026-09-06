"""PDF-extraction dispatch: the fitz path by default, Docling when enabled.

The Docling parse itself (TableFormer tables + figure captions) is proven against a
real PDF by tools/eval_docling_vs_fitz.py (needs the optional `docling` dep + a model
download, so it's a manual script, not a unit test). Here we lock the GATING only.
"""
from __future__ import annotations

import fitz

from zotero_summarizer.services.library import _paper_docling, _paper_read_pdf


def _pdf(tmp_path):
    path = tmp_path / "x.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((72, 72), "Real PDF text")
        doc.save(path)
    return path


def test_extract_dispatches_to_docling_when_enabled(tmp_path, monkeypatch):
    called = {}

    def _stub(p, **_kw):
        called["path"] = str(p)
        return {"full_text": "md", "sections": [], "tables": ["| a | b |"], "figures": ["Figure 1."]}

    monkeypatch.setattr(_paper_docling, "extract", _stub)
    # Both parsers now share real PDF metadata; distinct bodies prove dispatch.
    out = _paper_read_pdf.extract_pdf_content(_pdf(tmp_path), use_docling=True)
    assert called["path"].endswith("x.pdf")
    assert out["tables"] == ["| a | b |"] and out["figures"] == ["Figure 1."]
    assert out["full_text"] == "md"
    assert out["n_pages"] == 1


def test_extract_defaults_to_fitz(tmp_path, monkeypatch):
    # use_docling defaults False → Docling must NOT be called (we'd see the stub fire).
    def _boom(*_a, **_k):
        raise AssertionError("docling must not run when use_docling is False")

    monkeypatch.setattr(_paper_docling, "extract", _boom)
    content = _paper_read_pdf.extract_pdf_content(_pdf(tmp_path))
    assert "Real PDF text" in content["full_text"]
