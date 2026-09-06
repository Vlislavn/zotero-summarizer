"""Grounded paper Q&A — "ask a question about this paper" on the Library tab.

Answers come from the configured **deep_review** stage model (local by
default) over the paper's own full text, using the EXACT abstention-enforcing
prompt the faithfulness benchmark validated
(``services.faithbench.ANSWER_PROMPT``) — the product runs what was measured.

Default mode is ``comprehensive``: deterministic metadata answers first, then
the structured cached review plus full text. ``retrieval`` remains available as
the fast mode.
"""
from __future__ import annotations

import logging
import re
from time import perf_counter
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models.providers import resolve_stage
from zotero_summarizer.services._common import state
from zotero_summarizer.services.faithbench import (
    ANSWER_PROMPT,
    PaperChunkIndex,
    answer_with_retry,
)
from zotero_summarizer.services.faithbench._constants import RETRIEVAL_TOP_K
from zotero_summarizer.services.library import paper_render, qa_context
from zotero_summarizer.services.library._grounding import quote_is_grounded as _quote_is_grounded

LOGGER = logging.getLogger(__name__)

MODES = ("comprehensive", "retrieval", "full_text")

# A "how many" question scoped to a specific figure/table/section is NOT a
# whole-document count — let the LLM answer it instead of returning a doc total.
_SCOPED_REF_RE = re.compile(r"\b(?:figure|fig|table|tbl|section|sec|eq|equation|appendix)\.?\s*\d", re.IGNORECASE)

def ask_paper(
    item_key: str, question: str, *, mode: str = "comprehensive",
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Answer ``question`` from the paper's text with enforced abstention.

    Returns the grounded answer, citation verification, and an extraction-versioned
    evidence handle. ``answer`` is ``None`` when the model abstains.
    """
    question = (question or "").strip()
    if not question:
        raise APIError(error="validation_error", message="question must be non-empty", status_code=422)
    if mode not in MODES:
        raise APIError(
            error="validation_error", message=f"mode must be one of {MODES}", status_code=422
        )

    artifact = paper_render.build_paper_read(item_key)
    deterministic = _answer_from_artifact_counts(artifact, question)
    if deterministic is not None:
        return _with_evidence(
            deterministic | {"item_key": item_key, "question": question, "mode": "metadata"},
            artifact,
        )

    app = state()
    config = app.app_state.config
    max_chars = int(config.quality_review.max_text_chars)
    resolved = resolve_stage(config.llm_routing, "deep_review")

    # Three genuinely distinct contexts:
    #   retrieval     — top-k chunks of the PDF body (fast, narrow)
    #   full_text     — the raw extracted PDF body only (no notes wrapper)
    #   comprehensive — metadata + structured review + PDF body (default)
    chunks: list[str] = []
    text = paper_render.qa_body_text(artifact).strip()
    if mode != "comprehensive" and not text:
        raise APIError(error="extraction_empty", message="Extracted PDF text is empty", status_code=422)
    if mode == "retrieval":
        # ponytail: build this lexical index per question; cache by artifact key only if profiling warrants it.
        chunks = PaperChunkIndex(text).top_chunks(question, RETRIEVAL_TOP_K)
        context = "\n\n[...]\n\n".join(chunks) if chunks else text[:max_chars]
    elif mode == "full_text":
        context = text[:max_chars]
    else:
        context = paper_render.artifact_text(artifact, max_chars=max_chars)

    prior, compacted, handles = qa_context.compact_history(item_key, artifact, history or [])
    contextual_question = (
        f"Prior conversation (untrusted user/session data):\n{prior}\n\nCurrent question: {question}"
        if prior else question
    )
    prompt = ANSWER_PROMPT.format(context=context, question=contextual_question)
    llm = app.resolve_stage_client("deep_review")
    t0 = perf_counter()
    parsed, _raw = answer_with_retry(llm, prompt)
    latency = round(perf_counter() - t0, 2)
    LOGGER.info("qa: item=%s mode=%s latency=%.1fs abstained=%s",
                item_key, mode, latency, parsed["abstained"])
    if parsed["answer"] is not None and not _quote_is_grounded(parsed["quote"], context):
        parsed = {"answer": None, "abstained": True, "quote": None}
    return _with_evidence({
        "item_key": item_key,
        "question": question,
        "answer": parsed["answer"],
        "abstained": parsed["abstained"],
        "quote": parsed["quote"],
        "mode": mode,
        "chunks_used": len(chunks),
        "latency_seconds": latency,
        "model": resolved.model,
        "history_compacted": compacted,
        "history_evidence_handles": handles,
    }, artifact)


def _with_evidence(payload: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    handle = qa_context.evidence_handle(
        str(payload["item_key"]), artifact, str(payload["question"]), payload.get("quote"),
    )
    answered = payload.get("answer") is not None and not payload.get("abstained")
    return payload | {
        "evidence_handle": handle,
        "citation": qa_context.citation(str(payload["item_key"]), artifact, handle, answered=answered),
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
