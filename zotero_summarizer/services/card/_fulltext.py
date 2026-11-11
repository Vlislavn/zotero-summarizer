"""Full text for a paper, reusing the library acquisition chain.

No new network code: :mod:`services.library._pdf_acquire` resolves and downloads
the PDF into the shared cache (arXiv → Unpaywall → PMC → OpenAlex → bioRxiv rung),
and :mod:`services.library._paper_read_pdf` extracts the body text *and* the PDF
hyperlink annotations (the ``/URI`` links that sit over "our code" anchors and are
invisible to a plain-text regex). Everything fails soft to abstract-only facts.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from zotero_summarizer.services._common import LOGGER
from zotero_summarizer.services.library import _paper_read_pdf, _pdf_acquire

if TYPE_CHECKING:
    from zotero_summarizer.services.card.facts import PaperRef


@dataclass(slots=True)
class FullText:
    """Acquired full text + provenance. ``source`` is the resolver rung that won
    (``arxiv``/``unpaywall``/``biorxiv``/``pmc``/``openalex``/``abstract``).
    ``source_sha`` is the sha256 of ``text`` — the cache's content-change key."""

    text: str = ""
    link_uris: list[str] = field(default_factory=list)
    source: str = ""
    pdf_path: str = ""
    source_sha: str = ""
    chars: int = 0


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _from_pdf(pdf: Path, source: str) -> FullText:
    content = _paper_read_pdf.extract_pdf_content(pdf)
    text = str(content.get("full_text") or "")
    return FullText(
        text=text, link_uris=list(content.get("link_uris") or []),
        source=source, pdf_path=str(pdf), source_sha=_sha(text), chars=len(text),
    )


def get_fulltext(ref: "PaperRef", *, abstract: str = "") -> FullText:
    """Acquire and extract the paper's full text. Uses the app's headless
    acquisition chain; on any failure returns the abstract as a degraded
    source so downstream facts still compute."""
    detail = {"url": ref.url, "doi": ref.doi, "has_pdf": False}
    try:
        result = _pdf_acquire.acquire_pdf_for(ref.key, detail, allow_browser=False)
    except Exception as exc:  # noqa: BLE001 — best-effort background enrichment
        LOGGER.info("card facts: PDF acquire failed for %s: %s", ref.key, exc)
        result = None
    if result is not None and result.path is not None:
        return _from_pdf(result.path, result.source or "pdf")

    return FullText(text=abstract, source="abstract", source_sha=_sha(abstract), chars=len(abstract))


__all__ = ["FullText", "get_fulltext"]
