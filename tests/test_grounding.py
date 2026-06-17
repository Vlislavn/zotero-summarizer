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


def test_fuzzy_grounds_paraphrase_that_strict_rejects():
    # Regression: smaller models paraphrase their evidence quote, so the strict
    # verbatim bar rejected genuinely-met criteria and collapsed checklist coverage.
    ctx = ("Annotations were obtained independently from four practicing radiologists "
           "at the medical center, and patients were split at the subject level.")
    paraphrase = "annotations obtained independently from four practicing radiologists at the center"
    assert _grounding.quote_is_grounded(paraphrase, ctx) is False              # strict: not verbatim
    assert _grounding.quote_is_grounded(paraphrase, ctx, fuzzy=True) is True   # fuzzy: content present


def test_fuzzy_still_rejects_hallucinations():
    ctx = ("Annotations were obtained from four practicing radiologists; the network "
           "was trained on chest radiographs and reported an F1 score with a confidence interval.")
    # text simply not in the body
    assert _grounding.quote_is_grounded(
        "the trial enrolled three thousand patients across nine centers in Asia", ctx, fuzzy=True) is False
    # scattered common domain words (no contiguous run) must NOT ground — anti-fabrication
    assert _grounding.quote_is_grounded(
        "radiologists network chest trained score confidence patients centers reported obtained",
        ctx, fuzzy=True) is False


def test_fuzzy_is_nfkc_robust_to_ligatures():
    # PDF extraction keeps the 'ﬁ' ligature; a model emits plain 'fi'. Must still ground.
    ctx = "we ﬁt the classiﬁer on the training set and evaluate diagnostic eﬃciency on held-out data"
    quote = "we fit the classifier on the training set and evaluate diagnostic efficiency"
    assert _grounding.quote_is_grounded(quote, ctx, fuzzy=True) is True


def test_strict_default_unchanged_for_safety_critical_callers():
    # qa / goal-summaries call WITHOUT fuzzy → exact-span behavior preserved (faithbench guard).
    ctx = "We evaluate DxChain on the MIMIC-IV cardiac dataset for held-out testing."
    assert _grounding.quote_is_grounded("We evaluate DxChain on the MIMIC-IV cardiac dataset", ctx) is True
    assert _grounding.quote_is_grounded("we assess DxChain using the MIMIC cardiac data", ctx) is False
