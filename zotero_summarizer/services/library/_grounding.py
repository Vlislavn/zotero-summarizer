"""Shared quote-grounding contract for Q&A, goal summaries and quality eval.

A model-supplied supporting quote counts as grounded only when its content is
genuinely present in the supplied context — not a single common word that
trivially substring-matches a hallucinated answer. Centralising the rule here
keeps all consumers on ONE module.

Two strictness levels, by risk profile:

* **strict (default)** — a whitespace-normalized *verbatim* substring. This is
  the safety-critical bar for user-facing factual answers (``library.qa``, goal
  summaries): an ungrounded answer is a hallucination, so we demand an exact
  span. This is the bar the faithbench abstention/grounding benchmark validates;
  it is deliberately left UNCHANGED.
* **fuzzy (opt-in, ``fuzzy=True``)** — tolerant of paraphrase / OCR ligature
  drift: the quote's tokens must appear as near-contiguous matching runs covering
  ``FUZZY_MATCH_RATIO`` of the quote. Used by the quality CHECKLIST, where the
  verdict is a soft coverage judgment and a model that *correctly* identifies a
  met criterion but paraphrases its evidence (the common case for smaller models)
  must still count — while a hallucinated quote, whose tokens do not form a
  contiguous run in the body, is still rejected. NFKC-normalized so a model's
  plain ``fit`` matches a PDF's ``ﬁt`` ligature.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any

# A grounded quote must clear BOTH floors before either match is attempted.
MIN_QUOTE_WORDS = 6
MIN_QUOTE_CHARS = 40
# Fuzzy: fraction of the quote's tokens that must be covered by contiguous
# matching runs against the context. 0.8 grounds paraphrase/word-drop/reorder
# but rejects scattered common-word overlap (the anti-fabrication property,
# verified against real CheXNet quotes + hallucinated controls, 2026-06).
FUZZY_MATCH_RATIO = 0.8

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _content_tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(unicodedata.normalize("NFKC", text).lower())


def quote_is_grounded(quote: Any, context: str, *, fuzzy: bool = False) -> bool:
    """True iff ``quote`` is grounded in ``context``.

    ``fuzzy=False`` (default): a long-enough whitespace-normalized verbatim span.
    ``fuzzy=True``: also accept a paraphrase whose tokens form contiguous matching
    runs covering ``FUZZY_MATCH_RATIO`` of the quote (still rejects hallucinations).
    """
    if quote is None:
        return False
    normalized_quote = " ".join(str(quote).split())
    if len(normalized_quote) < MIN_QUOTE_CHARS or len(normalized_quote.split()) < MIN_QUOTE_WORDS:
        return False
    normalized_context = " ".join((context or "").split())
    if normalized_quote in normalized_context:
        return True
    if not fuzzy:
        return False
    quote_tokens = _content_tokens(normalized_quote)
    if len(quote_tokens) < MIN_QUOTE_WORDS:
        return False
    matcher = difflib.SequenceMatcher(None, quote_tokens, _content_tokens(normalized_context),
                                      autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return matched / len(quote_tokens) >= FUZZY_MATCH_RATIO
