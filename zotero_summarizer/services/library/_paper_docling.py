"""High-fidelity PDF extraction via IBM Docling — TableFormer **structured** tables +
figure classification/captions. This is the fix for the fitz path's truncated tables
and mis-extracted/duplicated figures (the user's complaint).

Optional + lazy: ``docling`` is imported only inside ``extract`` and is only called
when ``quality_review.use_docling`` is on (the layout models are heavy), so the base
install never needs it. Returns the SAME ``{full_text, sections}`` shape as
``_paper_read_pdf.extract_pdf_content`` (drop-in) PLUS ``tables`` (each as Markdown,
not truncated) and ``figures`` (deduped captions). Errors propagate — the caller
(``_paper_read_pdf`` dispatch) decides whether to fall back to fitz.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def extract(pdf_path: str | Path, *, do_ocr: bool = False) -> dict[str, Any]:
    """Parse ``pdf_path`` with Docling. ``do_ocr=False`` (default) skips OCR — arXiv /
    journal PDFs carry a text layer, so OCR only adds latency; set True for scans."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions(do_ocr=do_ocr, do_table_structure=True)
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    doc = converter.convert(str(pdf_path)).document

    full_text = doc.export_to_markdown()
    tables = [t.export_to_markdown(doc) for t in (doc.tables or [])]
    # Captions deduped by normalized text so an in-prose "Figure 1 shows…" can't
    # double-count (the fitz path's caption-duplication bug).
    seen: set[str] = set()
    figures: list[str] = []
    for pic in doc.pictures or []:
        cap = (pic.caption_text(doc) or "").strip()
        norm = " ".join(cap.lower().split())
        if cap and norm not in seen:
            seen.add(norm)
            figures.append(cap)
    sections = [
        {"title": (t.text or "").strip(), "text": ""}
        for t in (doc.texts or [])
        if getattr(t, "label", "") == "section_header" and (t.text or "").strip()
    ]
    return {"full_text": full_text, "sections": sections, "tables": tables, "figures": figures}


__all__ = ["extract"]
