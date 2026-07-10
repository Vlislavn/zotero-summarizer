"""assess_digest does ONE reinforced retry on a malformed/out-of-range digest.

A reasoning model (e.g. the remote endpoint) occasionally emits a digest
with scores of 0 (PaperDigest requires 1-5) or non-JSON. The path re-asks once strictly,
then propagates the error (caught at deep_review's per-item boundary — never faked).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from zotero_summarizer.models import PaperDigest
from zotero_summarizer.services.library.quality_review import assess_digest
from zotero_summarizer.services.setup.bootstrap import _default_goals_config


class _FakeLLM:
    """Returns queued responses for successive pydantic_prompt calls."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def pydantic_prompt(self, *, prompt, pydantic_model):
        response = self._responses[self.calls]
        self.calls += 1
        return response


def test_assess_digest_retries_on_malformed_then_succeeds():
    bad = '{"soundness": 0, "novelty": 0, "significance": 0, "reproducibility": 0, "clarity": 0}'
    good = PaperDigest()  # all-default → valid (scores default to in-range values)
    llm = _FakeLLM([bad, good])
    out = assess_digest(title="T", full_text="paper body", config=_default_goals_config(), llm=llm)
    assert isinstance(out, PaperDigest)
    assert out.basis == "full_text"
    assert llm.calls == 2  # retried exactly once


def test_assess_digest_raises_when_retry_also_fails():
    bad = '{"soundness": 0}'
    llm = _FakeLLM([bad, bad])
    with pytest.raises((ValidationError, ValueError)):
        assess_digest(title="T", full_text="paper body", config=_default_goals_config(), llm=llm)
    assert llm.calls == 2  # tried once + one retry, then propagated
