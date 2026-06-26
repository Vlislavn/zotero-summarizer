"""Compare the Docling vs fitz PDF extraction on a real paper (run by hand; needs the
optional `docling` dep + a model download on first use — hence tools/, not a test).

Shows that Docling recovers STRUCTURED tables (TableFormer) and deduped figure
captions that the fitz path either truncates or inlines as flat text — the user's
"truncated tables / mis-extracted figures" complaint.

Run:  uv run python tools/eval_docling_vs_fitz.py /path/to/paper.pdf
      (default: /tmp/chexnet.pdf — arxiv.org/pdf/1711.05225)
"""
from __future__ import annotations

import sys
from pathlib import Path

from zotero_summarizer.services.library import _paper_read_pdf


def main() -> int:
    pdf = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/chexnet.pdf")
    if not pdf.exists():
        print(f"PDF not found: {pdf} (download e.g. curl -L -o /tmp/chexnet.pdf https://arxiv.org/pdf/1711.05225)")
        return 2

    fitz_out = _paper_read_pdf.extract_pdf_content(pdf, use_docling=False)
    doc_out = _paper_read_pdf.extract_pdf_content(pdf, use_docling=True)

    print(f"=== {pdf.name} ===")
    print(f"fitz   : full_text={len(fitz_out.get('full_text') or '')} chars, "
          f"tables={len(fitz_out.get('tables') or [])}, figures={len(fitz_out.get('figures') or [])}")
    print(f"docling: full_text={len(doc_out.get('full_text') or '')} chars, "
          f"tables={len(doc_out.get('tables') or [])}, figures={len(doc_out.get('figures') or [])}")
    tables = doc_out.get("tables") or []
    if tables:
        print("\n--- Docling table[0] (structured, not truncated) ---")
        print(tables[0][:600])
    figs = doc_out.get("figures") or []
    print("\n--- Docling figure captions (deduped) ---")
    for c in figs[:6]:
        print(" *", c[:100])

    ok = len(tables) >= 1 and len(figs) >= 1
    print("\nRESULT:", "PASS ✓ (docling recovered structured tables + figures)" if ok else "FAIL ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
