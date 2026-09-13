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
# Answer-support: fraction of the ANSWER's distinct content tokens that must be
# present in the quote (order-free). 0.85 admits rewording/reordering of the
# quoted evidence (the universal answer shape) while any hallucinated token
# (number, name, verb not in the quote) fails the band. Tuned + verified against
# real kather/sota Q&A answers vs hallucinated controls, 2026-09-11.
ANSWER_COVER_RATIO = 0.85

_TOKEN_RE = re.compile(r"[a-z0-9]+")
# PDF extraction breaks words at line ends as "re-\ntrieval" (whitespace-flattened:
# "re- trieval"), while any model quote of that span reads "retrieval". Joining the
# hyphen-broken halves on BOTH sides before matching removes the whole class of
# false rejections without loosening either strictness level: the same
# normalization applies to quote and context, so hallucinated content still fails.
_HYPHEN_SPLIT_RE = re.compile(r"(\w)-\s+(?=[a-z])")
# PDF extraction emits typographic punctuation (LLM’s, “quoted”); model quotes of
# the same span use ASCII. Fold before matching — symmetric on both sides.
_PUNCT_FOLD = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})


def _dehyphenate(text: str) -> str:
    return _HYPHEN_SPLIT_RE.sub(r"\1", text)


def _match_normalize(text: str) -> str:
    """Shared pre-match normalization: whitespace-flatten + punctuation fold + dehyphenate."""
    return _dehyphenate(" ".join(unicodedata.normalize("NFKC", text).translate(_PUNCT_FOLD).split()))


def _content_tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(_dehyphenate(unicodedata.normalize("NFKC", text).translate(_PUNCT_FOLD).lower()))


def quote_is_grounded(quote: Any, context: str, *, fuzzy: bool = False) -> bool:
    """True iff ``quote`` is grounded in ``context``.

    ``fuzzy=False`` (default): a long-enough whitespace-normalized verbatim span.
    ``fuzzy=True``: also accept a paraphrase whose tokens form contiguous matching
    runs covering ``FUZZY_MATCH_RATIO`` of the quote (still rejects hallucinations).
    """
    if quote is None:
        return False
    normalized_quote = _match_normalize(str(quote))
    if len(normalized_quote) < MIN_QUOTE_CHARS or len(normalized_quote.split()) < MIN_QUOTE_WORDS:
        return False
    normalized_context = _match_normalize(context or "")
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


def answer_is_supported_by_quote(answer: Any, quote: Any) -> bool:
    """Support contract: the answer's content must come from the quote.

    Two acceptance bands, both anti-fabrication (a token NOT in the quote can
    never pass):

    * **verbatim span** — the answer's tokens appear contiguously in the quote
      (the original strict bar, kept for short extractive answers).
    * **covered paraphrase** (order-free) — ≥ ``ANSWER_COVER_RATIO`` of the
      answer's distinct content tokens occur in the quote. This is the shape
      real answers take: the quote is verbatim paper text, while the answer is
      the model's own words AROUND that text ("SLIM is the framework that
      separates … tools" vs the quote's "SLIM (Simple Lightweight Information
      Management), a simple framework that separates …"). Demanding a verbatim
      answer span rejected every such answer and collapsed Q&A into 100%
      spurious abstentions (measured live on kather/sota, 2026-09-11), because
      no non-trivial rewording of a sentence is ever a contiguous subsequence
      of it. Distinct-token coverage still rejects any answer introducing
      content absent from the quote.
    """
    answer_tokens = _content_tokens(str(answer or ""))
    quote_tokens = _content_tokens(str(quote or ""))
    if not answer_tokens or not quote_tokens or len(answer_tokens) > len(quote_tokens):
        return False
    width = len(answer_tokens)
    if any(quote_tokens[i:i + width] == answer_tokens for i in range(len(quote_tokens) - width + 1)):
        return True
    covered = len(set(answer_tokens) & set(quote_tokens)) / len(set(answer_tokens))
    return covered >= ANSWER_COVER_RATIO
