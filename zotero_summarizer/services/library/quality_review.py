"""Condensed paper-digest assessment for the deep-review path.

Fetches nothing itself — the caller (``services.library.deep_review``) supplies the
already-extracted full text and asks the reasoning LLM for a condensed, scannable
digest (the user's 7-point investigation + a referee-grade quality call), judged on
the paper's own merits and personalised to the user's research goals.

Genuine errors (LLM failure, malformed JSON) propagate to the caller — the feeds
background loop records them per-row rather than inventing a fake review.
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.models import GoalsConfig, PaperDigest
from zotero_summarizer.services._common import extract_json_blob, to_text
from zotero_summarizer.services.library._review_text import select_review_text

# Fallback when goals.yaml has no `prompts.paper_digest`. A referee-grade digest
# (NeurIPS/ICLR rubric) condensed into scannable fields, personalised to the
# reader's goals. Brevity is enforced hard — a note, not a wall of text — and
# every judgement must be grounded in the paper's own evidence (no confabulation).
_DEFAULT_DIGEST_PROMPT = (
    "You are a meticulous peer reviewer (NeurIPS/ICLR calibre). Read the paper's full "
    "text and produce a CONDENSED, scannable referee digest the reader will skim months "
    "from now.\n\n"
    "### ANTI-FABRICATION — the single most important rule\n"
    "Write ONLY numbers, percentages, metrics, dataset names, URLs and named entities that "
    "appear VERBATIM in the paper text below. Before you write ANY number or percentage, "
    "confirm it is literally present in the text; if it is not there, DO NOT write it. "
    "If the paper reports no quantitative results (e.g. a perspective/position paper), then "
    "`key_findings` MUST be qualitative or empty — NEVER invent statistics like '45%' or "
    "'2x' to fill it. A digest that leaves a field empty is GOOD; a digest with even one "
    "invented number, link, or result is a FAILURE worse than an empty one. When unsure "
    "whether a detail is in the paper, omit it.\n\n"
    "Ground EVERY judgement in the paper's own evidence; if the text does not support a "
    "field, leave it empty (\"\") or neutral. Be terse: each text field is ONE short sentence "
    "(<=25 words); lists hold at most 3 brief bullets. No fluff, do not restate the title.\n\n"
    "The reader's research goals: {research_goals}\n\n"
    "Title: {title}\n\nFull text (may be truncated):\n{full_text}\n\n"
    "Review like a referee: pin down the main CONTRIBUTION, weigh the EVIDENCE behind "
    "the central claim, compare NOVELTY against prior work, and judge whether the "
    "experiments actually support the conclusions. Then return a single strict JSON "
    "object with these fields:\n"
    '- "tldr": the paper\'s main contribution in one sentence.\n'
    '- "verdict": a one-line referee summary judgement (what it does + how convincingly).\n'
    '- "read_decision": exactly one of "read", "skim", "skip".\n'
    '- "read_why": one short clause justifying the decision.\n'
    '- "read_parts": sections/figures worth reading (<=3 short items; [] if skip).\n'
    '- "relevance": how it connects to the reader\'s research goals above (one sentence; "" if none).\n'
    '- "controversies": the most debatable claim and why it is contestable (one sentence; "" if none).\n'
    '- "impact": likely effect on the field if the claims hold (one sentence).\n'
    '- "unknown_unknowns": a useful implication the reader likely did not consider (one sentence).\n'
    '- "implementation": concrete steps to apply or reproduce the method (<=3 bullets; [] if n/a).\n'
    '- "executive_summary": 3-5 sentence neutral overview — what the paper is, its contribution, the headline result.\n'
    '- "key_findings": up to 5 concrete findings, each with its metric/number when stated (list of short strings).\n'
    '- "methods": dataset provenance, preprocessing, architecture and training setup, in one sentence.\n'
    '- "limitations": the stated limitations and the most material gaps, in one sentence.\n'
    '- "industry_impact": likely effect on industry/practice in one short line ("" if none).\n'
    '- "academy_impact": likely effect on academic research in one short line ("" if none).\n'
    '- "key_strength": the single strongest aspect, grounded in the text (one clause).\n'
    '- "key_weakness": the single most material limitation or threat to validity (one clause).\n'
    '- "grade": overall quality A (excellent)/B (solid)/C (borderline)/D (weak).\n'
    '- "soundness": integer 1-5 — are methods and claims supported by the evidence?\n'
    '- "novelty": integer 1-5 — new versus prior work.\n'
    '- "significance": integer 1-5 — importance of the problem and result.\n'
    '- "reproducibility": integer 1-5 — code/data/detail sufficient to reproduce.\n'
    '- "clarity": integer 1-5 — how clearly it is written.\n'
    '- "confidence": number 0-1 — your confidence in this assessment.\n'
    "Single JSON object only, start {{ end }}."
)


def assess_digest(
    *, title: str, full_text: str, config: GoalsConfig, llm: Any, focus_prompt: str = "",
    max_chars: int | None = None,
) -> PaperDigest:
    """Condensed paper digest (quality + the user's 7-point investigation) from
    the full text (must be non-empty). Personalised to ``config.research_goals``.
    When ``focus_prompt`` is set, the reviewer emphasises or de-emphasises aspects
    the user highlighted before running the review. ``max_chars`` overrides the
    text cap (the deep_review orchestrator passes a smaller cap for the local tier)."""
    template = config.prompts.paper_digest or _DEFAULT_DIGEST_PROMPT
    cap = int(max_chars if max_chars is not None else config.quality_review.max_text_chars)
    # Budget-aware selection instead of a blind prefix slice. ``full_text`` here is
    # the onprem-markdown extraction (a different backend than the fitz section
    # parser), so we curate over it WITHOUT section hints — chunk-ranking keeps the
    # digest 100% onprem-sourced and self-consistent. Byte-identical to
    # ``full_text[:cap]`` for papers that fit; relevant chunks (not a blind prefix)
    # for those that exceed the cap (see services/library/_review_text.py).
    text = select_review_text([], full_text, budget=cap)
    goals = "; ".join(g for g in (config.research_goals or []) if str(g).strip()) or "(not specified)"
    prompt = template.format(title=title or "Untitled", full_text=text, research_goals=goals)
    if focus_prompt:
        prompt += (
            f"\n\nReader's focus note: {focus_prompt}\n"
            "Please adjust your review emphasis to highlight or downplay aspects matching this focus."
        )
    digest = llm.pydantic_prompt(prompt=prompt, pydantic_model=PaperDigest)
    if not isinstance(digest, PaperDigest):
        # onprem returns the raw (often empty) string when its own parser can't
        # build the model. Salvage with the stronger 3-strategy extractor
        # (markdown-fenced / prose-embedded JSON), mirroring services/triage/
        # summarization. If THAT fails too (a truly empty completion) model_validate
        # RAISES — caught at deep_review's per-item boundary and surfaced, instead of
        # the opaque `'str' object has no attribute 'model_copy'` this once produced.
        digest = PaperDigest.model_validate(extract_json_blob(to_text(digest)))
    return digest.model_copy(update={"basis": "full_text"})
