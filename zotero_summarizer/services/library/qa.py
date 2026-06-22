"""Grounded paper Q&A — "ask a question about this paper" on the Library tab.

Answers come from the configured **deep_review** stage model (local by
default) over the paper's own full text, using the EXACT abstention-enforcing
prompt the faithfulness benchmark validated
(``services.faithbench.ANSWER_PROMPT``) — the product runs what was measured.

Default mode is ``comprehensive``: deterministic metadata answers first, then
the generated paper-read notes plus full text. ``retrieval`` remains available
as the fast mode.
"""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from time import perf_counter
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models.providers import resolve_stage
from zotero_summarizer.services._common import settings, state
from zotero_summarizer.services.faithbench import (
    ANSWER_PROMPT,
    PaperChunkIndex,
    answer_with_retry,
)
from zotero_summarizer.services.faithbench._constants import RETRIEVAL_TOP_K
from zotero_summarizer.services.library import paper_render
from zotero_summarizer.services.library._grounding import quote_is_grounded as _quote_is_grounded

LOGGER = logging.getLogger(__name__)

MODES = ("comprehensive", "retrieval", "full_text")

# A "how many" question scoped to a specific figure/table/section is NOT a
# whole-document count — let the LLM answer it instead of returning a doc total.
_SCOPED_REF_RE = re.compile(r"\b(?:figure|fig|table|tbl|section|sec|eq|equation|appendix)\.?\s*\d", re.IGNORECASE)

# Memoized per-paper text + chunk index, keyed by (pdf_path, mtime). Bounded:
# oldest entry evicted past _CACHE_MAX (papers are big; keep RAM flat).
_CACHE_MAX = 6
_CACHE_LOCK = threading.Lock()
_TEXT_CACHE: dict[tuple[str, int], tuple[str, PaperChunkIndex]] = {}


def _paper_context_source(item_key: str) -> tuple[str, PaperChunkIndex]:
    """Extracted full text + chunk index for a library item's local PDF."""
    app = state()
    reader = getattr(app, "zotero_reader", None)
    extractor = getattr(app, "pdf_extractor", None)
    if reader is None or extractor is None:
        raise APIError(
            error="unavailable", message="Zotero reader / PDF extractor not initialized",
            status_code=503,
        )
    detail = reader.get_item_detail(item_key)
    if detail is None:
        raise APIError(error="not_found", message=f"Item {item_key} not found", status_code=404)
    pdf_path = Path(str(detail.get("pdf_path") or ""))
    if not str(pdf_path) or not pdf_path.is_file():
        raise APIError(
            error="needs_pdf", message=f"No local PDF for item {item_key}", status_code=404
        )
    allowed = settings().pdf_root.expanduser().resolve()
    resolved = pdf_path.expanduser().resolve()
    if allowed not in [resolved, *resolved.parents]:
        raise APIError(
            error="path_not_allowed", message="PDF path is outside configured PDF_ROOT",
            status_code=403,
        )

    cache_key = (str(resolved), int(resolved.stat().st_mtime))
    with _CACHE_LOCK:
        cached = _TEXT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    text = str(extractor.extract_text(str(resolved)) or "").strip()
    if not text:
        raise APIError(
            error="extraction_empty", message="Extracted PDF text is empty", status_code=422
        )
    entry = (text, PaperChunkIndex(text))
    with _CACHE_LOCK:
        if len(_TEXT_CACHE) >= _CACHE_MAX:
            _TEXT_CACHE.pop(next(iter(_TEXT_CACHE)))
        _TEXT_CACHE[cache_key] = entry
    return entry


def ask_paper(item_key: str, question: str, *, mode: str = "comprehensive") -> dict[str, Any]:
    """Answer ``question`` from the paper's text with enforced abstention.

    Returns ``{answer, abstained, quote, mode, chunks_used, latency_seconds,
    model}`` — ``answer`` is ``None`` when the model abstains (the UI says
    "not in the paper" instead of inventing one).
    """
    question = (question or "").strip()
    if not question:
        raise APIError(error="validation_error", message="question must be non-empty", status_code=422)
    if mode not in MODES:
        raise APIError(
            error="validation_error", message=f"mode must be one of {MODES}", status_code=422
        )

    artifact = paper_render.ensure_artifact(item_key)
    deterministic = _answer_from_artifact_counts(artifact, question)
    if deterministic is not None:
        return deterministic | {"item_key": item_key, "question": question, "mode": "metadata"}

    app = state()
    llm = app.resolve_stage_client("deep_review")
    config = app.app_state.config
    max_chars = int(config.quality_review.max_text_chars)
    resolved = resolve_stage(config.llm_routing, "deep_review")

    # Three genuinely distinct contexts:
    #   retrieval     — top-k chunks of the PDF body (fast, narrow)
    #   full_text     — the raw extracted PDF body only (no notes wrapper)
    #   comprehensive — metadata + generated notes/digest + PDF body (default)
    chunks: list[str] = []
    if mode == "retrieval":
        text, index = _paper_context_source(item_key)
        chunks = index.top_chunks(question, RETRIEVAL_TOP_K)
        context = "\n\n[...]\n\n".join(chunks) if chunks else text[:max_chars]
    elif mode == "full_text":
        text, _index = _paper_context_source(item_key)
        context = text[:max_chars]
    else:
        context = paper_render.artifact_text(artifact, max_chars=max_chars)

    prompt = ANSWER_PROMPT.format(context=context, question=question)
    t0 = perf_counter()
    try:
        parsed, _raw = answer_with_retry(llm, prompt)
    except ValueError:
        # LLM output had no recoverable JSON answer (empty / malformed — often a
        # transient endpoint hiccup, or a reasoning model emptying `content` at
        # low max_tokens). Untrusted LLM output at this boundary becomes an
        # abstention, not an unhandled 500 — the user sees "no grounded answer".
        LOGGER.warning("qa: item=%s mode=%s — unparseable LLM output; abstaining", item_key, mode)
        parsed = {"answer": None, "abstained": True, "quote": None}
    latency = round(perf_counter() - t0, 2)
    LOGGER.info("qa: item=%s mode=%s latency=%.1fs abstained=%s",
                item_key, mode, latency, parsed["abstained"])
    if parsed["answer"] is not None and not _quote_is_grounded(parsed["quote"], context):
        parsed = {"answer": None, "abstained": True, "quote": None}
    return {
        "item_key": item_key,
        "question": question,
        "answer": parsed["answer"],
        "abstained": parsed["abstained"],
        "quote": parsed["quote"],
        "mode": mode,
        "chunks_used": len(chunks),
        "latency_seconds": latency,
        "model": resolved.model,
    }


def _answer_from_artifact_counts(artifact: dict[str, Any], question: str) -> dict[str, Any] | None:
    """Deterministic answer for true whole-document count questions only.

    Questions scoped to a specific figure/table/section (e.g. "how many
    references does Figure 3 cite?") are NOT whole-document totals → fall through
    to the LLM rather than returning a confident wrong global count."""
    q = (question or "").casefold()
    if "how many" not in q and "number of" not in q:
        return None
    if _SCOPED_REF_RE.search(question or ""):
        return None
    if "page" in q:
        n = int(artifact.get("n_pages") or 0)
        return _metadata_payload(f"{n} pages", f"Pages: {n}")
    if "figure" in q or "figures" in q or "table" in q or "tables" in q:
        n = int(artifact.get("figures_count") or 0)
        return _metadata_payload(f"{n} figures/tables", f"Figures: {n}")
    if "reference" in q or "references" in q or "citation" in q or "citations" in q or "papers cited" in q:
        n = int(artifact.get("references_count") or 0)
        return _metadata_payload(f"{n} references", f"References: {n}")
    if "section" in q or "sections" in q:
        n = int(artifact.get("sections_count") or len(artifact.get("sections") or []))
        return _metadata_payload(f"{n} sections", f"Sections: {n}")
    return None


def _metadata_payload(answer: str, quote: str) -> dict[str, Any]:
    return {
        "answer": answer,
        "abstained": False,
        "quote": quote,
        "chunks_used": 0,
        "latency_seconds": 0.0,
        "model": "deterministic-metadata",
    }


