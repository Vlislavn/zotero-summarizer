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


def assess_digest(*, title: str, full_text: str, config: GoalsConfig, llm: Any) -> PaperDigest:
    """Condensed paper digest (quality + the user's 7-point investigation) from
    the full text (must be non-empty). Personalised to ``config.research_goals``."""
    template = config.prompts.paper_digest or _DEFAULT_DIGEST_PROMPT
    text = full_text[: int(config.quality_review.max_text_chars)]
    goals = "; ".join(g for g in (config.research_goals or []) if str(g).strip()) or "(not specified)"
    prompt = template.format(title=title or "Untitled", full_text=text, research_goals=goals)
    digest = llm.pydantic_prompt(prompt=prompt, pydantic_model=PaperDigest)
    return digest.model_copy(update={"basis": "full_text"})
