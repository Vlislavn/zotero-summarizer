"""Shared quote-grounding contract for Q&A, goal summaries and quality eval.

A model-supplied supporting quote counts as grounded only when it is a
*substantial verbatim span* of the supplied context — not a single common word
that trivially substring-matches a hallucinated answer. Centralising the rule
here keeps all three consumers on ONE contract.
"""
from __future__ import annotations

from typing import Any

# A grounded quote must clear BOTH floors AND be a whitespace-normalized
# verbatim substring of the context.
MIN_QUOTE_WORDS = 6
MIN_QUOTE_CHARS = 40


def quote_is_grounded(quote: Any, context: str) -> bool:
    """True iff ``quote`` is a long-enough verbatim span of ``context``."""
    if quote is None:
        return False
    normalized_quote = " ".join(str(quote).split())
    if len(normalized_quote) < MIN_QUOTE_CHARS or len(normalized_quote.split()) < MIN_QUOTE_WORDS:
        return False
    normalized_context = " ".join((context or "").split())
    return normalized_quote in normalized_context
