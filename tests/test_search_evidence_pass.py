"""Original-source evidence through current opt-in owners, not a supplied-pass API."""
import hashlib
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from tests._search_evidence_fixtures import QUESTION, QUOTE, journey, run, stored_session  # noqa: F401
from zotero_summarizer.services.library import _review_cache
from zotero_summarizer.services.library._related_context import collect_comments
from zotero_summarizer.services.search import _question_evidence as evidence
from zotero_summarizer.services.search import materialize, pipeline, session

PRIVATE_COMMENT = "PRIVATE_CONTEXT_ONLY: Нет. This comment is not paper evidence."


def _receipt(result, candidate_id="P11"):
    candidate = next(c for c in result.candidates if c.candidate_id == candidate_id)
    row, = candidate.question_evidence
    return row


def test_shadow_assesses_explicit_eleven_before_cutoff_and_preserves_baseline(journey, monkeypatch):
    baseline = run(journey, policy="baseline", name="baseline")
    before = {c.candidate_id: asdict(c) for c in baseline.candidates}
    journey.acquired.clear()
    journey.light.clear()
    journey.deep.clear()
    events = []
    original = journey.verifier.pydantic_prompt
    light = pipeline.light_review

    def verify(**kwargs):
        reply = original(**kwargs)
        events.append("verify")
        return reply

    def reviewed(candidate, **kwargs):
        events.append("light")
        return light(candidate, **kwargs)

    monkeypatch.setattr(journey.verifier, "pydantic_prompt", verify)
    monkeypatch.setattr(pipeline, "light_review", reviewed)
    result = run(journey)
    assert events == ["verify"] * 12 + ["light"] * 10
    assert len(journey.generator.calls) == len(journey.verifier.calls) == 12
    assert "P11" in journey.light and "P11" in journey.deep and journey.acquired.count("P11") == 1
    assert _receipt(result)["state"] == "sufficient"
    for candidate in result.candidates:
        old = before[candidate.candidate_id]
        current = asdict(candidate)
        for field in ("question_evidence", "why", "relevance_band"):
            old.pop(field)
            current.pop(field)
        assert current == old


def test_receipt_retains_exact_second_occurrence_question_and_source_version(journey, monkeypatch):
    text = "Prelude.\n" + QUOTE + "\nBetween repeated passages.\n" + QUOTE + "\nEnd."
    second = text.rindex(QUOTE)
    monkeypatch.setattr(evidence, "acquire_full_text", lambda *a, **kw: text)
    monkeypatch.setattr(evidence, "retrieve_original_passages", lambda body, questions, **kw:
                        ([[{"start": second, "end": second + len(QUOTE), "text": QUOTE}] for _ in questions], {}))
    journey.verifier.state = "sufficient"
    result = run(journey)
    receipt = _receipt(result)
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert (receipt["candidate_id"], receipt["question_index"], receipt["question"], receipt["question_id"]) == (
        "P11", 0, QUESTION, hashlib.sha256(QUESTION.encode("utf-8")).hexdigest())
    assert receipt["source_version_id"] == receipt["source_sha256"] == sha
    assert receipt["checked_scope"] == {"start": 0, "end": len(text)}
    assert receipt["complete"] is False
    span, = receipt["evidence"]
    assert (span["start"], span["end"], span["quote"]) == (second, second + len(QUOTE), QUOTE)
    assert span["source_id"] == receipt["source_id"] and span["source_version_id"] == sha
    assert receipt["generator_id"] != receipt["verifier_id"]
    assert session.load(result.id).to_dict() == result.to_dict()


@pytest.mark.parametrize("state", ["sufficient", "partial", "not_found_in_checked_text", "unknown"])
def test_scripted_states_are_distinct_without_word_grading(journey, state):
    journey.verifier.state = state
    result = run(journey)
    receipt = _receipt(result)
    assert receipt["state"] == state and receipt["complete"] is False
    assert receipt["checked_passages"]
    assert len(journey.verifier.calls) == 12
    assert bool(receipt["uncovered"]) == (state == "partial")
    assert bool(receipt["evidence"]) == (state in {"sufficient", "partial"})


@pytest.mark.parametrize("mode,reason", [("missing", "source_empty"),
                                          ("oversized", "source_character_budget_exceeded")])
def test_incomplete_original_cannot_become_a_checked_negative(journey, monkeypatch, mode, reason):
    monkeypatch.setattr(evidence, "acquire_full_text", lambda *a, **kw: "" if mode == "missing" else "x" * 60001)
    journey.verifier.state = "not_found_in_checked_text"
    result = run(journey)
    assert (_receipt(result)["state"], _receipt(result)["reason"]) == ("unknown", reason)
    assert journey.generator.calls == journey.verifier.calls == []


@pytest.mark.parametrize("fault", ["negative_start", "boolean_start", "past_end", "mismatched_text",
                                   "missing_answer", "missing_quote", "foreign_quote", "ambiguous_quote"])
def test_invalid_generated_contract_is_rejected_before_semantic_verifier(journey, monkeypatch, fault):
    if fault in {"negative_start", "boolean_start", "past_end", "mismatched_text"}:
        def retrieve(text, questions, **kwargs):
            row = {"start": 0, "end": len(text), "text": text}
            row.update({"start": -1} if fault == "negative_start" else {"start": True} if fault == "boolean_start"
                       else {"end": len(text) + 1} if fault == "past_end" else {"text": "Foreign source"})
            return [[dict(row)] for _ in questions], {}
        monkeypatch.setattr(evidence, "retrieve_original_passages", retrieve)
    elif fault == "ambiguous_quote":
        acquire = evidence.acquire_full_text
        monkeypatch.setattr(evidence, "acquire_full_text", lambda c, **kw: acquire(c, **kw) + QUOTE)
    elif fault == "foreign_quote":
        journey.generator.output["quote"] = "A different paper's absent statement."
    else:
        journey.generator.output.pop("answer" if fault == "missing_answer" else "quote")
    result = run(journey)
    assert _receipt(result)["state"] == "unknown" and _receipt(result)["evidence"] == []
    assert journey.verifier.calls == []
    assert bool(journey.generator.calls) is (fault not in {
        "negative_start", "boolean_start", "past_end", "mismatched_text"})
    assert len(journey.light) == 10 and len(journey.deep) == 4


@pytest.mark.parametrize("fault", [{"state": "invented"}, {"evidence_ids": ["other"]},
                                   {"state": "sufficient", "uncovered": ["Uncovered"]}])
def test_malformed_verifier_reply_cannot_publish_a_semantic_status(journey, monkeypatch, fault):
    original = journey.verifier.pydantic_prompt

    def verify(**kwargs):
        reply = original(**kwargs).model_dump()
        reply.update(fault)
        return reply

    monkeypatch.setattr(journey.verifier, "pydantic_prompt", verify)
    result = run(journey)
    assert (_receipt(result)["state"], _receipt(result)["reason"]) == ("unknown", "verifier_error")
    assert len(journey.generator.calls) == len(journey.verifier.calls) == 12


@pytest.mark.parametrize("stage", ["generator", "verifier"])
def test_fake_provider_failure_is_unknown_not_a_scientific_negative(journey, stage):
    getattr(journey, stage).fail = True
    result = run(journey)
    assert (_receipt(result)["state"], _receipt(result)["reason"]) == ("unknown", stage + "_error")
    assert len(journey.generator.calls) == 12
    assert len(journey.verifier.calls) == (12 if stage == "verifier" else 0)
    assert "private synthetic" not in json.dumps(result.to_dict())


def _assert_private_comment_excluded(payload):
    assert PRIVATE_COMMENT not in json.dumps(payload, ensure_ascii=False), "Private comment leaked"


@pytest.mark.parametrize("target", ["source", "verifier"])
def test_privacy_oracle_rejects_deliberately_contaminated_payloads(target):
    source = {"text": QUOTE}
    payload = source if target == "source" else {"source": source, "answer": "Synthetic answer"}
    _assert_private_comment_excluded(payload)
    if target == "source":
        source["text"] += PRIVATE_COMMENT
    else:
        payload["answer"] = PRIVATE_COMMENT
    with pytest.raises(AssertionError, match="Private comment leaked"):
        _assert_private_comment_excluded(payload)


def test_private_comments_stay_context_and_never_original_evidence(journey, monkeypatch):
    context = {"manual": {"item_key": "APP00001", "value": PRIVATE_COMMENT, "revision": 7}, "verdict": None}
    comments = collect_comments("PAPER001", {}, context, "observed", "observed")
    assert comments["records"][0]["text"] == PRIVATE_COMMENT and comments["used_context_ids"] == []
    monkeypatch.setattr(materialize, "ZoteroWriter", lambda *a, **kw: pytest.fail("Evidence started native writer"))
    result = run(journey)
    assert _receipt(result)["state"] == "sufficient"
    for payload in [*journey.generator.calls, *journey.verifier.calls, result.to_dict()]:
        _assert_private_comment_excluded(payload)


@pytest.mark.parametrize("native,availability", [("observed", "empty"), ("failed", "unavailable")])
def test_original_assessment_does_not_conflate_absent_and_unavailable_comments(journey, native, availability):
    context = {"manual": {"item_key": "APP00001", "value": "", "revision": 7}, "verdict": None}
    comments = collect_comments("PAPER001", {}, context, "observed", native)
    assert comments["availability"] == availability and comments["records"] == []
    result = run(journey)
    assert _receipt(result)["state"] == "sufficient"


@pytest.mark.parametrize("raw", [b"{malformed", b'{"reviews": []}', b"\xff"])
def test_generated_cache_is_excluded_without_read_quarantine_or_empty_context(journey, monkeypatch, tmp_path, raw):
    cache = tmp_path / "deep_reviews.json"
    cache.write_bytes(raw)
    before = sorted(tmp_path.iterdir())
    monkeypatch.setattr(_review_cache, "settings", lambda: SimpleNamespace(model_dir=tmp_path))
    monkeypatch.setattr(_review_cache, "_read_all", lambda: pytest.fail("Evidence read generated cache"))
    monkeypatch.setattr(_review_cache, "get_current_review", lambda *a, **kw: pytest.fail("Evidence read generated cache"))
    result = run(journey)
    assert _receipt(result)["state"] == "sufficient" and cache.read_bytes() == raw
    assert sorted(p for p in tmp_path.iterdir() if p.name != "sessions") == before


@pytest.mark.parametrize("mode", ["baseline", "no_questions", "same_route", "disabled", "unavailable"])
def test_inactive_request_adds_zero_calls_and_preserves_ordinary_review(journey, monkeypatch, mode):
    if mode == "same_route":
        journey.deps.config.llm_routing.feed.model = journey.deps.config.llm_routing.deep_review.model
    elif mode == "disabled":
        monkeypatch.setenv("ZS_SEARCH_EVIDENCE_CALLS", "0")
    elif mode == "unavailable":
        journey.deps.extractor = None
    result = run(journey, policy="baseline" if mode == "baseline" else "question_evidence",
                 questions=[] if mode == "no_questions" else None)
    assert journey.generator.calls == journey.verifier.calls == []
    assert [c.candidate_id for c in result.candidates] == [f"P{n:02}" for n in range(1, 14)]
    assert len(journey.acquired) == len(journey.light) == 10 and len(journey.deep) == 4


def test_inactive_request_without_legacy_review_has_zero_acquisition_or_provider_calls(journey):
    research = stored_session(policy="baseline")
    session.save(research)
    result = pipeline.run_review(research.id, deps=journey.deps, top_n=0, k=0)
    assert result.status == "reviewed"
    assert journey.acquired == journey.generator.calls == journey.verifier.calls == []
    assert journey.light == journey.deep == []


def test_receipt_save_preserves_a_concurrent_explicit_add_key(journey):
    journey.generator.on_call = lambda: session.update("question-evidence", lambda current: setattr(
        current.candidates[10], "materialized_zotero_key", "ADDED011"))
    result = run(journey)
    assert _receipt(result)["state"] == "sufficient"
    assert next(c for c in result.candidates if c.candidate_id == "P11").materialized_zotero_key == "ADDED011"


def test_generator_status_is_not_verifier_authority(journey):
    journey.generator.output["state"] = "sufficient"
    journey.verifier.state = "unknown"
    result = run(journey)
    assert all(c.question_evidence[0]["state"] == "unknown" for c in result.candidates)
    assert len(journey.verifier.calls) == 12 and result.evidence_status["promoted_candidate_ids"] == []
