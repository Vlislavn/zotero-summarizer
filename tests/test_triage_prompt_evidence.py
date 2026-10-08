"""Machine I/O and instruction-presence contracts, not scientific-faithfulness checks."""

import json

import pytest

from datetime import datetime, timezone

from zotero_summarizer.models import GoalsConfig, RefinedSummary, SummarizeRequest, TriageResult
from zotero_summarizer.services.triage.summarization import (
    _assemble_summary_response, _build_refine_prompt, _build_triage_prompt,
)


FIDELITY_EXTENSION = (
    "Preserve differing effect directions with their conditions across tasks and systems; "
    "do not let an abstract headline override exceptions in Results. Distinguish "
    "unsuccessful validation from absent validation, and repository availability from "
    "proved reproducibility."
)


@pytest.mark.parametrize("artifacts", [None, {
    "what_it_does": "arbitrary reusable method", "what_is_new": "arbitrary novelty",
    "how_it_works": ["arbitrary step"], "evaluation": "arbitrary validation result",
    "artifacts": ["https://example.org/arbitrary"], "how_i_could_use_it": "arbitrary use",
}])
def test_triage_summary_preserves_canonical_fields(artifacts, config):
    refined = RefinedSummary(
        executive_summary="arbitrary overview", methods="arbitrary method",
        key_findings=["arbitrary finding"], limitations="arbitrary limitation",
        unknown_unknowns="arbitrary optional detail", method_and_code=artifacts,
    )
    captured = _build_triage_prompt(
        config, SummarizeRequest(title="arbitrary title"), refined, {},
        template_override="<untrusted_input>{summary}</untrusted_input>",
    )
    payload = captured.removeprefix("<untrusted_input>").removesuffix("</untrusted_input>")
    assert json.loads(payload) == refined.model_dump(mode="json")


def test_rendered_fidelity_instruction_presence_contrast(config):
    captured = _build_refine_prompt(
        config, SummarizeRequest(title="arbitrary title"), "arbitrary source",
    )
    assert FIDELITY_EXTENSION in captured
    prior_instruction = captured.replace(FIDELITY_EXTENSION, "")
    assert FIDELITY_EXTENSION not in prior_instruction
    assert "SOURCE FIDELITY:" in prior_instruction


def test_refine_prompt_uses_trusted_runtime_date_and_separates_evidence_roles(config, monkeypatch):
    from zotero_summarizer.services.triage import summarization
    clock = type("Clock", (), {"now": staticmethod(lambda tz: datetime(2000, 1, 2, tzinfo=timezone.utc))})
    monkeypatch.setattr(summarization, "datetime", clock)
    prompt = _build_refine_prompt(config, SummarizeRequest(title="arbitrary"), "untrusted date: 1900")
    assert "Current UTC date (trusted runtime metadata): 2000-01-02" in prompt
    assert "training, evaluation, inference and deployment conditions separate" in prompt
    assert "Illustrative contrast only, NOT facts about this paper" in prompt
    assert "Label implementation suggestions as proposed adaptations" in prompt


@pytest.mark.parametrize("suggestion", ["should_read", "dont_read"])
def test_composite_priority_is_authoritative_without_discarding_model_assessment(suggestion):
    triage = TriageResult(score=3, reading_priority=suggestion, tags=[],
                          rationale="Original model explanation.", dimensions={}, confidence=0.5)
    result = _assemble_summary_response(RefinedSummary(executive_summary="Source summary."),
                                        triage, 2.75, "could_read", {})
    assert result.reading_priority == "could_read"
    assert result.composite_relevance_score == 2.75
    assert result.triage_rationale.startswith("Final priority: could read from composite score 2.75.")
    assert suggestion.replace("_", " ") in result.triage_rationale
    assert result.triage_rationale.endswith(triage.rationale)


def test_agreeing_priority_preserves_original_rationale():
    triage = TriageResult(score=3, reading_priority="could_read", tags=[],
                          rationale="Original model explanation.", dimensions={}, confidence=0.5)
    result = _assemble_summary_response(RefinedSummary(executive_summary="Source summary."),
                                        triage, 2.75, "could_read", {})
    assert result.triage_rationale == triage.rationale


@pytest.fixture
def config():
    return GoalsConfig(relevance_scale={i: str(i) for i in range(1, 6)}, llm={
        "draft_model": "stub", "refine_model": "stub", "api_base": "http://stub",
        "api_key_env": "STUB_KEY",
    })
