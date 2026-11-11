"""Deep-review enrichment layers, split out of ``deep_review`` to keep that
module under the 500-LOC cap.

``extra_layers`` runs the INDEPENDENTLY-SKIPPABLE layers that sit on top of the core
digest: section extraction, paper-type detection, reference-free quality eval,
goal-conditioned summaries and the code link. The three branches (paper type → quality,
goals, code link) share no data, so they run concurrently (local providers stay serial)
and may start BEFORE the digest is done — only the overstatement check reads it. The
review's provider budget (``sub_concurrency`` calls in flight) is enforced per attempt
in ``_review_attempt``, not here, so branches and their inner pools cannot exceed it. Quality failure remains explicitly
unassessed; other optional failures omit panels/boards — the broad excepts are the
background-worker boundary ``deep_review`` already uses.
"""
from __future__ import annotations

import contextvars
import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Any

from zotero_summarizer.models import QualityEval

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ExtraLayersCtx:
    item_key: str
    title: str
    pdf_path: str
    text: str
    llm: Any
    config: Any
    prestige: dict[str, Any] | None
    prestige_floor_value: float | None
    # Use the feed client for rubric/verification and section sub-calls.
    # None preserves the digest-client fallback.
    llm_light: Any = None
    reporter: Any = None
    lean_tier: bool = False
    sub_concurrency: int = 1
    item_type: str | None = None  # Zotero typeName — weak prior for paper-type detection
    web_article: bool = False  # text rendered from a web page → NON_PAPER (relevance-only)
    abstract: str = ""  # feed/Zotero abstract — paper-type input, so detection needs no digest
    max_chars: int | None = None  # per-stage body window (None: the tier's configured cap)
    digest_dump: dict[str, Any] | Callable[[], dict[str, Any]] | None = None  # a callable blocks until the digest is ready
    abort: threading.Event = field(default_factory=threading.Event)  # set when the digest failed: skip unstarted work


def _phase(reporter: Any, name: str, *, is_call: bool = False) -> None:
    """Report a pipeline phase that the caller is about to run (no-op without a reporter)."""
    if reporter is not None:
        reporter.phase(name, is_call=is_call)


def _traced(client: Any, stage: str) -> Any:
    """Every layer call is timed and token-counted under its own stage name (#132)."""
    from zotero_summarizer.services.library._review_attempt import observed_client

    return observed_client(client, stage)


def _run_branches(ctx: ExtraLayersCtx, branches: list[Callable[[], Any]]) -> list[Any]:
    """Run the independent branches and return their results in order: serial when the
    provider is serial, else one thread per branch (each task keeps the caller's
    contextvars so the attempt's stage timings and call budget still apply). Each branch handles
    its own failure; after ``ctx.abort`` an unstarted branch yields ``None``."""
    def guarded(branch: Callable[[], Any]) -> Any:
        return None if ctx.abort.is_set() else branch()

    if ctx.sub_concurrency <= 1 or len(branches) <= 1:
        return [guarded(branch) for branch in branches]
    with ThreadPoolExecutor(max_workers=min(ctx.sub_concurrency, len(branches)),
                            thread_name_prefix="review-layer") as pool:
        futures = [pool.submit(contextvars.copy_context().run, guarded, branch) for branch in branches]
        return [future.result() for future in futures]


def extra_layers(
    ctx: ExtraLayersCtx,
) -> tuple[dict[str, Any], list[dict[str, Any]] | None, dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    """Paper-type detection + reference-free quality eval + goal-conditioned summaries
    + the section overlay (findings located onto the paper's own sections) + the
    validated code-repository link.

    Each is optional: quality failure returns an explicit unassessed result;
    other failures omit panels/boards without blocking the digest. The broad excepts are
    the background-worker boundary the rest of this module already uses."""
    from pathlib import Path

    sections: list[dict[str, Any]] = []
    body = ctx.text
    link_uris: list[str] = []
    try:
        from zotero_summarizer.services.library import _paper_read_pdf
        content = _paper_read_pdf.extract_pdf_content(Path(ctx.pdf_path), use_docling=ctx.config.quality_review.use_docling)
        sections = content.get("sections") or []  # layout only: the body every stage reads is ctx.text
        link_uris = content.get("link_uris") or []
    except Exception as exc:  # noqa: BLE001 — section extraction is best-effort enrichment
        LOGGER.warning("section extraction for %s failed: %s", ctx.item_key, exc)

    qr = ctx.config.quality_review
    # The rubric yes/no checks, overstatement, and self-verify are known-cheap by
    # identity — ride the light (feed) model, not the digest's reasoning model.
    llm_light = ctx.llm_light or ctx.llm

    def quality_branch() -> tuple[dict[str, Any], dict[str, Any]]:
        from zotero_summarizer.services.library import paper_type as _pt
        if ctx.abort.is_set():
            return {}, {}
        _phase(ctx.reporter, "paper_type", is_call=True)
        paper_type_dump = _pt.detect_safe(
            replace(ctx, llm=_traced(ctx.llm, "paper_type")), sections, body, LOGGER)
        runs = 1 if ctx.config.review_depth == "fast" else int(qr.lean_self_consistency_runs if ctx.lean_tier else qr.self_consistency_runs)
        max_chars = ctx.max_chars or int(qr.lean_max_text_chars if ctx.lean_tier else qr.max_text_chars)
        try:
            from zotero_summarizer.services.library import quality_eval
            from zotero_summarizer.services.llm import jev_engine
            ps = ctx.prestige or {}
            quality = quality_eval.evaluate_quality(
                title=ctx.title, full_text=body, sections=sections, digest=ctx.digest_dump,
                llm=_traced(llm_light, "quality"), max_chars=max_chars,
                # Uncertain detection still names the safe supertype: grade on its generic checklist.
                paper_type=paper_type_dump.get("type") or None,
                prestige_score=ps.get("prestige"), prestige_known=bool(ps.get("prestige_known")),
                prestige_floor=ctx.prestige_floor_value, self_consistency_runs=runs,
                shadow_claim_check=bool(getattr(qr, "shadow_claim_check", False)),
                claim_check_model=str(getattr(qr, "claim_check_model", "flan-t5-large")),
                reporter=ctx.reporter, sub_concurrency=ctx.sub_concurrency, self_verification=bool(qr.self_verification),
                jev_target=jev_engine.resolve(ctx.config),
            )
            quality.review_depth = str(ctx.config.review_depth)
            if ctx.config.checklist_engine != "off" and quality.jev is None:  # engine on but unusable (no key / offline): the LLM rubric ran alone
                quality.jev = {"engine": ctx.config.checklist_engine, "status": "unavailable"}
            quality.type_uncertain = bool(paper_type_dump.get("uncertain")) and quality.basis == "full_text"
            quality_dump = quality.model_dump()
        except Exception as exc:  # noqa: BLE001 — independently-skippable layer (design)
            if ctx.abort.is_set():  # the digest failed: its error is the one to report
                return paper_type_dump, {}
            LOGGER.warning("quality eval for %s failed: %s", ctx.item_key, exc)
            quality_dump = QualityEval(quality_band="uncertain", basis="not_assessed").model_dump()
        return paper_type_dump, quality_dump

    def goals_branch() -> list[dict[str, Any]] | None:
        try:
            goals = [g for g in (ctx.config.research_goals or []) if str(g).strip()]
            if not goals:
                return None
            from zotero_summarizer.services.library import _paper_goal_summaries
            # Goal batching is a LEAN-tier optimization (6→1 call to spare prefill on a
            # slow backend). On the full tier (MLX) keep per-goal calls — each goal gets
            # the model's full attention, higher quality. Match the CLI's gating. (#135
            # bench: batching on the full tier turned 2 of 24 hits into misses.)
            batch = bool(ctx.lean_tier and getattr(qr, "batch_goal_summaries", True))
            summaries = _paper_goal_summaries.summarize_for_goals(
                goals=goals, sections=sections, full_text=body, llm=_traced(ctx.llm, "goals"),
                reporter=ctx.reporter, batch=batch, sub_concurrency=ctx.sub_concurrency,
            )
            if ctx.reporter is not None:
                ctx.reporter.finish("goals")
            return [g.model_dump() for g in summaries]
        except Exception as exc:  # noqa: BLE001 — independently-skippable layer (design)
            LOGGER.warning("goal summaries for %s failed: %s", ctx.item_key, exc)
            return None

    def code_branch() -> dict[str, Any] | None:
        # The paper's OWN code repo, extracted from the full text + validated (live +
        # relates-to-paper). A network/parse failure degrades to no banner. The harvested
        # hyperlink-annotation URIs catch a github link that is only a clickable annotation
        # (they carry no availability phrase, so it may render "unverified" — still found).
        try:
            from zotero_summarizer.services.library import _code_link
            return _code_link.find_code_link(body + "\n" + "\n".join(link_uris), ctx.title)
        except Exception as exc:  # noqa: BLE001 — independently-skippable layer (design)
            LOGGER.warning("code link for %s failed: %s", ctx.item_key, exc)
            return None

    quality_result, goal_dump, code_link = _run_branches(ctx, [quality_branch, goals_branch, code_branch])
    paper_type_dump, quality_dump = quality_result or ({}, {})  # None/empty only after a digest failure

    # Section overlay: locate the findings just computed onto the SAME `sections`
    # they were grounded against (stable id join, no cross-extractor mismatch). Pure
    # assembly; section one-liners are NOT built here — they are generated when the
    # paper is opened (``section_summaries_job``), so the map starts as titles + pages.
    section_overlay: dict[str, Any] | None = None
    try:
        from zotero_summarizer.services.library import _review_section_overlay
        section_overlay = _review_section_overlay.build_section_overlay(sections, quality_dump, goal_dump)
        if section_overlay and not section_overlay.get("degraded"):
            # Log the per-review localization operating point — accumulated across the
            # real corpus, these INFO lines are how the chip-confidence threshold gets
            # CALIBRATED (exact/fuzzy = asserted, approx = the low-confidence fallback).
            loc = _review_section_overlay.localization_stats(section_overlay)
            LOGGER.info(
                "[overlay %s] located %.0f%% of %d findings: exact=%d fuzzy=%d approx=%d unplaced=%d",
                ctx.item_key, loc["located_rate"] * 100, loc["total"],
                loc["exact"], loc["fuzzy"], loc["approx"], loc["unplaced"],
            )
    except Exception as exc:  # noqa: BLE001 — independently-skippable layer (design)
        LOGGER.warning("section overlay for %s failed: %s", ctx.item_key, exc)

    return quality_dump, goal_dump, paper_type_dump, section_overlay, code_link


def digest_with_layers(compute_digest: Callable[[], Any], ctx: ExtraLayersCtx) -> tuple[Any, tuple]:
    """``(digest, extra_layers(...))``: the layers need the digest only for the
    overstatement check, so with a concurrent provider they start alongside it and the
    review takes the longer of the two chains; a serial provider runs the digest first.
    ``compute_digest`` returns the ``PaperDigest``; a failure aborts the unstarted work."""
    ready: Future = Future()
    ctx.digest_dump = ready.result

    def digest_and_publish() -> Any:
        try:
            digest = compute_digest()
            ready.set_result(digest.model_dump())
            return digest
        except BaseException as exc:
            ctx.abort.set()
            if not ready.done():
                ready.set_exception(exc)  # never leave the quality branch waiting
            raise

    if ctx.sub_concurrency <= 1:
        digest = digest_and_publish()
        return digest, extra_layers(ctx)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="review-layers") as pool:
        layers = pool.submit(contextvars.copy_context().run, extra_layers, ctx)
        digest = digest_and_publish()
        return digest, layers.result()


__all__ = ["ExtraLayersCtx", "digest_with_layers", "extra_layers"]
