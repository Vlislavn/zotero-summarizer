"""Encoding at current output/source/request owners; no supplied-pass facade."""
import hashlib
import json

import pytest

from tests._search_evidence_fixtures import QUESTION, QUOTE, journey, run  # noqa: F401
from zotero_summarizer.services.library._related_context import collect_comments
from zotero_summarizer.services.search import _question_evidence as evidence
from zotero_summarizer.services.search import pipeline, session
from zotero_summarizer.services.search._models import ScreenRequest

UNICODE = "Нет 🧪 𝄞 e\u0301"


@pytest.mark.parametrize("stage", ["generator", "verifier"])
@pytest.mark.parametrize("suffix,invalid", [("\ud800", True), (UNICODE, False)])
def test_provider_output_encoding_boundary_preserves_review(journey, monkeypatch, stage, suffix, invalid):
    if stage == "generator":
        expected = journey.generator.output["answer"] + " " + suffix
        journey.generator.output["answer"] = expected
    else:
        expected = "Missing detail " + suffix
        original = journey.verifier.pydantic_prompt

        def verified(**kwargs):
            result = original(**kwargs)
            return kwargs["pydantic_model"](state="partial", evidence_ids=result.evidence_ids or [
                journey.verifier.calls[-1]["evidence"][0]["passage_id"]], uncovered=[expected])

        monkeypatch.setattr(journey.verifier, "pydantic_prompt", verified)
    result = run(journey)

    rows = [c.question_evidence[0] for c in result.candidates[:12]]
    if invalid:
        assert all((row["state"], row["reason"]) == ("unknown", stage + "_error") for row in rows)
        assert all("answer" not in row and row["evidence"] == row["uncovered"] == [] for row in rows)
        if stage == "generator":
            assert journey.verifier.calls == []
    elif stage == "generator":
        assert result.candidates[0].candidate_id == "P11"
        assert result.candidates[0].question_evidence[0]["answer"] == expected
    else:
        assert all(row["state"] == "partial" and row["uncovered"] == [expected] for row in rows)
    assert result.status == "reviewed" and len(journey.light) == 10 and len(journey.deep) == 4
    assert session.load(result.id).to_dict() == result.to_dict()


@pytest.mark.parametrize("suffix,invalid", [("\udfff", True), (UNICODE, False)])
def test_source_encoding_is_admitted_before_model_callbacks(journey, monkeypatch, suffix, invalid):
    text = "P11: original text\n" + QUOTE + suffix
    monkeypatch.setattr(evidence, "acquire_full_text", lambda *args, **kwargs: text)
    result = run(journey)

    if invalid:
        assert journey.generator.calls == journey.verifier.calls == []
        assert all(c.question_evidence[0]["state"] == "unknown" for c in result.candidates)
        assert all("source_sha256" not in c.question_evidence[0] for c in result.candidates)
    else:
        assert len(journey.generator.calls) == len(journey.verifier.calls) == 12
        assert result.candidates[0].question_evidence[0]["source_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert result.status == "reviewed" and len(journey.light) == 10


@pytest.mark.parametrize("state,availability", [("observed", "available"), ("failed", "partial"),
                                                 ("unknown", "partial")])
def test_context_encoding_is_admitted_before_generator_context(journey, state, availability):
    context = {"manual": {"item_key": "APP00001", "value": UNICODE, "revision": 7}, "verdict": None}
    comments = collect_comments("PAPER001", {"item_key": "PAPER001", "notes": [], "annotations": []},
                                context, "observed", state)
    record, = comments["records"]
    assert comments["availability"] == availability
    assert (record["text"], record["revision"], record["source_id"], record["authorship"]) == (
        UNICODE, 7, "APP00001", "user")
    result = run(journey)
    assert result.candidates[0].candidate_id == "P11"
    assert all(UNICODE not in prompt for prompt in journey.generator.calls)
    assert UNICODE not in json.dumps(journey.verifier.calls, ensure_ascii=False)
    assert UNICODE not in json.dumps(result.to_dict(), ensure_ascii=False)


@pytest.mark.parametrize("native,availability", [("observed", "empty"), ("failed", "unavailable"),
                                                 ("unknown", "unavailable")])
def test_encodable_empty_context_text_retains_the_existing_schema(native, availability):
    context = {"manual": {"item_key": "APP00001", "value": "", "revision": 7}, "verdict": None}
    comments = collect_comments("PAPER001", {"item_key": "PAPER001", "notes": [], "annotations": []},
                                context, "observed", native)
    assert comments["records"] == [] and comments["used_context_ids"] == []
    assert comments["availability"] == availability
    assert comments["app_state"] == "observed" and comments["native_state"] == native


@pytest.mark.parametrize("field", ["query", "questions"])
@pytest.mark.parametrize("suffix,invalid", [("\udfff", True), (UNICODE, False)])
def test_request_encoding_is_admitted_before_callbacks_or_receipts(journey, field, suffix, invalid):
    values = {"query": "Treatment studies", "questions": [QUESTION], "evidence_policy": "question_evidence"}
    values[field] = "Treatment " + suffix if field == "query" else [QUESTION + suffix]
    if invalid:
        with pytest.raises(ValueError):
            ScreenRequest(**values)
        with pytest.raises(ValueError):
            pipeline.run_screen(values["query"], values["questions"], deps=journey.deps,
                                evidence_policy="question_evidence")
    else:
        admitted = ScreenRequest(**values)
        assert getattr(admitted, field) == values[field]
    assert journey.acquired == journey.generator.calls == journey.verifier.calls == []


@pytest.mark.parametrize("suffix,invalid", [("\udfff", True), (UNICODE, False)])
def test_nonencodable_generated_locator_is_schema_invalid_before_verifier(journey, monkeypatch, suffix, invalid):
    quote = QUOTE + suffix
    journey.generator.output["quote"] = quote
    monkeypatch.setattr(evidence, "acquire_full_text", lambda candidate, **kw:
                        candidate.candidate_id + ": original text\n" + QUOTE + ("" if invalid else suffix))
    result = run(journey)
    if invalid:
        assert journey.verifier.calls == []
        assert all(c.question_evidence[0]["state"] == "unknown" for c in result.candidates)
        assert all(c.question_evidence[0]["evidence"] == [] for c in result.candidates)
    else:
        span, = result.candidates[0].question_evidence[0]["evidence"]
        assert span["quote"] == quote
        assert span["passage_id"] == f"chars:{span['start']}:{span['end']}"
    assert result.status == "reviewed" and len(journey.light) == 10 and len(journey.deep) == 4
