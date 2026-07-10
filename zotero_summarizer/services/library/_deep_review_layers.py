"""Deep-review enrichment layers, split out of ``deep_review`` to keep that
module under the 500-LOC cap.

``extra_layers`` runs the three INDEPENDENTLY-SKIPPABLE layers that sit on top of
the core digest: section extraction, paper-type detection, reference-free quality
eval, and goal-conditioned summaries. Each failure degrades to no panel / no board
rather than blocking the digest — the broad excepts are the background-worker
boundary ``deep_review`` already uses.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ExtraLayersCtx:
    item_key: str
    title: str
    pdf_path: str
    text: str
    digest_dump: dict[str, Any]
    llm: Any
    config: Any
    prestige: dict[str, Any] | None
    prestige_floor_value: float | None
    # Cheap tier for the known-trivial sub-calls (rubric yes/no, overstatement,
    # self-verify, section one-liners) — the digest's strong reasoning model is
    # wasted on them (SOTA static per-identity tiering, claude-code model-tier-routing).
    # ponytail: rides the feed model (llm_map); add a dedicated deep_review.light_model
    # config only if a user needs feed != sub-call model. None → falls back to `llm`.
    llm_light: Any = None
    reporter: Any = None
    lean_tier: bool = False
    sub_concurrency: int = 1
    item_type: str | None = None  # Zotero typeName — weak prior for paper-type detection
    web_article: bool = False  # text rendered from a web page → NON_PAPER (relevance-only)


def extra_layers(
    ctx: ExtraLayersCtx,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None, dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    """Paper-type detection + reference-free quality eval + goal-conditioned summaries
    + the section overlay (findings located onto the paper's own sections) + the
    validated code-repository link.

    Each is an INDEPENDENTLY-SKIPPABLE layer (per the design): a failure degrades
    to no panel / no board rather than blocking the digest. The broad excepts are
    the background-worker boundary the rest of this module already uses."""
    from pathlib import Path

    sections: list[dict[str, Any]] = []
    body = ctx.text
    link_uris: list[str] = []
    try:
        from zotero_summarizer.services.library import _paper_read_pdf
        content = _paper_read_pdf.extract_pdf_content(Path(ctx.pdf_path), use_docling=ctx.config.quality_review.use_docling)
        sections = content.get("sections") or []
        body = str(content.get("full_text") or ctx.text)
        link_uris = content.get("link_uris") or []
    except Exception as exc:  # noqa: BLE001 — section extraction is best-effort enrichment
        LOGGER.warning("section extraction for %s failed: %s", ctx.item_key, exc)

    from zotero_summarizer.services.library import paper_type as _pt
    paper_type_dump = _pt.detect_safe(ctx, sections, body, LOGGER)

    qr = ctx.config.quality_review
    runs = int(qr.lean_self_consistency_runs if ctx.lean_tier else qr.self_consistency_runs)
    max_chars = int(qr.lean_max_text_chars if ctx.lean_tier else qr.max_text_chars)
    # The rubric yes/no checks, overstatement, and self-verify are known-cheap by
    # identity — ride the light (feed) model, not the digest's reasoning model.
    llm_light = ctx.llm_light or ctx.llm
    quality_dump: dict[str, Any] | None = None
    try:
        from zotero_summarizer.services.library import quality_eval
        ps = ctx.prestige or {}
        quality = quality_eval.evaluate_quality(
            title=ctx.title, full_text=body, sections=sections, digest=ctx.digest_dump,
            llm=llm_light, max_chars=max_chars, paper_type=paper_type_dump.get("type"),
            prestige_score=ps.get("prestige"), prestige_known=bool(ps.get("prestige_known")),
            prestige_floor=ctx.prestige_floor_value, self_consistency_runs=runs,
            shadow_claim_check=bool(getattr(qr, "shadow_claim_check", False)),
            claim_check_model=str(getattr(qr, "claim_check_model", "flan-t5-large")),
            reporter=ctx.reporter, sub_concurrency=ctx.sub_concurrency, self_verification=bool(qr.self_verification),
        )
        quality_dump = quality.model_dump()
    except Exception as exc:  # noqa: BLE001 — independently-skippable layer (design)
        LOGGER.warning("quality eval for %s failed: %s", ctx.item_key, exc)

    goal_dump: list[dict[str, Any]] | None = None
    try:
        goals = [g for g in (ctx.config.research_goals or []) if str(g).strip()]
        if goals:
            from zotero_summarizer.services.library import _paper_goal_summaries
            # Goal batching is a LEAN-tier optimization (6→1 call to spare prefill on a
            # slow backend). On the full tier (MLX) keep per-goal calls — each goal gets
            # the model's full attention, higher quality. Match the CLI's gating.
            batch = bool(ctx.lean_tier and getattr(qr, "batch_goal_summaries", True))
            summaries = _paper_goal_summaries.summarize_for_goals(
                goals=goals, sections=sections, full_text=body, llm=ctx.llm,
                reporter=ctx.reporter, batch=batch, sub_concurrency=ctx.sub_concurrency,
            )
            goal_dump = [g.model_dump() for g in summaries]
    except Exception as exc:  # noqa: BLE001 — independently-skippable layer (design)
        LOGGER.warning("goal summaries for %s failed: %s", ctx.item_key, exc)

    # Per-section "what it covers" one-liners for the story page's Paper map — ONE
    # grounded call, FULL tier only (the lean local path deliberately minimizes call
    # count, so the map there shows titles+pages without summaries). Skippable.
    section_summaries: dict[str, str] = {}
    if not ctx.lean_tier:
        try:
            from zotero_summarizer.services.library import _paper_section_summaries
            # Section one-liners are cheap-by-identity → light (feed) model like the rubric.
            section_summaries = _paper_section_summaries.summarize_sections(sections, llm_light)
        except Exception as exc:  # noqa: BLE001 — independently-skippable layer (design)
            LOGGER.warning("section summaries for %s failed: %s", ctx.item_key, exc)

    # Section overlay: locate the findings just computed onto the SAME `sections`
    # they were grounded against (stable id join, no cross-extractor mismatch). Pure
    # assembly; an independently-skippable layer like the three above.
    section_overlay: dict[str, Any] | None = None
    try:
        from zotero_summarizer.services.library import _review_section_overlay
        section_overlay = _review_section_overlay.build_section_overlay(
            sections, quality_dump, goal_dump, section_summaries=section_summaries,
        )
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

    # The paper's OWN code repo, extracted from the full text + validated (live +
    # relates-to-paper). Skippable like the layers above; a network/parse failure
    # degrades to no banner, never blocks the digest.
    code_link: dict[str, Any] | None = None
    try:
        from zotero_summarizer.services.library import _code_link
        # Append the harvested hyperlink-annotation URIs so a github link that's only
        # a clickable annotation (never in the visible text) is caught. They carry no
        # availability phrase, so such a link may render "unverified" (grey) — still found.
        code_text = body + "\n" + "\n".join(link_uris)
        code_link = _code_link.find_code_link(code_text, ctx.title)
    except Exception as exc:  # noqa: BLE001 — independently-skippable layer (design)
        LOGGER.warning("code link for %s failed: %s", ctx.item_key, exc)
    return quality_dump, goal_dump, paper_type_dump, section_overlay, code_link


__all__ = ["ExtraLayersCtx", "extra_layers"]
