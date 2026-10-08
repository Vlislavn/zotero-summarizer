"""Independent acceptance contracts for four paper-review evidence findings.

All model/provider behavior is scripted; the production entry points still decide
whether source evidence is retained, routed, counted, and bounded.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, get_args

import pytest

from zotero_summarizer.services.faithbench._build_qa import generate_candidates
from zotero_summarizer.services.library import _paper_section_summaries, qa, quality_calibration


class _SectionModel:
    """Script summary generation and semantic verdicts from the supplied schema."""

    def __init__(self, *, summary: str, quote: str, supported: bool, section_id: str):
        self.summary = summary
        self.quote = quote
        self.supported = supported
        self.section_id = section_id

    def pydantic_prompt(self, *, prompt: str, pydantic_model: Any, **kwargs: Any) -> Any:
        fields = pydantic_model.model_fields
        response_key = "sections" if "sections" in fields else "checks"
        response_types = get_args(fields[response_key].annotation)
        row_model = response_types[0]
        row_fields = row_model.model_fields
        values = {
            "index": 0,
            "section_id": self.section_id,
            "summary": self.summary,
            "supporting_quote": self.quote if response_key == "sections" or self.supported else "",
            "supported": self.supported,
        }
        row = {name: values[name] for name in row_fields if name in values}
        if response_key == "checks":
            assert {"section_id", "supporting_quote"} <= row_fields.keys()
            assert row["section_id"] == self.section_id
            assert row["supporting_quote"] == (self.quote if self.supported else "")
        return pydantic_model.model_validate({response_key: [row]})


def test_section_summary_rejects_grounded_quote_that_does_not_support_summary():
    source_quote = "The cohort included patients from three hospitals and follow-up lasted six months."
    sections = [{"id": "results", "title": "Results", "text": source_quote}]
    model = _SectionModel(
        summary="The intervention reduced mortality compared with usual care.",
        quote=source_quote,
        supported=False,
        section_id="results",
    )

    summaries = _paper_section_summaries.summarize_sections(sections, model)

    assert summaries == {}


def test_section_summary_retains_semantically_supported_paraphrase():
    source_quote = "We compared automated screening with the conventional manual review workflow."
    paraphrase = "The study contrasts automated screening against standard human review."
    sections = [{"id": "methods", "title": "Methods", "text": source_quote}]
    model = _SectionModel(
        summary=paraphrase, quote=source_quote, supported=True, section_id="methods"
    )

    summaries = _paper_section_summaries.summarize_sections(sections, model)

    assert summaries == {"methods": paraphrase}


class _AnswerModel:
    def __init__(self, *, answer: str | None = None, quote: str | None = None):
        self.answer = answer
        self.quote = quote
        self.prompts: list[str] = []

    def prompt(self, prompt: str, **kwargs: Any) -> str:
        self.prompts.append(prompt)
        return json.dumps({"answer": self.answer, "quote": self.quote})


def _install_qa(
    monkeypatch: pytest.MonkeyPatch,
    *,
    artifact: dict[str, Any],
    llm: _AnswerModel,
    max_chars: int,
) -> None:
    config = SimpleNamespace(
        quality_review=SimpleNamespace(max_text_chars=max_chars),
        llm_routing=SimpleNamespace(),
    )
    app = SimpleNamespace(
        app_state=SimpleNamespace(config=config),
        resolve_stage_client=lambda stage: llm,
    )
    monkeypatch.setattr(qa, "state", lambda: app)
    monkeypatch.setattr(
        qa, "resolve_stage", lambda routing, stage: SimpleNamespace(model="scripted-model")
    )
    monkeypatch.setattr(qa.paper_render, "build_paper_read", lambda item_key: artifact)


def test_scoped_opening_count_uses_qa_while_whole_paper_count_uses_metadata(monkeypatch):
    artifact = {
        "title": "A paper with a novel opening",
        "n_pages": 12,
        "figures_count": 3,
        "references_count": 42,
        "sections_count": 8,
        "full_text": "The paper begins with background and later reports its methods.",
    }
    llm = _AnswerModel()
    _install_qa(monkeypatch, artifact=artifact, llm=llm, max_chars=2_000)

    scoped = qa.ask_paper("PAPER", "How many references does the novel's opening paragraph cite?")
    whole = qa.ask_paper("PAPER", "How many references does the whole paper cite?")

    assert scoped["mode"] == "comprehensive"
    assert scoped["abstained"] is True
    assert whole["mode"] == "metadata"
    assert whole["answer"] == "42 references"
    assert len(llm.prompts) == 1


def test_generate_candidates_preserves_complete_long_source_in_bounded_windows():
    interior_sentinel = "INTERIOR_SENTINEL_60001"
    long_text = "".join(f"{index:05d}" for index in range(12_000)) + "x"
    sentinel_offset = 30_123
    long_text = (
        long_text[:sentinel_offset]
        + interior_sentinel
        + long_text[sentinel_offset + len(interior_sentinel):]
    )

    class FakeBuilder:
        def __init__(self):
            self.window_inputs: list[str] = []

        def prompt(self, prompt: str, **kwargs: Any) -> str:
            _, opening_tag, wrapped_input = prompt.rpartition("<untrusted_input>")
            excerpt, closing_tag, _ = wrapped_input.partition("</untrusted_input>")
            assert opening_tag and closing_tag
            excerpt = excerpt.removeprefix("\n").removesuffix("\n")
            self.window_inputs.append(excerpt)
            return json.dumps({"items": []})

    builder = FakeBuilder()

    generate_candidates(builder, title="Long paper", text=long_text, per_window=1)

    assert len(long_text) == 60_001
    assert builder.window_inputs
    assert all(0 < len(window) <= 6_000 for window in builder.window_inputs)
    assert "".join(builder.window_inputs) == long_text
    assert any(interior_sentinel in window for window in builder.window_inputs)


@pytest.mark.parametrize(
    "malformed_proposal",
    [
        pytest.param("not a proposal object", id="wrong-record-shape"),
        pytest.param({"proposed": "not-a-verdict"}, id="unknown-verdict"),
        pytest.param({"proposed": ["must_read"]}, id="unhashable-verdict"),
    ],
)
def test_malformed_matching_proposal_does_not_poison_valid_calibration_pair(
    malformed_proposal: Any,
):
    proposals = {
        "MALFORMED": malformed_proposal,
        "HEALTHY": {"proposed": "must_read"},
    }
    labels = {"MALFORMED": "dont_read", "HEALTHY": "must_read"}

    result = quality_calibration.compute_proposal_calibration(proposals=proposals, labels=labels)

    assert result["n_pairs"] == 1
    assert result["agreement"] == 1.0
    assert result["cohen_kappa"] is None
    assert result["insufficient"] is True
