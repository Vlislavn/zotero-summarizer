"""Budget-aware full-text selector for the deep-review digest and rubric phases.

Replaces the naive ``full_text[:budget]`` slice used by ``quality_review.assess_digest``
and ``quality_eval.evaluate_quality``. Instead of silently dropping the tail
(which typically contains Limitations, Conclusion, and Discussion — exactly what a
peer referee needs), this module:

0. Returns the whole text unchanged when it already fits the budget (the common
   case) — byte-identical to the old truncation, so only papers that would have
   been truncated change at all.
1. Shares the budget between referee-critical section prefixes in document order,
   reserving space for separators.
2. Fills any remaining budget with ``PaperChunkIndex`` BM25-ranked chunks for
   the supplied ``queries`` (rubric dimension names, research goals), again in
   document order to preserve narrative coherence.

Call sites pass ``sections`` from ``_paper_read_pdf.extract_pdf_content``; when
``sections`` is empty the function skips step 1 and goes straight to step 2.
Errors propagate (no error-masking): the only place this runs is on over-budget
papers, and a genuine chunk-index failure is a signal, not something to hide
behind a silent prefix slice. The digest caller runs it directly; the quality
caller is already inside ``_deep_review_layers.extra_layers``' skippable-layer boundary.
"""
from __future__ import annotations

import re
from typing import Any

# NB: ``PaperChunkIndex`` is imported lazily inside ``_fill_from_chunks`` — a
# top-level import would pull in ``faithbench/__init__``, and faithbench's
# ``_build_claims`` imports ``library.quality_review`` which imports THIS module,
# a partially-initialized circular import. Keeping this a leaf module at import
# time lets both ``quality_review`` (loaded during faithbench init) and
# ``quality_eval`` import it freely. See CLAUDE.md's note on the lazy cycle.

# Section titles that a peer reviewer always needs: guaranteed to appear if
# present in the PDF and within budget. Listed in typical paper order so
# the stitched text reads naturally.
_HIGH_VALUE_RE = re.compile(
    r"^\s*(Abstract|Introduction|Background|Related\s+Work|Methods?|Methodology|"
    r"Approach|Experiments?|Results?|Discussion|Limitations?|Conclusion|"
    r"Future\s+Work|Ablation)\s*$",
    re.IGNORECASE,
)

# Exclude these from prioritized sections; ranked filler still uses full text.
_SKIP_RE = re.compile(
    r"^\s*(References?|Acknowledg(?:e)?ments?|Appendix)\s*$",
    re.IGNORECASE,
)


def _is_high_value(title: str) -> bool:
    return bool(_HIGH_VALUE_RE.match(title or ""))


def _is_skip(title: str) -> bool:
    return bool(_SKIP_RE.match(title or ""))


def select_review_text(
    sections: list[dict[str, Any]],
    full_text: str,
    *,
    budget: int,
    queries: list[str] | None = None,
) -> str:
    """Return at most ``budget`` characters of the paper, selected to maximise
    referee coverage.

    ``sections`` — list of ``{title, text, page}`` dicts from
        ``_paper_read_pdf.extract_pdf_content``; may be empty.
    ``full_text`` — raw concatenated text (fallback source for chunk ranking).
    ``budget`` — character cap (same as ``max_text_chars`` / ``max_chars``).
    ``queries`` — optional ranking queries for filling leftover budget; defaults
        to a minimal rubric dimension set when omitted.
    """
    if budget <= 0:
        return ""

    # Preserve short papers byte-for-byte, regardless of parsed sections.
    if len(full_text) <= budget:
        return full_text

    if not sections:
        return _fill_from_chunks(full_text, budget, queries or _DEFAULT_QUERIES)

    # Step 1: high-value sections, allocated by EQUAL-SHARE water-filling so an
    # early long section (e.g. a 30k Introduction) can't crowd out the critical
    # tail (Limitations/Conclusion). Short sections release their unused share.
    selected = [
        (f"[{t}]\n{x}" if t else x)
        for sec in sections
        for t in [str(sec.get("title") or "")]
        for x in [str(sec.get("text") or "").strip()]
        if x and not _is_skip(t) and _is_high_value(t)
    ]
    allocations = _water_fill(selected, budget - 2 * max(0, len(selected) - 1))
    kept = [chunk[:cap] for chunk, cap in zip(selected, allocations) if cap > 0]
    context = "\n\n".join(kept)
    if not context:
        return _fill_from_chunks(full_text, budget, queries or _DEFAULT_QUERIES)

    # Step 2: any leftover budget (sections shorter than their share) is filled
    # with BM25-ranked chunks of the whole text for extra coverage.
    remaining = budget - len(context) - 2
    if remaining > 0:
        filler = _fill_from_chunks(full_text, remaining, queries or _DEFAULT_QUERIES)
        if filler:
            context += "\n\n" + filler

    return context


def _water_fill(chunks: list[str], budget: int) -> list[int]:
    """Per-chunk character allocations summing to <= ``budget``, giving every chunk
    an equal share, redistributing short-chunk slack and integer remainders.
    The returned allocations retain input order."""
    alloc = [0] * len(chunks)
    remaining = max(0, budget)
    for rank, i in enumerate(sorted(range(len(chunks)), key=lambda i: len(chunks[i]))):
        alloc[i] = min(len(chunks[i]), remaining // (len(chunks) - rank))
        remaining -= alloc[i]
    return alloc


_DEFAULT_QUERIES = [
    "methodology study design evaluation",
    "results findings metrics performance",
    "limitations weaknesses future work",
    "conclusion contribution novelty",
]


def _fill_from_chunks(text: str, budget: int, queries: list[str]) -> str:
    """Fit ranked chunks (including a final prefix), then restore document order.

    Separators count toward the cap; a remainder too small for a separator and
    one source character is left unused, never filled with synthetic padding.
    """
    if not text or budget <= 0:
        return ""

    from zotero_summarizer.services.faithbench._corpus import PaperChunkIndex, _clip_chunks

    index = PaperChunkIndex(text)
    chunks = index.chunks  # document order
    if not chunks:
        return text[:budget]

    # Rank by the combined query, then order chunk INDICES by relevance (best BM25
    # rank first; unranked chunks keep document order after the ranked ones). Working
    # in INDEX space — not string identity — is essential: chunks overlap and can be
    # byte-identical, so a set of strings would mis-count the budget and re-emit
    # duplicates. Each index is counted and emitted exactly once.
    ranked = index.top_chunks(" ".join(queries), k=len(chunks))
    rank_of: dict[str, int] = {}
    for r, chunk in enumerate(ranked):
        rank_of.setdefault(chunk, r)
    by_relevance = sorted(
        range(len(chunks)),
        key=lambda i: (rank_of.get(chunks[i], len(ranked) + i)),
    )

    kept = _clip_chunks([chunks[i] for i in by_relevance], budget, separator="\n\n")
    return "\n\n".join(chunk for _, chunk in sorted(zip(by_relevance, kept)))
