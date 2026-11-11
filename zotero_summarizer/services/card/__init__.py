"""Today card facts (Todo #36) and, later, the AI card.

``facts.collect_facts`` gathers the owner's decision facts for a paper by reusing
the library full-text chain, the code-link cascade, OpenAlex and the PDF header —
cached as JSON with per-fact TTLs, no new DB table and no LLM. See ``README.md``.
"""
from zotero_summarizer.services.card.facts import (
    EXTRACTOR_VERSION,
    PaperRef,
    collect_facts,
    resolve_ref,
)

__all__ = ["EXTRACTOR_VERSION", "PaperRef", "collect_facts", "resolve_ref"]
