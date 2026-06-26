from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Any

from zotero_summarizer.api.errors import APIError, ExtractionError
from zotero_summarizer.integrations.llm import LLMClient
from zotero_summarizer.integrations.pdf import PdfExtractor
from zotero_summarizer.models import (
    GoalsConfig,
    RefinedSummary,
    SummarizeRequest,
    SummarizeResponse,
    TriageResult,
)
from zotero_summarizer.domain import ReadingPriority
from zotero_summarizer.services import corpus
from zotero_summarizer.services.model import scoring
from zotero_summarizer.services._common import (
    LOGGER,
    build_log_prefix,
    extract_json_blob,
    log_context,
    settings,
    state,
    to_text,
)


def _extract_pdf_text(pdf_path: str) -> str:
    pdf_candidate = Path(pdf_path).expanduser().resolve()
    allowed_root = settings().pdf_root.expanduser().resolve()
    if allowed_root not in [pdf_candidate, *pdf_candidate.parents]:
        raise APIError(
            error="path_not_allowed",
            message="PDF path is outside configured PDF_ROOT",
            status_code=403,
            details={"pdf_path": str(pdf_candidate), "pdf_root": str(allowed_root)},
        )

    if not pdf_candidate.is_file():
        raise FileNotFoundError(pdf_path)

    extractor: PdfExtractor | None = getattr(state(), "pdf_extractor", None)
    if extractor is None:
        raise ExtractionError("PDF extractor is not initialized")

    text = extractor.extract_text(pdf_candidate)
    if not text.strip():
        raise ExtractionError("Extracted document content is empty")
    return text


def _build_refine_prompt(config: GoalsConfig, req: SummarizeRequest, paper_text: str) -> str:
    template = config.prompts.refine or (
        "Refine the draft summary into structured JSON with keys executive_summary, should_deep_read, "
        "key_sections_to_read, relevance_to_research, controversial_points, industry_academy_impact, "
        "unknown_unknowns, implementation_quickstart, key_findings, methods, limitations."
    )
    return template.format(
        title=req.title,
        doi=req.doi or "N/A",
        abstract=req.abstract or "N/A",
        paper_text=paper_text,
        research_goals="\n".join(f"- {g}" for g in config.research_goals),
        summary_structure="\n".join(f"- {s}" for s in config.summary_structure),
        output_language=config.output_language,
    )


def _build_triage_prompt(
    config: GoalsConfig,
    req: SummarizeRequest,
    refined: RefinedSummary,
    corpus_context: dict[str, Any],
) -> str:
    template = config.prompts.triage or (
        "You are a strict triage reviewer. Default stance: NOT relevant unless proven by concrete evidence. "
        "Return JSON with score, reading_priority, tags, rationale, dimensions, confidence."
    )
    return template.format(
        research_goals="\n".join(f"- {g}" for g in config.research_goals),
        triage_criteria="\n".join(f"- {c}" for c in config.triage_criteria),
        relevance_scale="\n".join(f"{score}: {desc}" for score, desc in sorted(config.relevance_scale.items())),
        reading_priority_scale="\n".join(f"{key}: {desc}" for key, desc in config.reading_priority_scale.items()),
        title=req.title,
        doi=req.doi or "N/A",
        summary=refined.executive_summary,
        corpus_context=corpus.build_corpus_context_text(corpus_context),
        corpus_affinity=f"{corpus_context.get('affinity_score', 0.0):.3f}",
        matched_goal=corpus_context.get("matched_goal", ""),
        matched_goal_similarity=f"{corpus_context.get('matched_goal_similarity', 0.0):.3f}",
        suggested_collections=", ".join(corpus_context.get("suggested_collections", [])),
        output_language=config.output_language,
    )


def run_abstract_pipeline(
    req: SummarizeRequest,
    log_prefix: str | None = None,
    *,
    llm_override: LLMClient | None = None,
) -> SummarizeResponse:
    """Triage a paper using only title + abstract (no PDF).

    Used by the RSS-feed batch processor: feed items have title + abstract but
    not yet a downloaded PDF. The same refine + triage prompts are reused; the
    LLM operates on the abstract directly. Zotero's "Find Available PDF"
    feature fetches PDFs after the item lands in a collection, so deep-text
    analysis can happen on a future re-triage if the user promotes the paper.

    The corpus pre-filter still applies — papers with very low corpus affinity
    are fast-rejected before any LLM call, exactly as in `run_pipeline`.

    ``llm_override`` lets a caller (e.g. the backlog-triage job) score with the
    backlog stage's client instead — without affecting the feed stage client
    used by the daemon. ``None`` resolves the configured **feed** stage client.
    """
    app_state = state()
    config: GoalsConfig = app_state.app_state.config
    llm: LLMClient = llm_override if llm_override is not None else app_state.resolve_stage_client("feed")
    prefix = log_prefix or build_log_prefix(req)
    pipeline_started = perf_counter()

    abstract_text = (req.abstract or "").strip()
    if not abstract_text:
        # Without an abstract there's nothing for the LLM to evaluate.
        return _abstract_only_empty_response("Feed item missing abstract")

    log_context(prefix, "abstract pipeline started chars=%d", len(abstract_text))

    corpus_context = corpus.run_corpus_match(req, abstract_text)
    log_context(
        prefix,
        "corpus stage has_corpus=%s affinity=%.3f matched_goal=%s",
        corpus_context.get("has_corpus"),
        corpus_context.get("affinity_score", 0.0),
        corpus_context.get("matched_goal", ""),
    )

    if (
        corpus_context.get("has_corpus")
        and float(corpus_context.get("affinity_score", 0.0)) < config.corpus.similarity_threshold
    ):
        log_context(prefix, "fast-rejected by corpus threshold=%.3f", config.corpus.similarity_threshold)
        return _fast_reject_response(req, corpus_context, abstract_text)

    # Use the abstract as paper_text directly — same refine/triage prompts.
    refined = _refine_with_retry(llm, config, req, abstract_text, prefix)
    triage = _run_triage(llm, config, req, refined, corpus_context)
    composite_score = scoring.compute_composite_score(triage, float(corpus_context.get("affinity_score", 0.0)))
    mapped_priority = scoring.map_priority_from_score(composite_score)

    log_context(
        prefix,
        "abstract pipeline completed in %.2fs composite=%.2f priority=%s",
        perf_counter() - pipeline_started,
        composite_score,
        mapped_priority,
    )
    return _assemble_summary_response(
        refined, triage, composite_score, mapped_priority, corpus_context
    )


def _abstract_only_empty_response(reason: str) -> SummarizeResponse:
    # Only the non-default fields; the rest fall back to SummarizeResponse defaults.
    return SummarizeResponse(
        executive_summary=reason,
        should_deep_read="No.",
        relevance_score=1,
        composite_relevance_score=1.0,
        reading_priority=ReadingPriority.DONT_READ.value,
        tags=["abstract_missing"],
        triage_rationale=reason,
        triage_confidence=0.5,
    )


def _fast_reject_response(
    req: SummarizeRequest, corpus_context: dict[str, Any], paper_text: str
) -> SummarizeResponse:
    """Build a low-priority response when corpus pre-filter rejects."""
    summary_seed = (req.abstract or paper_text).strip()[:2000] or "Low corpus affinity."
    return SummarizeResponse(
        executive_summary=summary_seed,
        should_deep_read="No. Low corpus affinity against your engaged library.",
        relevance_to_research="Fast-rejected by corpus similarity pre-filter.",
        relevance_score=1,
        composite_relevance_score=1.0,
        reading_priority=ReadingPriority.DONT_READ.value,
        tags=["prefilter_low_corpus_affinity"],
        triage_rationale="Corpus affinity was below threshold; paper likely does not match your engaged library profile.",
        triage_confidence=0.9,
        corpus_affinity_score=float(corpus_context.get("affinity_score", 0.0)),
        corpus_positive_similarity=float(corpus_context.get("positive_similarity", 0.0)),
        corpus_negative_similarity=float(corpus_context.get("negative_similarity", 0.0)),
        matched_goal=str(corpus_context.get("matched_goal", "") or ""),
        matched_goal_similarity=float(corpus_context.get("matched_goal_similarity", 0.0)),
        suggested_collections=list(corpus_context.get("suggested_collections", [])),
        top_similar_items=list(corpus_context.get("top_similar_items", [])),
    )


def _refine_with_retry(
    llm: LLMClient,
    config: GoalsConfig,
    req: SummarizeRequest,
    paper_text: str,
    prefix: str,
) -> RefinedSummary:
    refine_prompt = _build_refine_prompt(config, req, paper_text)
    refined_text = to_text(llm.prompt(refine_prompt))
    try:
        return RefinedSummary.model_validate(extract_json_blob(refined_text))
    except ValueError:
        LOGGER.warning("%s refine JSON parse failed, retrying", prefix)
        retry_prompt = (
            "The following text contains a research analysis. Return a single valid JSON "
            "object with keys: executive_summary, should_deep_read, key_sections_to_read, "
            "relevance_to_research, controversial_points, industry_academy_impact, "
            "unknown_unknowns, implementation_quickstart, key_findings, methods, limitations. "
            "Return ONLY the JSON, no other text.\n\n" + refined_text
        )
        retry_text = to_text(llm.prompt(retry_prompt))
        return RefinedSummary.model_validate(extract_json_blob(retry_text))


def _run_triage(
    llm: LLMClient,
    config: GoalsConfig,
    req: SummarizeRequest,
    refined: RefinedSummary,
    corpus_context: dict[str, Any],
) -> TriageResult:
    triage_prompt = _build_triage_prompt(config, req, refined, corpus_context)
    triage = llm.pydantic_prompt(prompt=triage_prompt, pydantic_model=TriageResult)
    if not isinstance(triage, TriageResult):
        triage = TriageResult.model_validate(extract_json_blob(to_text(triage)))
    return triage


def _assemble_summary_response(
    refined: RefinedSummary,
    triage: TriageResult,
    composite_score: float,
    mapped_priority: str,
    corpus_context: dict[str, Any],
) -> SummarizeResponse:
    return SummarizeResponse(
        executive_summary=refined.executive_summary,
        should_deep_read=refined.should_deep_read,
        key_sections_to_read=refined.key_sections_to_read,
        relevance_to_research=refined.relevance_to_research,
        controversial_points=refined.controversial_points,
        industry_academy_impact=refined.industry_academy_impact,
        unknown_unknowns=refined.unknown_unknowns,
        implementation_quickstart=refined.implementation_quickstart,
        key_findings=refined.key_findings,
        methods=refined.methods,
        limitations=refined.limitations,
        relevance_score=triage.score,
        composite_relevance_score=composite_score,
        reading_priority=mapped_priority,
        tags=triage.tags,
        triage_rationale=triage.rationale,
        triage_dimensions=triage.dimensions,
        triage_confidence=triage.confidence,
        corpus_affinity_score=float(corpus_context.get("affinity_score", 0.0)),
        corpus_positive_similarity=float(corpus_context.get("positive_similarity", 0.0)),
        corpus_negative_similarity=float(corpus_context.get("negative_similarity", 0.0)),
        matched_goal=str(corpus_context.get("matched_goal", "") or ""),
        matched_goal_similarity=float(corpus_context.get("matched_goal_similarity", 0.0)),
        suggested_collections=list(corpus_context.get("suggested_collections", [])),
        top_similar_items=list(corpus_context.get("top_similar_items", [])),
    )


def run_pipeline(req: SummarizeRequest, log_prefix: str | None = None) -> SummarizeResponse:
    app_state = state()
    config: GoalsConfig = app_state.app_state.config
    llm: LLMClient = app_state.resolve_stage_client("feed")
    prefix = log_prefix or build_log_prefix(req)
    pipeline_started = perf_counter()

    log_context(prefix, "pipeline started pdf_path=%s", req.pdf_path)
    extract_started = perf_counter()
    raw_text = _extract_pdf_text(req.pdf_path)
    log_context(prefix, "pdf extracted chars=%d in %.2fs", len(raw_text), perf_counter() - extract_started)

    corpus_context = corpus.run_corpus_match(req, raw_text)
    log_context(
        prefix,
        "corpus stage has_corpus=%s affinity=%.3f positive=%.3f negative=%.3f matched_goal=%s",
        corpus_context.get("has_corpus"),
        corpus_context.get("affinity_score", 0.0),
        corpus_context.get("positive_similarity", 0.0),
        corpus_context.get("negative_similarity", 0.0),
        corpus_context.get("matched_goal", ""),
    )
    if (
        corpus_context.get("has_corpus")
        and float(corpus_context.get("affinity_score", 0.0)) < config.corpus.similarity_threshold
    ):
        log_context(prefix, "fast-rejected by corpus threshold=%.3f", config.corpus.similarity_threshold)
        return _fast_reject_response(req, corpus_context, raw_text)

    max_direct_chars = 80_000
    if len(raw_text) > max_direct_chars:
        log_context(prefix, "text too long (%d chars), splitting into 2 chunks", len(raw_text))
        mid = len(raw_text) // 2
        break_pos = raw_text.rfind("\n\n", mid - 2000, mid + 2000)
        if break_pos == -1:
            break_pos = mid
        chunk1, chunk2 = raw_text[:break_pos], raw_text[break_pos:]
        summary_prompt = (
            "Summarize the following chunk of an academic paper. "
            "Cover: main claims, methodology, results with numbers, limitations. "
            "Be thorough and factual.\n\n{text}"
        )
        log_context(prefix, "chunk 1 summary started chars=%d", len(chunk1))
        s1 = to_text(llm.prompt(summary_prompt.format(text=chunk1)))
        log_context(prefix, "chunk 2 summary started chars=%d", len(chunk2))
        s2 = to_text(llm.prompt(summary_prompt.format(text=chunk2)))
        paper_text = f"[Part 1 summary]\n{s1}\n\n[Part 2 summary]\n{s2}"
    else:
        paper_text = raw_text

    refine_prompt = _build_refine_prompt(config, req, paper_text)
    refine_started = perf_counter()
    log_context(prefix, "refine started prompt_chars=%d", len(refine_prompt))
    refined_text = to_text(llm.prompt(refine_prompt))
    LOGGER.debug("%s refine raw output (first 500 chars): %s", prefix, refined_text[:500])
    try:
        refined_data = extract_json_blob(refined_text)
    except ValueError:
        LOGGER.warning("%s refine JSON parse failed, retrying with extraction prompt", prefix)
        retry_prompt = (
            "The following text contains a research analysis. "
            "Extract the content and return it as a single valid JSON object with these keys: "
            "executive_summary, should_deep_read, key_sections_to_read, relevance_to_research, "
            "controversial_points, industry_academy_impact, unknown_unknowns, implementation_quickstart, "
            "key_findings, methods, limitations. "
            "Return ONLY the JSON object, no other text.\n\n" + refined_text
        )
        retry_text = to_text(llm.prompt(retry_prompt))
        try:
            refined_data = extract_json_blob(retry_text)
        except ValueError:
            LOGGER.error("%s refine retry parse failed raw_output=%s", prefix, retry_text[:2000])
            raise
    refined = RefinedSummary.model_validate(refined_data)
    log_context(prefix, "refine completed in %.2fs", perf_counter() - refine_started)

    triage_prompt = _build_triage_prompt(config, req, refined, corpus_context)
    triage_started = perf_counter()
    log_context(prefix, "triage started")
    triage = llm.pydantic_prompt(prompt=triage_prompt, pydantic_model=TriageResult)
    if not isinstance(triage, TriageResult):
        triage = TriageResult.model_validate(extract_json_blob(to_text(triage)))

    composite_score = scoring.compute_composite_score(triage, float(corpus_context.get("affinity_score", 0.0)))
    mapped_priority = scoring.map_priority_from_score(composite_score)
    log_context(
        prefix,
        "triage completed in %.2fs score=%s composite=%.2f priority=%s confidence=%.2f",
        perf_counter() - triage_started,
        triage.score,
        composite_score,
        mapped_priority,
        triage.confidence,
    )
    log_context(prefix, "pipeline completed in %.2fs", perf_counter() - pipeline_started)

    return _assemble_summary_response(refined, triage, composite_score, mapped_priority, corpus_context)
