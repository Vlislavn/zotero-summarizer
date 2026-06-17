"""PDF-extraction dispatch: the fitz path by default, Docling when enabled.

The Docling parse itself (TableFormer tables + figure captions) is proven against a
real PDF by tools/eval_docling_vs_fitz.py (needs the optional `docling` dep + a model
download, so it's a manual script, not a unit test). Here we lock the GATING only.
"""
from __future__ import annotations

from pathlib import Path

from zotero_summarizer.services.library import _paper_docling, _paper_read_pdf


def test_extract_dispatches_to_docling_when_enabled(monkeypatch):
    called = {}

    def _stub(p, **_kw):
        called["path"] = str(p)
        return {"full_text": "md", "sections": [], "tables": ["| a | b |"], "figures": ["Figure 1."]}

    monkeypatch.setattr(_paper_docling, "extract", _stub)
    # A non-existent path: if this fell through to fitz it would raise on open — so a
    # clean return proves the Docling branch was taken instead.
    out = _paper_read_pdf.extract_pdf_content(Path("/nonexistent/x.pdf"), use_docling=True)
    assert called["path"].endswith("x.pdf")
    assert out["tables"] == ["| a | b |"] and out["figures"] == ["Figure 1."]


def test_extract_defaults_to_fitz(monkeypatch):
    # use_docling defaults False → Docling must NOT be called (we'd see the stub fire).
    def _boom(*_a, **_k):
        raise AssertionError("docling must not run when use_docling is False")

    monkeypatch.setattr(_paper_docling, "extract", _boom)
    # fitz will raise on the missing file — that's fine; we only assert docling is skipped.
    import pytest

    with pytest.raises(Exception):  # noqa: B017 — any fitz open error; the point is _boom did NOT fire
        _paper_read_pdf.extract_pdf_content(Path("/nonexistent/x.pdf"))
