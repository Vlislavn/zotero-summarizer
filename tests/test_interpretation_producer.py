"""Mocked producer contracts only: no model/scientific/publication acceptance.

Reuse the existing digest factory and semantic-verifier fake; Mock records scripted
client returns without adding another LLM fake class or provider harness.
"""
from __future__ import annotations

import builtins
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.test_digest_verification import PAPER, _Verifier, _claims, _digest
from zotero_summarizer.integrations.llm import InstrumentedLLMClient, build_response_format
from zotero_summarizer.models import PaperDigest, PaperParameters
from zotero_summarizer.models.interpretation import InterpretationDigestResponse, InterpretationUnavailable
from zotero_summarizer.services.library import _digest_audit, quality_review
from zotero_summarizer.services.library._digest_verification import DigestVerifierUnavailable
from zotero_summarizer.services.library._prompt_security import UNTRUSTED_INPUT_RULE, untrusted_input
from zotero_summarizer.services.setup.bootstrap import _default_goals_config


REQUIRED = (
    "read_decision", "read_why", "read_parts", "skip_parts", "estimated_read_minutes",
    "original_value", "writing_friction", "writing_reasons",
)
SCIENTIFIC_FIELDS = {
    "inference_strength", "rationale", "scope", "uncertainties", "unsupported_extrapolations",
}


def _base():
    return _digest().model_copy(update={
        "key_findings": ["The evaluation uses held-out data."],
        "methods": "A carefully controlled evaluation.",
        "limitations": "Transfer was not tested.",
        "key_weakness": "One benchmark.",
        "parameters": PaperParameters(dataset="ImageNet", external_validation=False),
    })


def _card(rationale="The held-out evaluation supports a limited inference."):
    return {"items": [{
        "finding_ref": "/key_findings/0", "inference_strength": "Limited comparative evidence.",
        "rationale": rationale, "scope": "The studied ImageNet evaluation.",
        "uncertainties": ["Transfer remains unknown."],
        "unsupported_extrapolations": ["A causal or deployment recommendation is not established."],
        "basis_refs": ["/methods", "/limitations", "/key_weakness", "/parameters/dataset"],
    }]}


def _payload(card):
    return {**_base().model_dump(exclude_unset=True), "interpretation": card}


def _reply(payload, form):
    if form == "parsed":
        return InterpretationDigestResponse.model_validate(payload)
    if form == "dict":
        return payload
    text = json.dumps(payload)
    if form == "fenced":
        return "```json\n" + text + "\n```"
    return "Here is the digest: " + text + " End." if form == "prose" else text


def test_off_keeps_base_prompt_model_serialization_calls_and_no_extra_io(monkeypatch):
    config = _default_goals_config()
    template = "Title: {title}\nGoals: {research_goals}\nOriginal: {full_text}"
    config.prompts.paper_digest = template
    goals = "; ".join(g for g in (config.research_goals or []) if str(g).strip()) or "(not specified)"
    expected = UNTRUSTED_INPUT_RULE + "\n\n" + (
        "The original source is already supplied. Acquisition and authentication are operational "
        "states owned by the caller, not scientific claims to invent in the digest. "
        "Review the supplied evidence; do not claim it was never retrieved. "
        "Source-stated limitations of external resources may still be reported faithfully.\n\n"
    ) + template.format(title=untrusted_input("Paper"), full_text=untrusted_input(PAPER),
                        research_goals=untrusted_input(goals)) + (
        f"\n\nReader's focus note: {untrusted_input('Methods')}\n"
        "Please adjust your review emphasis to highlight or downplay aspects matching this focus."
    )
    generator = Mock()
    generator.pydantic_prompt.return_value = _digest()
    verifier = _Verifier()
    no_io = Mock(side_effect=AssertionError("unexpected file IO"))
    original_import = builtins.__import__

    def no_c_import(name, *args, **kwargs):
        assert name != "zotero_summarizer.models.interpretation"
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_c_import)
    monkeypatch.setattr("builtins.open", no_io)
    monkeypatch.setattr(Path, "open", no_io)
    monkeypatch.setattr(_digest_audit, "_contract_hashes", no_io)

    result = quality_review.assess_digest(title="Paper", full_text=PAPER, config=config,
                                         llm=generator, verifier_llm=verifier, focus_prompt="Methods")

    assert generator.pydantic_prompt.call_args.kwargs == {"prompt": expected, "pydantic_model": PaperDigest}
    assert type(result) is PaperDigest
    assert result.model_dump(mode="json") == _digest().model_dump(mode="json")
    assert "interpretation" not in PaperDigest.model_json_schema()["properties"]
    assert "interpretation" not in result.model_dump()
    assert generator.pydantic_prompt.call_count == verifier.calls == 1
    no_io.assert_not_called()


@pytest.mark.parametrize("format_arg", [None, {}, {"type": "json_object"}])
def test_off_preserves_caller_response_format(format_arg):
    generator = Mock()
    generator.pydantic_prompt.return_value = _digest()
    quality_review.assess_digest(title="Paper", full_text=PAPER, config=_default_goals_config(),
                                 llm=generator, verifier_llm=_Verifier(), response_format=format_arg)
    expected = {"response_format": format_arg} if format_arg else {}
    assert {key: value for key, value in generator.pydantic_prompt.call_args.kwargs.items()
            if key not in {"prompt", "pydantic_model"}} == expected


def test_c_schema_is_typed_and_cannot_authorize_origin_or_verification():
    schema = InterpretationDigestResponse.model_json_schema()
    item = schema["$defs"]["InterpretationItem"]
    draft = schema["$defs"]["InterpretationDraft"]
    assert set(item["properties"]) == SCIENTIFIC_FIELDS | {"finding_ref", "basis_refs"}
    assert set(item["required"]) == set(item["properties"])
    assert item["additionalProperties"] is False and draft["additionalProperties"] is False
    assert draft["properties"]["items"]["minItems"] == 1
    for field in ("uncertainties", "unsupported_extrapolations", "basis_refs"):
        assert item["properties"][field]["items"]["type"] == "string"
    assert "pattern" in item["properties"]["basis_refs"]["items"]
    alternatives = schema["properties"]["interpretation"]["anyOf"]
    assert {part.get("$ref", part.get("type")) for part in alternatives} == {
        "#/$defs/InterpretationDraft", "#/$defs/InterpretationUnavailable", "null",
    }
    assert "origin" not in schema["properties"] and "scientific_verification" not in schema["properties"]


def test_off_projects_an_unexpected_c_subclass_without_payload_leak():
    raw = InterpretationDigestResponse.model_validate(_payload(_card()))
    result = quality_review._coerce_digest(raw)
    assert type(result) is PaperDigest
    assert result.model_dump() == _base().model_dump()
    assert "interpretation" not in result.model_dump()


@pytest.mark.parametrize("form", ["dict", "json", "fenced", "prose", "parsed"])
def test_c_capture_precedes_plain_base_projection_and_retains_explicit_presence(form):
    out = {"stale": True}
    result = quality_review._coerce_digest(_reply(_payload(_card()), form), interpretation_out=out)
    assert type(result) is PaperDigest
    assert result.model_dump(mode="json") == _base().model_dump(mode="json")
    assert result.model_fields_set == _base().model_fields_set
    assert "interpretation" not in result.model_dump() and "interpretation" not in result.model_json_schema()["properties"]
    assert out == {"origin": "ai_generated", "scientific_verification": "unverified",
                   "status": "schema_validated_ai_draft", **_card()}
    assert result.key_findings[0] == "The evaluation uses held-out data."
    assert "finding_text" not in out["items"][0]


@pytest.mark.parametrize("form", ["dict", "json", "fenced", "prose", "parsed"])
@pytest.mark.parametrize("card,reason", [
    (None, "missing_interpretation"), ({"items": []}, "malformed_interpretation"),
    ({"unavailable_reason": "missing_interpretation"}, "missing_interpretation"),
    ({"items": [{key: value for key, value in _card()["items"][0].items() if key != "rationale"}]}, "malformed_interpretation"),
    ({"items": [{**_card()["items"][0], "uncertainties": "unknown"}]}, "malformed_interpretation"),
    ({"items": [{**_card()["items"][0], "rationale": 42}]}, "malformed_interpretation"),
    ({"items": [{**_card()["items"][0], "finding_text": "Copied finding"}]}, "malformed_interpretation"),
    ({"items": [{**_card()["items"][0], "basis_refs": ["/confidence"]}]}, "malformed_interpretation"),
    ({"items": [{**_card()["items"][0], "finding_ref": "/key_findings/00"}]}, "malformed_interpretation"),
    ({"items": [{**_card()["items"][0], "finding_ref": "/key_findings/99"}]}, "invalid_references"),
    ({"items": [{**_card()["items"][0], "finding_ref": "/key_findings/" + "9" * 5000}]}, "invalid_references"),
    ({"items": [{**_card()["items"][0], "basis_refs": ["/parameters/architecture"]}]}, "invalid_references"),
    ({"items": _card()["items"] * 2}, "invalid_references"),
    ({**_card(), "origin": "author_approved", "status": "accepted"}, "malformed_interpretation"),
])
def test_optional_failure_preserves_known_valid_base_explicitly(form, card, reason):
    out = {"stale": True}
    result = quality_review._coerce_digest(_reply(_payload(card), form), interpretation_out=out)
    assert type(result) is PaperDigest and result.model_dump() == _base().model_dump()
    assert out == {"origin": "ai_generated", "scientific_verification": "unverified",
                   "status": "unavailable", "unavailable_reason": reason}


@pytest.mark.parametrize("form", ["dict", "parsed"])
def test_missing_optional_content_is_not_a_silent_empty_card(form):
    out = {}
    result = quality_review._coerce_digest(_reply(_base().model_dump(exclude_unset=True), form), interpretation_out=out)
    assert result.model_dump() == _base().model_dump()
    assert out["unavailable_reason"] == "missing_interpretation"


@pytest.mark.parametrize("ref,field,value", [("/tldr", "tldr", ""),
                                           ("/key_findings/0", "key_findings", [""])])
def test_binding_requires_actual_nonempty_current_finding(ref, field, value):
    card = _card()
    card["items"][0]["finding_ref"] = ref
    payload = {**_payload(card), field: value}
    out = {}
    result = quality_review._coerce_digest(payload, interpretation_out=out)
    assert getattr(result, field) == value
    assert out["status"] == "unavailable" and out["unavailable_reason"] == "invalid_references"


@pytest.mark.parametrize("field", REQUIRED)
@pytest.mark.parametrize("form", ["dict", "json", "parsed"])
def test_c_does_not_fabricate_required_fields_from_typed_dump_defaults(field, form):
    payload = _payload(_card())
    del payload[field]
    raw = _reply(payload, form)
    if form == "parsed":
        assert field not in raw.model_fields_set
    out = {"stale": True}
    with pytest.raises(quality_review.DigestGenerationFailed, match="omitted required"):
        quality_review._coerce_digest(raw, interpretation_out=out)
    assert out == {}


@pytest.mark.parametrize("form", ["dict", "parsed"])
@pytest.mark.parametrize("field,value", [("soundness", 0), ("read_decision", "maybe"),
                                         ("writing_friction", "high")])
def test_optional_failure_never_masks_invalid_base(form, field, value):
    payload = _payload({"items": []})
    payload[field] = value
    out = {"stale": True}
    with pytest.raises(ValueError):
        # Simulate a pre-parsed client object that bypassed validation: recheck it.
        raw = InterpretationDigestResponse.model_construct(**{
            **payload, "interpretation": InterpretationUnavailable(unavailable_reason="malformed_interpretation"),
        }) if form == "parsed" else payload
        quality_review._coerce_digest(raw, interpretation_out=out)
    assert out == {}


@pytest.mark.parametrize("format_route", ["prompt", "caller", "instrumented"])
def test_c_uses_same_selected_schema_on_generation_parse_retry_and_correction(format_route):
    rejected = _payload(_card("First attempt rationale."))
    rejected["tldr"] = "The result is 99999%."
    corrected = _payload(_card("Corrected attempt rationale."))
    generator = Mock()
    generator.pydantic_prompt.side_effect = ["", rejected, InterpretationDigestResponse.model_validate(corrected)]
    client = InstrumentedLLMClient(generator, structured_output=True) if format_route == "instrumented" else generator
    response_format = build_response_format(PaperDigest) if format_route == "caller" else None
    verifier, out = _Verifier(), {"stale": True}

    result = quality_review.assess_digest(title="Paper", full_text=PAPER, config=_default_goals_config(),
                                         llm=client, verifier_llm=verifier, interpretation_out=out,
                                         response_format=response_format)

    calls = generator.pydantic_prompt.call_args_list
    assert len(calls) == 3 and verifier.calls == 1
    assert all(call.kwargs["pydantic_model"] is InterpretationDigestResponse for call in calls)
    for call in calls:
        assert "Experimental AI-draft interpretation:" in call.kwargs["prompt"]
        if format_route != "prompt":
            assert call.kwargs["response_format"] == build_response_format(InterpretationDigestResponse)
        else:
            assert "response_format" not in call.kwargs
    assert "previous reply was not valid" in calls[1].kwargs["prompt"]
    assert "failed source verification" in calls[2].kwargs["prompt"]
    assert type(result) is PaperDigest and result.model_dump() == _base().model_dump()
    assert out["items"][0]["rationale"] == "Corrected attempt rationale."
    assert out["scientific_verification"] == "unverified"
    support = out["native_support"]
    count = len(_claims(result))
    assert support["base_count"] == count and support["global_indices"] == list(range(count, count + 5))
    assert support["validated_checks"] == [{"index": index, "supported": True, "evidence": [0]}
                                           for index in range(count, count + 5)]
    assert "interpretation[0](/key_findings/0).rationale: Corrected attempt rationale." in support["optional_claims"]
    assert all("First attempt" not in claim for claim in support["optional_claims"])


@pytest.mark.parametrize("card", [None, {"items": []}, _card()])
@pytest.mark.parametrize("typed_client", [False, True])
def test_optional_content_never_adds_generator_retry(card, typed_client):
    generator = Mock()
    payload = _payload(card)
    generator.pydantic_prompt.side_effect = (
        lambda **kwargs: kwargs["pydantic_model"].model_validate(payload)
    ) if typed_client else [payload]
    verifier, out = _Verifier(), {}
    result = quality_review.assess_digest(title="Paper", full_text=PAPER, config=_default_goals_config(),
                                         llm=generator, verifier_llm=verifier, interpretation_out=out)
    assert result.model_dump() == _base().model_dump()
    assert generator.pydantic_prompt.call_count == verifier.calls == 1
    assert out["scientific_verification"] == "unverified"
    if card == _card():
        count = len(_claims(result))
        assert out["native_support"]["validated_checks"] == [{"index": index, "supported": True, "evidence": [0]}
                                                            for index in range(count, count + 5)]
    else:
        assert out["status"] == "unavailable" and "native_support" not in out


def test_base_evidence_and_original_spans_cannot_certify_optional_scientific_content():
    card = _card("An invented extrapolation reaches 99999%.")
    card["items"].append({**card["items"][0], "finding_ref": "/tldr", "basis_refs": ["/parameters/external_validation"]})
    generator = Mock()
    generator.pydantic_prompt.return_value = _payload(card)
    verifier, out, audit = _Verifier(), {}, {}
    result = quality_review.assess_digest(title="Paper", full_text=PAPER, config=_default_goals_config(),
                                         llm=generator, verifier_llm=verifier, interpretation_out=out,
                                         verification_audit=audit)
    assert generator.pydantic_prompt.call_count == verifier.calls == 1
    assert all("99999" not in claim for claim in _claims(result))
    count = len(_claims(result))
    diagnostic = {"phase": "numeric", "base_count": count, "rejected_indices": [count + 1, count + 6]}
    assert out == {"origin": "ai_generated", "scientific_verification": "unverified", "status": "unavailable",
                   "unavailable_reason": "native_optional_support_not_established", "optional_rejection": diagnostic}
    assert "items" not in out and "native_support" not in out
    assert audit["status"] == "accepted_verifier_evidence"
    assert audit["lineage"][0]["optional_rejection"] == diagnostic and audit["lineage"][0]["outcome"] == "accepted"
    assert {claim["field"] for claim in audit["claims"]} == {"tldr", "key_findings"}
    passage = audit["passages"][0]
    assert passage["text"] == PAPER and passage["selected_span"] == {"start": 0, "end": len(PAPER)}
    assert passage["original_fragments"] == [{"selected_span": {"start": 0, "end": len(PAPER)},
                                              "original_span": {"start": 0, "end": len(PAPER)}, "mapping": "exact"}]
    assert "interpretation" not in result.model_dump()


@pytest.mark.parametrize("failure", ["source", "parse", "generator", "base_rejected", "verifier", "correction", "second_verifier"])
def test_assessment_failure_clears_existing_collector_and_never_exposes_first_attempt(monkeypatch, failure):
    generator = Mock()
    generator.pydantic_prompt.side_effect = [_payload(_card("First attempt.")), _payload(_card("Second attempt."))]
    verifier = _Verifier()
    text = PAPER
    if failure == "source":
        text = ""
    elif failure == "parse":
        generator.pydantic_prompt.side_effect = ["", ""]
    elif failure == "generator":
        generator.pydantic_prompt.side_effect = RuntimeError("generation failed")
    elif failure == "base_rejected":
        verifier = _Verifier(supported=False)
    elif failure == "verifier":
        monkeypatch.setattr(quality_review, "_verify_generated_digest", Mock(side_effect=DigestVerifierUnavailable("unavailable")))
    else:
        second = ValueError("unsupported") if failure == "correction" else DigestVerifierUnavailable("unavailable")
        monkeypatch.setattr(quality_review, "_verify_generated_digest", Mock(side_effect=[ValueError("unsupported"), second]))
        if failure == "correction":
            generator.pydantic_prompt.side_effect = [_payload(_card("First attempt.")), ""]
    out = {"origin": "ai_generated", "items": _card()["items"]}
    with pytest.raises((ValueError, RuntimeError)):
        quality_review.assess_digest(title="Paper", full_text=text, config=_default_goals_config(),
                                     llm=generator, verifier_llm=verifier, interpretation_out=out)
    assert out == {}
    if failure == "source":
        generator.pydantic_prompt.assert_not_called()


def test_correction_missing_card_replaces_not_reuses_first_attempt():
    rejected = _payload(_card("First attempt."))
    rejected["tldr"] = "The result is 99999%."
    generator = Mock()
    generator.pydantic_prompt.side_effect = [rejected, _base()]
    out = {}
    result = quality_review.assess_digest(title="Paper", full_text=PAPER, config=_default_goals_config(),
                                         llm=generator, verifier_llm=_Verifier(), interpretation_out=out)
    assert result.model_dump() == _base().model_dump() and type(result) is PaperDigest
    assert out == {"origin": "ai_generated", "scientific_verification": "unverified",
                   "status": "unavailable", "unavailable_reason": "missing_interpretation"}
    assert generator.pydantic_prompt.call_count == 2
