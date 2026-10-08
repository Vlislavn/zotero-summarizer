"""Per-section 'what it covers' one-liners: index→id mapping, body-text gating,
empty/out-of-range dropping. The LLM is stubbed (the real call needs a model)."""
from __future__ import annotations

import pytest

from zotero_summarizer.services.library import _paper_section_summaries as ss

SECTIONS = [
    {"id": "sec-1", "title": "Introduction", "page": 1, "text": "Framing the clinical-agent gap and prior triage baselines in detail."},
    {"id": "sec-2", "title": "Methods", "page": 4, "text": "How the triage gate is trained on labelled abstracts and evaluated."},
    {"id": "sec-3", "title": "Empty", "page": 9, "text": "   "},  # no body → excluded from the call
]


class _FakeLLM:
    def __init__(self, lines):
        self.lines = lines
        self.calls = 0

    def pydantic_prompt(self, *, prompt, pydantic_model):  # noqa: ARG002 - signature parity
        self.calls += 1
        return pydantic_model(sections=self.lines)


class _ScriptedLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0
        self.prompts = []

    def pydantic_prompt(self, *, prompt, pydantic_model):
        self.prompts.append(prompt)
        response = self.responses[self.calls]
        self.calls += 1
        if isinstance(response, Exception):
            raise response
        return pydantic_model.model_validate(response)


def _check(index, section_id, supported, quote=""):
    return {
        "index": index,
        "section_id": section_id,
        "supported": supported,
        "supporting_quote": quote,
    }


def test_maps_index_to_section_id_and_drops_blanks():
    llm = _ScriptedLLM(
        {"sections": [
            {"index": 0, "summary": "Frames the clinical-agent gap.",
             "supporting_quote": "Framing the clinical-agent gap and prior triage baselines"},
            {"index": 1, "summary": "  Describes the gate training and eval.  ",
             "supporting_quote": "triage gate is trained on labelled abstracts"},
        ]},
        {"checks": [
            _check(0, "sec-1", True, "Framing the clinical-agent gap and prior triage baselines"),
            _check(1, "sec-2", True, "triage gate is trained on labelled abstracts"),
        ]},
    )
    out = ss.summarize_sections(SECTIONS, llm)
    assert llm.calls == 2                                    # one generation + one verifier batch
    assert out == {"sec-1": "Frames the clinical-agent gap.",
                   "sec-2": "Describes the gate training and eval."}  # whitespace-collapsed


def test_empty_summary_and_out_of_range_index_are_dropped():
    llm = _FakeLLM([
        {"index": 0, "summary": ""},      # empty → dropped
        {"index": 9, "summary": "x y z", "supporting_quote": "missing"}, # out of range → dropped
    ])
    assert ss.summarize_sections(SECTIONS, llm) == {}
    assert llm.calls == 1


def test_no_usable_sections_makes_no_call():
    llm = _FakeLLM([])
    assert ss.summarize_sections([{"id": "s", "title": "t", "text": ""}], llm) == {}
    assert llm.calls == 0


def test_ungrounded_summary_is_dropped():
    llm = _FakeLLM([{
        "index": 0,
        "summary": "Reports 99% accuracy on SecretSet.",
        "supporting_quote": "Reports 99% accuracy on SecretSet.",
    }])
    assert ss.summarize_sections(SECTIONS, llm) == {}


def test_grounded_but_unrelated_quote_does_not_support_fabricated_finding():
    section = {
        "id": "trial-results",
        "title": "Results",
        "text": "The trial enrolled 40 patients at one hospital. The primary outcome was mortality.",
    }
    llm = _ScriptedLLM(
        {"sections": [{
            "index": 0,
            "summary": "The intervention cut mortality in half.",
            "supporting_quote": "The trial enrolled 40 patients at one hospital.",
        }]},
        {"checks": [_check(0, "trial-results", False)]},
    )

    assert ss.summarize_sections([section], llm) == {}
    assert llm.calls == 2


def test_source_supported_paraphrase_survives_independent_check():
    section = {
        "id": "trial-results",
        "title": "Results",
        "text": (
            "Participants assigned to the intervention showed a lower rate of "
            "hospitalization than those receiving standard care."
        ),
    }
    other = {"id": "sec-other", "title": "Discussion", "text": "The intervention was a new device."}
    llm = _ScriptedLLM(
        {"sections": [{
            "index": 0,
            "summary": "The intervention reduced hospitalizations compared with standard care.",
            "supporting_quote": "a lower rate of hospitalization than those receiving standard care",
        }]},
        {"checks": [_check(0, "trial-results", True,
                            "a lower rate of hospitalization than those receiving standard care")]},
    )

    assert ss.summarize_sections([section, other], llm) == {
        "trial-results": "The intervention reduced hospitalizations compared with standard care."
    }
    assert llm.calls == 2
    assert section["text"] in llm.prompts[1]
    assert other["text"] not in llm.prompts[1]


def test_verifier_context_uses_generation_prefix_and_keeps_prefix_paraphrase():
    evidence = (
        "Participants assigned to the intervention showed a lower rate of "
        "hospitalization than those receiving standard care."
    )
    prefix = (evidence + " Additional study context was reported. " * 30)[:700]
    suffix = "UNSEEN_SECTION_SUFFIX_" + "Unseen trial details. " * 5000
    section = {"id": "trial-results", "title": "Results", "text": prefix + suffix}
    quote = "a lower rate of hospitalization than those receiving standard care"
    llm = _ScriptedLLM(
        {"sections": [{
            "index": 0,
            "summary": "The intervention reduced hospitalizations compared with standard care.",
            "supporting_quote": quote,
        }]},
        {"checks": [_check(0, "trial-results", True, quote)]},
    )

    assert len(prefix) == 700
    assert ss.summarize_sections([section], llm) == {
        "trial-results": "The intervention reduced hospitalizations compared with standard care."
    }
    assert llm.calls == 2
    assert prefix in llm.prompts[0]
    assert prefix in llm.prompts[1]
    assert "UNSEEN_SECTION_SUFFIX_" not in llm.prompts[1]


def test_quote_only_in_unseen_suffix_cannot_validate_generation():
    prefix = ("This section describes the study protocol and data collection. " * 20)[:700]
    suffix_quote = (
        "Treatment group participants experienced fewer overnight hospital admissions "
        "than control participants during follow-up."
    )
    section = {"id": "trial-results", "title": "Results", "text": prefix + suffix_quote}
    llm = _ScriptedLLM(
        {"sections": [{
            "index": 0,
            "summary": "Treatment reduced hospital admissions compared with control.",
            "supporting_quote": suffix_quote,
        }]},
        {"checks": [_check(0, "trial-results", True, suffix_quote)]},
    )

    assert ss.summarize_sections([section], llm) == {}
    assert llm.calls == 1
    assert suffix_quote not in llm.prompts[0]


@pytest.mark.parametrize("checks", [
    [],
    [_check(99, "trial-results", True, "The trial enrolled 40 patients for follow-up.")],
    [_check(0, "unknown", True, "The trial enrolled 40 patients for follow-up.")],
    [
        _check(0, "trial-results", True, "The trial enrolled 40 patients for follow-up."),
        _check(0, "trial-results", True, "The trial enrolled 40 patients for follow-up."),
    ],
    [_check(1, "sec-other", True, "The trial findings may guide future work in other clinical settings.")],
    [_check(0, "trial-results", True, "The trial findings may guide future work in other clinical settings.")],
])
def test_missing_unknown_duplicate_or_cross_section_verifier_outcomes_fail_closed(checks):
    sections = [
        {"id": "trial-results", "title": "Results", "text": "The trial enrolled 40 patients for follow-up."},
        {"id": "sec-other", "title": "Discussion", "text": "The trial findings may guide future work in other clinical settings."},
    ]
    llm = _ScriptedLLM(
        {"sections": [{
            "index": 0,
            "summary": "The trial enrolled 40 patients.",
            "supporting_quote": "The trial enrolled 40 patients for follow-up.",
        }]},
        {"checks": checks},
    )

    with pytest.raises(ss.SectionVerifierUnavailable):
        ss.summarize_sections(sections, llm)
    assert llm.calls == 2


@pytest.mark.parametrize("response", [
    {},
    {"checks": [{
        "index": 0, "section_id": "trial-results", "supported": "yes",
        "supporting_quote": "The trial enrolled 40 patients for follow-up.",
    }]},
    {"checks": [{"index": 0, "section_id": "trial-results", "supported": True}]},
])
def test_malformed_verifier_response_fails_closed(response):
    from pydantic import ValidationError

    section = {"id": "trial-results", "title": "Results", "text": "The trial enrolled 40 patients for follow-up."}
    llm = _ScriptedLLM(
        {"sections": [{
            "index": 0,
            "summary": "The trial enrolled 40 patients.",
            "supporting_quote": "The trial enrolled 40 patients for follow-up.",
        }]},
        response,
    )

    with pytest.raises(ValidationError):
        ss.summarize_sections([section], llm)


def test_generation_provider_failure_propagates_without_calling_verifier():
    section = {"id": "trial-results", "title": "Results", "text": "The trial enrolled 40 patients for follow-up."}
    llm = _ScriptedLLM(RuntimeError("generator unavailable"))

    with pytest.raises(RuntimeError, match="generator unavailable"):
        ss.summarize_sections([section], llm)
    assert llm.calls == 1


def test_verifier_provider_failure_propagates_without_unverified_summary():
    section = {"id": "trial-results", "title": "Results", "text": "The trial enrolled 40 patients for follow-up."}
    llm = _ScriptedLLM(
        {"sections": [{
            "index": 0,
            "summary": "The trial enrolled 40 patients.",
            "supporting_quote": "The trial enrolled 40 patients for follow-up.",
        }]},
        RuntimeError("verifier unavailable"),
    )

    with pytest.raises(RuntimeError, match="verifier unavailable"):
        ss.summarize_sections([section], llm)
    assert llm.calls == 2


def test_verifier_provider_failure_degrades_optional_paper_map_summary(monkeypatch):
    from types import SimpleNamespace

    from zotero_summarizer.services.library import (
        _code_link,
        _deep_review_layers,
        _paper_goal_summaries,
        _paper_read_pdf,
        paper_type,
        quality_eval,
    )

    section = {
        "id": "trial-results", "title": "Results", "page": 2,
        "text": "The trial enrolled 40 patients for follow-up.",
    }
    llm = _ScriptedLLM(
        {"sections": [{
            "index": 0,
            "summary": "The trial enrolled 40 patients.",
            "supporting_quote": section["text"],
        }]},
        RuntimeError("verifier unavailable"),
    )
    monkeypatch.setattr(
        _paper_read_pdf, "extract_pdf_content",
        lambda *_args, **_kwargs: {"sections": [section], "full_text": section["text"], "link_uris": []},
    )
    monkeypatch.setattr(paper_type, "detect_safe", lambda *_args, **_kwargs: {"type": "generic_other"})
    monkeypatch.setattr(
        quality_eval, "evaluate_quality",
        lambda **_kwargs: SimpleNamespace(model_dump=lambda: {}),
    )
    monkeypatch.setattr(_paper_goal_summaries, "summarize_for_goals", lambda **_kwargs: [])
    monkeypatch.setattr(_code_link, "find_code_link", lambda *_args: {"found": False})
    config = SimpleNamespace(
        quality_review=SimpleNamespace(
            use_docling=False, lean_self_consistency_runs=1, self_consistency_runs=1,
            lean_max_text_chars=1000, max_text_chars=1000, self_verification=False,
            shadow_claim_check=False, claim_check_model="x", batch_goal_summaries=True,
        ),
        research_goals=[],
    )
    ctx = _deep_review_layers.ExtraLayersCtx(
        item_key="K1", title="Trial", pdf_path="paper.pdf", text=section["text"],
        digest_dump={}, llm=llm, config=config, prestige=None, prestige_floor_value=None,
    )

    quality_dump, goal_dump, _, overlay, code_link = _deep_review_layers.extra_layers(ctx)

    assert quality_dump == {} and goal_dump is None and code_link == {"found": False}
    assert overlay["sections"][0]["summary"] == ""
    assert llm.calls == 2
