"""Shared quote-grounding contract (services.library._grounding)."""
from __future__ import annotations

from zotero_summarizer.services.library import _grounding
from zotero_summarizer.services.library import qa


def test_grounded_quote_requires_substantial_verbatim_span():
    ctx = "In this work We evaluate DxChain on the MIMIC-IV cardiac dataset for testing."
    # A full supporting sentence (>=6 words / >=40 chars) that is verbatim present.
    assert _grounding.quote_is_grounded("We evaluate DxChain on the MIMIC-IV cardiac dataset", ctx) is True


def test_short_or_absent_quotes_are_rejected():
    ctx = "The model is called DxChain and it is great."
    assert _grounding.quote_is_grounded("DxChain", ctx) is False          # single word
    assert _grounding.quote_is_grounded("the model", ctx) is False        # too short
    assert _grounding.quote_is_grounded(None, ctx) is False               # missing
    assert _grounding.quote_is_grounded("a quote that is not in the paper at all here", ctx) is False  # not present


def test_floors_match_published_contract():
    assert _grounding.MIN_QUOTE_WORDS == 6
    assert _grounding.MIN_QUOTE_CHARS == 40


def test_qa_reuses_the_shared_contract():
    # qa imports the shared function (single contract for qa / goal-summaries / quality).
    assert qa._quote_is_grounded is _grounding.quote_is_grounded
