"""Targeted deep review — the query-lens brief for a selected candidate (spec §5
step 7d, §13.4 #10). Composes the existing READ-ONLY library layers directly:

    assess_digest      → the goal-aligned brief (focus_prompt = the query)
    summarize_for_goals → grounded query-lens + one grounded answer per question

We do NOT wrap ``deep_review.start`` (spec §13.4 #2): it serves cached corpus
reviews, writes a note back to Zotero, and is lensed on the standing goals — none
of which fits an ephemeral, query-scoped session over a non-corpus paper. Reusing
these two leaf layers gets the same faithfulness (abstain-on-miss grounding) with
no Zotero write to suppress and the query as the lens.
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.models import GoalsConfig
from zotero_summarizer.services.library._paper_goal_summaries import summarize_for_goals
from zotero_summarizer.services.library.quality_review import assess_digest
from zotero_summarizer.services.search._models import Candidate

# Brief fields worth carrying into the session card (the rest of PaperDigest is
# quality scoring already surfaced by the light-review tier).
_BRIEF_FIELDS = (
    "tldr", "executive_summary", "key_findings", "read_decision", "read_why",
    "methods", "limitations", "key_strength", "key_weakness", "grade",
)


def _summary_row(summary: Any) -> dict[str, Any]:
    return {
        "text": summary.summary or "",
        "relevant": summary.relevant,
        "retrieval_state": summary.retrieval_state,
        "abstained": summary.abstained,
        "supporting_quotes": list(summary.supporting_quotes),
        "key_sections": list(summary.key_sections),
    }


def targeted_review(
    cand: Candidate,
    *,
    full_text: str,
    sections: list[dict[str, Any]],
    query: str,
    questions: list[str],
    config: GoalsConfig,
    llm: Any,
) -> None:
    """Fill ``cand.review`` with a query-lensed brief + grounded answers (mutates
    in place). No full text → an explicit ``needs_full_text`` review, never a
    fabricated one (spec §9 / faithbench abstention contract)."""
    if not (full_text or "").strip():
        cand.review = {"state": "needs_full_text"}
        return

    digest = assess_digest(
        title=cand.title, full_text=full_text, config=config, llm=llm, focus_prompt=query,
    )
    brief = {k: getattr(digest, k) for k in _BRIEF_FIELDS}

    goals = [query] + [q for q in (questions or []) if str(q).strip()]
    summaries = summarize_for_goals(
        goals=goals, sections=sections or [], full_text=full_text, llm=llm,
    )
    lens = _summary_row(summaries[0]) if summaries else {}
    answers = [
        {"question": q, **_summary_row(s)}
        for q, s in zip(questions or [], summaries[1:])
    ]

    cand.review = {"state": "reviewed", "brief": brief, "query_lens": lens, "question_answers": answers}


__all__ = ["targeted_review"]
