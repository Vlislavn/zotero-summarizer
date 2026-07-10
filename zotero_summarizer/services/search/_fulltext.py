"""Full-text acquisition for a federated candidate (spec §9).

External candidates carry no Zotero attachment, so we resolve an open-access PDF
URL from the identifiers we already have (arXiv id → Unpaywall(DOI) → a direct PDF
url), stream it to the shared cache, and extract text with the app's onprem
extractor — the same PDF→text path deep review uses, minus the Zotero item key.

The network + OA-resolution steps are inherently best-effort (a paper may have no
OA copy). ``pdf_fetch`` returns ``None`` at that boundary by contract; we surface
that as an empty string, and the review tiers branch to ``uncertain`` /
``needs_full_text`` on it (spec §9 — a missing OA copy is an expected state for a
false-negative-averse reader, never a fabricated review). No headed/paywalled
browser rung here — targeted screening stays to open access (spec §12).
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.integrations import europepmc, pdf_fetch
from zotero_summarizer.services.search._models import Candidate


def acquire_full_text(cand: Candidate, *, extractor: Any, unpaywall: Any = None) -> str:
    """Resolve + fetch + extract this candidate's OA full text. Returns the text,
    or ``""`` when no OA copy is resolvable / extractable (the spec §9 boundary the
    review tiers handle). ``extractor is None`` (quality disabled) → ``""``."""
    if extractor is None:
        return ""
    # PMC open access: the reliable keyless full text is Europe PMC's fullTextXML
    # REST endpoint — the fullTextUrlList ``?pdf=render`` link 404s and NCBI's
    # ``/pdf/`` mirror serves a bot-block HTML page, so recover the machine-readable
    # XML before falling back to a PDF fetch (derive-over-cull).
    if cand.pmcid:
        text = europepmc.fetch_fulltext_xml(cand.pmcid)
        if text:
            return text
    url = pdf_fetch.resolve_pdf_url(
        doi=cand.doi or None, arxiv_id=cand.arxiv_id or None,
        url=cand.url or None, unpaywall=unpaywall,
    )
    if not url:
        return ""
    path = pdf_fetch.fetch_pdf(url)
    if path is None:
        return ""
    return extractor.extract_text(path).strip()


__all__ = ["acquire_full_text"]
