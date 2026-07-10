"""PaperDigest ``parameters`` block — the deep-review parameter extraction (Part B).

The user asked the triage path to "pre-extract some parameters" (datasets,
baselines, sample size, metrics, architecture, external-validation). Extraction
happens in the deep review (top-K only — triage stays abstract-based), as one
extra JSON block in the digest prompt. The contract is the faithbench-hardened
abstention rule: a paper with no extractable parameters yields ``parameters=None``,
NEVER a fabricated guess.
"""
from __future__ import annotations

from zotero_summarizer.models import PaperDigest, PaperParameters
from zotero_summarizer.services.library.quality_review import (
    _DEFAULT_DIGEST_PROMPT,
    assess_digest,
)
from zotero_summarizer.services.setup.bootstrap import _default_goals_config


class _FakeLLM:
    """Returns a queued pydantic model for each pydantic_prompt call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def pydantic_prompt(self, *, prompt, pydantic_model):
        response = self._responses[self.calls]
        self.calls += 1
        return response


def test_digest_with_no_parameters_yields_none_not_a_guess():
    """A non-empirical / parameter-less paper → parameters=None (abstention,
    not a fabricated parameters object)."""
    digest = PaperDigest()  # all defaults — a valid, parameter-less digest
    llm = _FakeLLM([digest])
    out = assess_digest(title="T", full_text="position paper body",
                        config=_default_goals_config(), llm=llm)
    assert out.parameters is None


def test_digest_with_parameters_parses_verbatim():
    """An empirical paper's extracted parameters round-trip through the schema."""
    params = PaperParameters(
        dataset="MIMIC-IV (n=12000)",
        baselines=["GPT-4", "Llama-3"],
        sample_size="12000 admissions",
        metrics=["AUROC 0.81", "F1 0.74"],
        architecture="encoder-decoder transformer",
        external_validation=True,
    )
    digest = PaperDigest(parameters=params)
    llm = _FakeLLM([digest])
    out = assess_digest(title="T", full_text="empirical paper body",
                        config=_default_goals_config(), llm=llm)
    assert out.parameters is not None
    assert out.parameters.dataset == "MIMIC-IV (n=12000)"
    assert out.parameters.baselines == ["GPT-4", "Llama-3"]
    assert out.parameters.external_validation is True


def test_default_digest_prompt_instructs_parameter_extraction():
    """The shipped prompt tells the model to extract parameters verbatim and to
    abstain (null) for non-empirical papers — guards against prompt regressions."""
    assert '"parameters"' in _DEFAULT_DIGEST_PROMPT
    assert "VERBATIM" in _DEFAULT_DIGEST_PROMPT
    assert "external_validation" in _DEFAULT_DIGEST_PROMPT
    # abstention discipline is named for the parameters block too
    assert "null for non-empirical" in _DEFAULT_DIGEST_PROMPT
