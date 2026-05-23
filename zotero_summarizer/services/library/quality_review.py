"""Full-text, peer-review-style QUALITY assessment for the top-K Today picks.

Distinct from ``full_text_refine`` (which re-scores *relevance*): this fetches
the paper's PDF (arXiv / OA) and asks the reasoning LLM for a referee-grade
assessment — soundness / novelty / significance / reproducibility / clarity +
an overall grade + verdict — judged on the paper's own merits, INDEPENDENT of
the user's research goals.

Boundary contract: ``fetch_full_text`` returns ``None`` when no open-access PDF
is identifiable/fetchable (a real "no full text" outcome, not error masking).
Genuine errors (corrupt PDF, LLM failure) propagate to the caller — the feeds
background loop records them per-row rather than inventing a fake review.
"""
from __future__ import annotations

import logging
from typing import Any

from zotero_summarizer.integrations.pdf import PdfExtractor
from zotero_summarizer.integrations.pdf_fetch import fetch_pdf, resolve_pdf_url
from zotero_summarizer.models import GoalsConfig, PaperDigest, QualityReview

LOGGER = logging.getLogger(__name__)

# Fallback when goals.yaml has no `prompts.paper_digest`. Produces the user's
# condensed 7-point investigation + quality, personalised to their research
# goals. Brevity is enforced hard — the result is a note, not a wall of text.
_DEFAULT_DIGEST_PROMPT = (
    "You are an expert research assistant. Read the paper's full text and produce a "
    "CONDENSED, scannable digest the reader will skim months from now. Be terse: every "
    "text field is ONE short sentence (<=25 words); lists hold at most 3 brief bullets. "
    "No fluff, do not restate the title.\n\n"
    "The reader's research goals: {research_goals}\n\n"
    "Title: {title}\n\nFull text (may be truncated):\n{full_text}\n\n"
    "Return a single strict JSON object with these fields:\n"
    '- "tldr": one sentence on what the paper is about.\n'
    '- "read_decision": exactly one of "read", "skim", "skip".\n'
    '- "read_why": one short clause justifying the decision.\n'
    '- "read_parts": sections worth reading (<=3 short items; [] if skip).\n'
    '- "relevance": how it connects to the reader\'s research goals above (one sentence; "" if none).\n'
    '- "controversies": the most debatable claim and why (one sentence; "" if none).\n'
    '- "impact": likely effect on industry/academia (one sentence).\n'
    '- "unknown_unknowns": something useful the reader likely did not consider (one sentence).\n'
    '- "implementation": quickstart steps to apply its methods (<=3 short bullets; [] if n/a).\n'
    '- "grade": overall quality A (excellent)/B (solid)/C (borderline)/D (weak).\n'
    '- "soundness","novelty","significance","reproducibility","clarity": integers 1-5.\n'
    '- "key_strength","key_weakness": one short clause each.\n'
    '- "confidence": number 0-1.\n'
    "Single JSON object only, start {{ end }}."
)

# Fallback when goals.yaml has no `prompts.quality_review` (it normally does).
_DEFAULT_PROMPT = (
    "You are an expert peer reviewer. Judge the QUALITY of this paper from its "
    "full text, on its own merits, IGNORING any personal research relevance.\n\n"
    "Title: {title}\n\nFull text (may be truncated):\n{full_text}\n\n"
    "Score 1-5 each: soundness, novelty, significance, reproducibility, clarity. "
    "Assign grade A (excellent) / B (solid) / C (borderline) / D (weak).\n"
    'Output strict JSON: "grade","soundness","novelty","significance",'
    '"reproducibility","clarity" (1-5),"verdict","key_strength","key_weakness",'
    '"confidence" (0-1). Single JSON object only, start {{ end }}.'
)


def _not_assessed() -> QualityReview:
    """A placeholder review meaning 'no open-access full text was available'."""
    return QualityReview(grade="", basis="not_assessed")


def fetch_full_text(
    row: dict[str, Any],
    *,
    config: GoalsConfig,
    extractor: PdfExtractor,
    unpaywall: Any | None = None,
) -> str | None:
    """Resolve + fetch the OA PDF for a feed row and extract its text.

    ``None`` when no OA PDF is identifiable or the download fails (the
    documented "no full text" contract). Extraction errors propagate.
    """
    cfg = config.quality_review
    pdf_url = resolve_pdf_url(
        doi=(row.get("doi") or "").strip() or None,
        arxiv_id=(row.get("arxiv_id") or "").strip() or None,
        url=(row.get("url") or "").strip() or None,
        unpaywall=unpaywall,
    )
    if not pdf_url:
        return None
    pdf_path = fetch_pdf(
        pdf_url,
        max_bytes=int(cfg.max_pdf_bytes),
        timeout=float(cfg.fetch_timeout_secs),
    )
    if pdf_path is None:
        return None
    text = extractor.extract_text(pdf_path).strip()
    return text or None


def assess_quality(*, title: str, full_text: str, config: GoalsConfig, llm: Any) -> QualityReview:
    """Run the referee-grade review on the full text (must be non-empty)."""
    template = config.prompts.quality_review or _DEFAULT_PROMPT
    text = full_text[: int(config.quality_review.max_text_chars)]
    prompt = template.format(title=title or "Untitled", full_text=text)
    review = llm.pydantic_prompt(prompt=prompt, pydantic_model=QualityReview)
    return review.model_copy(update={"basis": "full_text"})


def assess_digest(*, title: str, full_text: str, config: GoalsConfig, llm: Any) -> PaperDigest:
    """Condensed paper digest (quality + the user's 7-point investigation) from
    the full text (must be non-empty). Personalised to ``config.research_goals``."""
    template = config.prompts.paper_digest or _DEFAULT_DIGEST_PROMPT
    text = full_text[: int(config.quality_review.max_text_chars)]
    goals = "; ".join(g for g in (config.research_goals or []) if str(g).strip()) or "(not specified)"
    prompt = template.format(title=title or "Untitled", full_text=text, research_goals=goals)
    digest = llm.pydantic_prompt(prompt=prompt, pydantic_model=PaperDigest)
    return digest.model_copy(update={"basis": "full_text"})


def review_row(
    row: dict[str, Any],
    *,
    config: GoalsConfig,
    llm: Any,
    extractor: PdfExtractor,
    unpaywall: Any | None = None,
) -> QualityReview:
    """Fetch the full text and assess it; ``not_assessed`` when no OA PDF."""
    text = fetch_full_text(row, config=config, extractor=extractor, unpaywall=unpaywall)
    if not text:
        return _not_assessed()
    return assess_quality(title=str(row.get("title") or ""), full_text=text, config=config, llm=llm)
