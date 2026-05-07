from __future__ import annotations

from pathlib import Path
from typing import Protocol


class PdfExtractor(Protocol):
    def extract_text(self, pdf_path: str | Path) -> str:
        ...


class OnPremPdfExtractor:
    """PDF extractor adapter around `onprem.ingest.base.load_single_document`."""

    def __init__(self, load_single_document) -> None:
        self._load_single_document = load_single_document

    def extract_text(self, pdf_path: str | Path) -> str:
        docs = self._load_single_document(str(pdf_path), pdf_markdown=True)
        parts = []
        for doc in docs:
            text = getattr(doc, "page_content", None)
            if text:
                parts.append(str(text))
        return "\n\n".join(parts).strip()
