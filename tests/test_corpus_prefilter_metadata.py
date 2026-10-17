"""Private prefilter provenance stays separate from public/generated data."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

from zotero_summarizer.integrations.pdf import PdfSource
from zotero_summarizer.models import GoalsConfig, SummarizeRequest, SummarizeResponse, TriageResult
from zotero_summarizer.services.triage import summarization


def _machine_response():
    return summarization._fast_reject_response(
        SummarizeRequest(title="Synthetic", abstract="Synthetic abstract"),
        {"has_corpus": True, "affinity_score": -0.31}, "Synthetic text",
    )


def test_private_outcome_cannot_be_supplied_by_json_or_leak_into_public_contract():
    machine = _machine_response()
    public = machine.model_dump(mode="json")
    assert machine._corpus_fast_rejected is True
    assert machine.model_copy()._corpus_fast_rejected is True
    reopened = SummarizeResponse.model_validate_json(machine.model_dump_json())
    forged = SummarizeResponse.model_validate_json(json.dumps({
        **public, "_corpus_fast_rejected": True, "corpus_fast_rejected": True,
    }))
    assert reopened._corpus_fast_rejected is False
    assert forged._corpus_fast_rejected is False
    assert reopened.model_dump(mode="json") == forged.model_dump(mode="json") == public
    for key in ("_corpus_fast_rejected", "corpus_fast_rejected"):
        assert key not in public
        assert key not in SummarizeResponse.model_fields
        for mode in ("validation", "serialization"):
            assert key not in SummarizeResponse.model_json_schema(mode=mode)["properties"]
        assert key not in TriageResult.model_json_schema()["properties"]


def test_private_defaults_are_per_response_and_independent_of_display_tags():
    machine = _machine_response()
    ordinary = SummarizeResponse(**machine.model_dump())
    missing = summarization._abstract_only_empty_response("Missing abstract")
    assert ordinary.tags == ["prefilter_low_corpus_affinity"]
    assert ordinary._corpus_fast_rejected is False
    assert missing._corpus_fast_rejected is False
    machine.tags = ["ordinary-topic"]
    ordinary.tags = ["prefix:PREFILTER_LOW_CORPUS_AFFINITY:suffix"]
    assert machine._corpus_fast_rejected is True
    assert ordinary._corpus_fast_rejected is False


def test_pdf_prefilter_uses_same_machine_producer_without_model_calls(monkeypatch):
    config = GoalsConfig(relevance_scale={i: str(i) for i in range(1, 6)}, llm={
        "draft_model": "scripted", "refine_model": "scripted",
        "api_base": "https://example.invalid/v1", "api_key_env": "SYNTHETIC_API_KEY",
    })
    llm = Mock()
    app = SimpleNamespace(app_state=SimpleNamespace(config=config),
                          resolve_stage_client=lambda stage: llm)
    # This is a controlled extraction boundary, not actual SDK/native acceptance.
    source = PdfSource("Synthetic original text", (), "a" * 64)
    extract = Mock(return_value=source)
    match = Mock(return_value={"has_corpus": True, "affinity_score": -0.31})
    monkeypatch.setattr(summarization, "state", lambda: app)
    monkeypatch.setattr(summarization, "_extract_pdf_source", extract)
    monkeypatch.setattr(summarization.corpus, "run_corpus_match", match)
    req = SummarizeRequest(title="Synthetic", pdf_path="/scripted.pdf")
    response = summarization.run_pipeline(req)
    extract.assert_called_once_with(req.pdf_path)
    match.assert_called_once_with(req, source.text)
    assert response._corpus_fast_rejected is True
    assert response.tags == ["prefilter_low_corpus_affinity"]
    assert response.reading_priority == "dont_read"
    llm.prompt.assert_not_called()
    llm.pydantic_prompt.assert_not_called()
