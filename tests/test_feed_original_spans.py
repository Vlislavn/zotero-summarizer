"""Layout normalization must yield exact original spans, never paraphrase matches."""
import pytest

from tests.test_digest_verification import _digest
from tests.test_feed_engineering import _assessment
from zotero_summarizer.models import ResearchProfile
from zotero_summarizer.models.research_feed import FeedDigestResponse
from zotero_summarizer.services.library import _feed_assessment as feed


QUOTE = "Compare the baseline and candidate on identical held-out traces."
FIELDS = ("projects", "reproduction", "impact")


def _record(content, field):
    return content["projects"][0] if field == "projects" else content[field]


def _finish(original, quote, field):
    content = _assessment("paper", quote="Header.")
    if field == "reproduction":
        content[field] = {"tier": "unknown", "rationale": "Setup is unassessed",
                          "missing_inputs": [], "evidence_quote": ""}
    elif field == "impact":
        content[field] = {"research": 1, "production": 1, "personal_novelty": 1,
                          "rationale": "Unverified estimate", "evidence_quote": ""}
    _record(content, field)["evidence_quote"] = quote
    digest = FeedDigestResponse.model_validate({**_digest().model_dump(), "feed_assessment": content})
    before = digest.model_dump()
    context = feed.context_for("paper", ResearchProfile(themes=[], projects=["agent harness"]))
    out = {}
    feed.finish(digest, original, "same-response prompt", context, out)
    assert digest.model_dump() == before, "Canonicalization must not mutate the accepted digest"
    return out


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("source_separator,model_separator", [
    ("\n", " "), ("\t\r\n  ", " "), ("\u00a0", " "), (" ", "\n\t"),
])
def test_layout_only_quote_is_replaced_by_the_exact_original_substring(field, source_separator, model_separator):
    source_quote = source_separator.join(QUOTE.split())
    model_quote = model_separator.join(QUOTE.split())
    original = f"Header.\n{source_quote}\nTail."

    out = _finish(original, model_quote, field)

    assert out["status"] == "assessed"
    assert _record(out["content"], field)["evidence_quote"] == source_quote
    path = "/projects/0" if field == "projects" else f"/{field}"
    span = out["evidence"][path]
    assert original[span["start"]:span["end"]] == span["quote"] == source_quote
    assert span["end"] - span["start"] == len(source_quote)
    assert out["scientific_verification"] == "unverified"


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("changed_quote", [
    QUOTE.lower(), QUOTE.replace(".", "!"), QUOTE.replace("baseline", "different baseline"),
])
def test_changed_case_punctuation_or_words_are_still_rejected(field, changed_quote):
    out = _finish(f"Header.\n{QUOTE}\nTail.", changed_quote, field)
    assert out == {"status": "invalid"}


@pytest.mark.parametrize("field", FIELDS)
def test_exact_original_quote_is_unchanged(field):
    original = f"Header.\n{QUOTE}\nTail."
    out = _finish(original, QUOTE, field)
    assert out["status"] == "assessed"
    assert _record(out["content"], field)["evidence_quote"] == QUOTE
    path = "/projects/0" if field == "projects" else f"/{field}"
    span = out["evidence"][path]
    assert original[span["start"]:span["end"]] == QUOTE
