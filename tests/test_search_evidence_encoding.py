"""Provider output must remain encodable without interrupting ordinary review."""
from __future__ import annotations

import json

import pytest

from tests.test_search_evidence_pass import _digest, _loaded_sources, _receipt, _run, case
from zotero_summarizer.services.search._evidence_pass import _checked_request
from zotero_summarizer.services.search._models import Candidate, QueryPlan, ResearchSession, SearchIntent


@pytest.mark.parametrize("stage", ["generator", "verifier"])
@pytest.mark.parametrize("text,invalid", [(json.loads('"\\ud800"'), True), ("Нет 🧪 𝄞 e\u0301", False)])
def test_provider_output_encoding_boundary_preserves_review(case, stage, text, invalid):
    case.request.candidate_ids = ("P11",)
    if stage == "generator":
        original = case.request.generator

        def generated(**kwargs):
            reply = original(**kwargs)
            reply["answer"] = text
            return reply

        case.request.generator = generated
    else:
        case.state = "partial"
        original = case.request.verifier

        def verified(**kwargs):
            reply = original(**kwargs)
            reply["uncovered"] = [text]
            return reply

        case.request.verifier = verified

    result = _run(case, limits=(1, 0))

    receipt = _receipt(result)
    if invalid:
        assert (receipt["state"], receipt["reason"]) == ("unknown", "invalid_schema")
        assert "answer" not in receipt and receipt["uncovered"] == []
        if stage == "generator":
            assert case.verified == []
    elif stage == "generator":
        assert receipt["state"] == "sufficient" and receipt["answer"] == text
    else:
        assert receipt["state"] == "partial" and receipt["uncovered"] == [text]
    assert any(event[0] == "light" for event in case.events)
    assert case.effects == []


@pytest.mark.parametrize("field", ["source_id", "source_version_id", "passage_id", "text"])
@pytest.mark.parametrize("text,invalid", [(json.loads('"\\udfff"'), True), ("Нет 🧪 𝄞 e\u0301", False)])
def test_source_encoding_is_admitted_before_model_callbacks(case, field, text, invalid):
    case.request.candidate_ids = ("P11",)
    source = case.sources["P11"]
    if field == "passage_id":
        source["passages"][1][field] = text
        case.verifier_fault = {"evidence_ids": [text]}
    elif field == "text":
        if invalid:
            source["text"] = text
        else:
            source["text"] += text
            source["source_sha256"] = _digest(source["text"])
            source["checked_scope"]["end"] = len(source["text"])
            case.request.max_chars = len(source["text"])
    else:
        source[field] = text

    result = _run(case, limits=(1, 0))

    receipt = _receipt(result)
    if invalid:
        assert (receipt["state"], receipt["reason"]) == ("unknown", "invalid_source")
        assert receipt["source_id"] == receipt["source_version_id"] == ""
        assert receipt["evidence"] == [] and case.generated == case.verified == []
        assert not any(event[0] == "context" for event in case.events)
    else:
        assert receipt["state"] == "sufficient"
        assert receipt["source_sha256"] == source["source_sha256"]
        if field == "passage_id":
            assert receipt["evidence"][0][field] == text
        elif field != "text":
            assert receipt[field] == text
    assert result.status == "reviewed" and any(event[0] == "light" for event in case.events)
    assert case.effects == []


@pytest.mark.parametrize("field", ["source_id", "authorship", "text"])
@pytest.mark.parametrize("text,invalid", [(json.loads('"\\udfff"'), True), ("Нет 🧪 𝄞 e\u0301", False)])
def test_context_encoding_is_admitted_before_generator_context(case, field, text, invalid):
    case.request.candidate_ids = ("P11",)
    case.context["records"][0][field] = text

    result = _run(case, limits=(1, 0))

    receipt = _receipt(result)
    assert receipt["state"] == "sufficient"
    if invalid:
        assert (receipt["context_availability"], receipt["context_reason"]) == ("unavailable", "invalid_context")
        assert all(call["context"] == {"availability": "unavailable", "records": []} for call in case.generated)
    else:
        assert (receipt["context_availability"], receipt["context_reason"]) == ("available_nonempty", "")
        assert all(call["context"] == case.context for call in case.generated)
    assert result.status == "reviewed" and any(event[0] == "light" for event in case.events)
    assert case.effects == []


def test_encodable_empty_context_text_retains_the_existing_schema(case):
    case.request.candidate_ids = ("P11",)
    case.context["records"][0]["text"] = ""

    result = _run(case, limits=(1, 0))

    assert _receipt(result)["state"] == "sufficient"
    assert all(call["context"] == case.context for call in case.generated)
    assert _receipt(result)["context_availability"] == "available_nonempty"
    assert case.effects == []


@pytest.mark.parametrize("field", ["generator_id", "verifier_id", "candidate_id", "canonical_question", "question"])
@pytest.mark.parametrize("text,invalid", [(json.loads('"\\udfff"'), True), ("Нет 🧪 𝄞 e\u0301", False)])
def test_request_encoding_is_admitted_before_callbacks_or_receipts(case, field, text, invalid):
    stored = ResearchSession(id="admission", created_at="2026-10-13T00:00:00Z", raw_query="topic",
                             intent=SearchIntent(raw_query="topic"), plan=QueryPlan(), questions=["Original question"],
                             candidates=[Candidate(title="Paper", candidate_id="P11")])
    case.request.candidate_ids = ("P11",)
    if field == "candidate_id":
        stored.candidates[0].candidate_id = text
        case.request.candidate_ids = (text,)
    elif field == "canonical_question":
        stored.intent.canonical_question = text
    elif field == "question":
        stored.questions = [text]
    else:
        setattr(case.request, field, text)

    if invalid:
        with pytest.raises(ValueError):
            _checked_request(case.request, stored)
    else:
        admitted = _checked_request(case.request, stored)
        if field == "candidate_id":
            assert admitted.candidate_ids == (text,)
        elif field == "canonical_question":
            assert stored.intent.canonical_question == text
        elif field == "question":
            assert stored.questions == [text]
        else:
            assert getattr(admitted, field) == text
    assert stored.candidates[0].question_evidence == []
    assert case.events == case.generated == case.verified == case.effects == []


@pytest.mark.parametrize("field", ["source_id", "source_version_id", "passage_id", "quote"])
def test_nonencodable_generated_locator_is_schema_invalid_before_verifier(case, field):
    case.request.candidate_ids = ("P11",)
    case.proposal_fault = {field: json.loads('"\\udfff"')}

    result = _run(case, limits=(1, 0))

    receipt = _receipt(result)
    assert (receipt["state"], receipt["reason"]) == ("unknown", "invalid_schema")
    assert "answer" not in receipt and receipt["evidence"] == []
    assert case.generated and case.verified == []
    assert result.status == "reviewed" and any(event[0] == "light" for event in case.events)
    assert case.effects == []
