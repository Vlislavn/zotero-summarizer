"""Scripted same-call machine contracts; not model-policy or usefulness grading."""
import builtins
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_digest_verification import PAPER, _Verifier, _digest
from tests.test_review_source_boundary import _Mapper
from zotero_summarizer.integrations.llm import build_response_format
from zotero_summarizer.models import PaperDigest, ResearchProfile
from zotero_summarizer.models.research_feed import FeedDigestResponse
from zotero_summarizer.services.library import _feed_assessment, _map_reduce, deep_review, quality_review
from zotero_summarizer.services.library._digest_audit import canonical_hash
from zotero_summarizer.services.library._prompt_security import untrusted_input
from zotero_summarizer.services.setup.bootstrap import _default_goals_config


def profile():
    return ResearchProfile(themes=["evaluation"], projects=["agent harness"])


def assessment(source_id="paper", target="agent harness"):
    return {"projects": [{
        "source_id": source_id, "target_project": target, "assessment": "applicable",
        "use_kind": "use_as_benchmark", "rationale": "Evaluate held-out tool traces.",
        "mechanism": "Retain held-out trajectories", "target_component": "trace evaluator",
        "proposed_change": "Add held-out trajectory cases to the evaluator",
        "minimal_validation_experiment": "Compare recorded failures on the same held-out traces",
        "evidence_quote": PAPER,
    }], "reproduction": {"tier": "one_week", "rationale": "Held-out trace adapter needed.",
                           "missing_inputs": ["Trace adapter"], "evidence_quote": PAPER},
        "impact": {"research": 3, "production": 2, "personal_novelty": 4,
                   "rationale": "Held-out evaluation proposal.", "evidence_quote": PAPER}}


def reply(content):
    return {**_digest().model_dump(exclude_unset=True), "feed_assessment": content}


def produce_review(tmp_path, monkeypatch, *, selected_profile=None, source_id="paper", content=None,
                   strategy="rank", generator=None, mapper=None):
    selected_profile = selected_profile or profile()
    config = _default_goals_config()
    config.quality_review.chunk_strategy = strategy
    source = tmp_path / (source_id.replace(":", "-") + "-owned-source.pdf")
    source.write_text(PAPER, encoding="utf-8")
    reader = SimpleNamespace(get_item_detail=lambda _key: {"title": "Paper", "pdf_path": str(source)})
    extractor = SimpleNamespace(extract_text=lambda _path: PAPER)
    generator = generator or Mock()
    if generator.pydantic_prompt.side_effect is None:
        generator.pydantic_prompt.return_value = reply(content if content is not None else assessment(source_id))
    mapper = mapper or _Mapper("Generation notes, not original source.")
    monkeypatch.setattr(deep_review._deep_review_layers, "extra_layers", lambda ctx: (None, None, None, None, None))
    monkeypatch.setattr(deep_review, "_mirror_digest_note", Mock(return_value=(False, None)))
    context = _feed_assessment.context_for(source_id, selected_profile)
    entry = deep_review._review_one(
        {"item_key": source_id, "pdf_path": str(source)}, reader=reader, config=config,
        extractor=extractor, llm=generator, llm_map=mapper, quality_enabled=True,
        feed_context=context, response_format=build_response_format(PaperDigest),
        provenance={"provider": "scripted", "model": "owned", "prompt_schema_version": 3},
    )
    return entry, context, generator, mapper


def test_base_only_current_review_must_regenerate_for_explicit_feed(tmp_path, monkeypatch):
    """New control precedes the single approved old-fake premise migration."""
    from tests.test_research_feed import _review, _settings
    from zotero_summarizer.models import ResearchCandidate
    from zotero_summarizer.services.research_feed import runner

    settings = _settings(tmp_path)
    selected = profile()
    reviews = {"paper": _review("paper")}
    start = Mock()
    monkeypatch.setattr(deep_review, "get_current_review", lambda key: reviews.get(key))
    monkeypatch.setattr(deep_review, "start", start)
    monkeypatch.setattr(deep_review, "status", lambda key: {"status": "ready"})
    candidate = ResearchCandidate(source_id="paper", source="app_rss", title="Paper")

    outcome = runner._ensure_reviews(settings, [candidate], 1, selected)

    start.assert_called_once()
    assert start.call_args.kwargs["item_keys"] == ["paper"]
    assert start.call_args.kwargs["feed_contexts"]["paper"].model_dump(mode="json") == {
        "source_id": "paper", "profile": selected.model_dump(mode="json")}
    assert outcome == {"paper": "deep review did not produce a current feed assessment"}
    assert reviews["paper"] == _review("paper")


@pytest.mark.parametrize("strategy", ["rank", "prefix", "map_reduce"])
def test_selected_schema_same_call_source_and_actual_generation_provenance(tmp_path, monkeypatch, strategy):
    entry, context, generator, mapper = produce_review(tmp_path, monkeypatch, strategy=strategy)
    payload = entry["feed_assessment"]
    selected = _feed_assessment.qualify(entry, context)
    assert selected["qualified"] is True and selected["status"] == "assessed"
    assert generator.pydantic_prompt.call_count == mapper.calls == 1
    call = generator.pydantic_prompt.call_args.kwargs
    assert call["pydantic_model"] is FeedDigestResponse
    assert call["response_format"] == build_response_format(FeedDigestResponse)
    assert untrusted_input(context.model_dump_json()) in call["prompt"]
    assert payload["producer"]["prompt_sha256"] == canonical_hash(call["prompt"])
    assert payload["producer"]["schema_sha256"] == canonical_hash(FeedDigestResponse.model_json_schema())
    assert payload["producer"]["response_format_sha256"] == canonical_hash(call["response_format"])
    assert payload["producer"]["context"] == context.model_dump(mode="json")
    assert payload["producer"]["response_model"] == "FeedDigestResponse"
    assert payload["binding"]["review_identity_sha256"] == canonical_hash(entry["review_identity"])
    assert payload["binding"]["digest_sha256"] == canonical_hash(entry["digest"])
    assert payload["producer"]["original_text_sha256"] == entry["verification_audit"]["source"]["original_text_sha256"]
    assert payload["evidence"]["/projects/0"] == {"quote": PAPER, "start": 0, "end": len(PAPER), "pdf_page": None}
    assert payload["origin"] == "ai_generated" and payload["scientific_verification"] == "unverified"
    assert "feed_assessment" not in entry["digest"] and "interpretation_schema" not in entry["review_identity"]
    assert "feed_assessment" not in PaperDigest.model_json_schema()["properties"]
    assert mapper.map_calls == (1 if strategy == "map_reduce" else 0)
    assert PAPER in mapper.verification_prompts[0]
    assert "Generation notes, not original source." not in mapper.verification_prompts[0]


@pytest.mark.parametrize("form", ["dict", "json", "parsed"])
@pytest.mark.parametrize("case,status,reason", [
    ("missing", "not_assessed", "missing_assessment"), ("malformed", "invalid", "invalid_assessment"),
    ("source", "invalid", "project_source_binding_invalid"), ("project", "invalid", "project_source_binding_invalid"),
    ("duplicate", "invalid", "project_source_binding_invalid"), ("quote", "invalid", "quote_not_in_original"),
    ("component", "invalid", "invalid_assessment"), ("kind", "invalid", "invalid_assessment"),
    ("score_bool", "invalid", "invalid_assessment"), ("origin", "invalid", "invalid_assessment"),
])
def test_invalid_optional_never_retries_or_manufactures_a_positive(form, case, status, reason):
    content = assessment()
    if case == "missing":
        content = None
    elif case == "malformed":
        content = {"projects": "bad"}
    elif case in {"source", "project", "quote", "component", "kind"}:
        field, value = {"source": ("source_id", "foreign"), "project": ("target_project", "foreign"),
                        "quote": ("evidence_quote", PAPER.lower()), "component": ("target_component", ""),
                        "kind": ("use_kind", "invented")}[case]
        content["projects"][0][field] = value
    elif case == "duplicate":
        content["projects"] *= 2
    elif case == "score_bool":
        content["impact"]["production"] = True
    else:
        content["origin"] = "human_approved"
    raw = reply(content)
    raw = FeedDigestResponse.model_validate(raw) if form == "parsed" else json.dumps(raw) if form == "json" else raw
    generator, verifier, out = Mock(), _Verifier(), {"stale": True}
    generator.pydantic_prompt.return_value = raw
    result = quality_review.assess_digest(title="Paper", full_text=PAPER, config=_default_goals_config(),
        llm=generator, verifier_llm=verifier, feed_context=_feed_assessment.context_for("paper", profile()), feed_out=out)
    assert type(result) is PaperDigest and result.model_dump() == _digest().model_dump()
    assert out["status"] == status and out["reason"] == reason
    assert out["content"] is None and out["evidence"] == {}
    assert generator.pydantic_prompt.call_count == verifier.calls == 1


@pytest.mark.parametrize("case", ["no_fit", "unknown", "omitted_project"])
def test_assessed_no_fit_is_distinct_from_unknown_and_partial(case):
    content = assessment()
    record = content["projects"][0]
    record.update(assessment="not_applicable" if case == "no_fit" else "unknown",
                  use_kind="not_applicable" if case == "no_fit" else None,
                  mechanism="", target_component="", proposed_change="", minimal_validation_experiment="")
    if case == "omitted_project":
        content["projects"] = []
    out, generator = {}, Mock()
    generator.pydantic_prompt.return_value = reply(content)
    quality_review.assess_digest(title="Paper", full_text=PAPER, config=_default_goals_config(),
        llm=generator, verifier_llm=_Verifier(), feed_context=_feed_assessment.context_for("paper", profile()), feed_out=out)
    assert out["status"] == ("assessed" if case == "no_fit" else "partial")
    assert out["content"]["projects"] == content["projects"]
    assert generator.pydantic_prompt.call_count == 1


@pytest.mark.parametrize("entry", ["native", "dispatch", "map", "one_paper"])
def test_c_feed_conflict_refuses_before_reader_admission_mapping_or_generation(entry):
    context = _feed_assessment.context_for("paper", profile())
    no_work = Mock(side_effect=AssertionError("must refuse before work"))
    kwargs = {"interpretation_out": {}, "feed_context": context, "feed_out": {}}
    with pytest.raises(ValueError, match="conflicts"):
        if entry == "native":
            quality_review.assess_digest(title="Paper", full_text=PAPER, config=no_work, llm=no_work, **kwargs)
        elif entry == "dispatch":
            _map_reduce.digest_for_strategy("Paper", PAPER, no_work, map_llm=no_work,
                reduce_llm=no_work, budget=_map_reduce.ChunkBudget(100, 8000, 1), **kwargs)
        elif entry == "map":
            _map_reduce.map_reduce_digest("Paper", PAPER, no_work, map_llm=no_work, reduce_llm=no_work, **kwargs)
        else:
            deep_review._review_one({"item_key": "paper"}, reader=no_work, config=no_work,
                extractor=no_work, llm=no_work, quality_enabled=True,
                interpretation_schema="interpretation-digest-response-v1", feed_context=context)
    assert no_work.mock_calls == []


def test_map_quote_from_generated_notes_cannot_authorize_a_project_use():
    content = assessment()
    content["projects"][0]["evidence_quote"] = "Invented generation note."
    mapper, generator, out = _Mapper("Invented generation note."), Mock(), {}
    generator.pydantic_prompt.return_value = reply(content)
    result = _map_reduce.map_reduce_digest("Paper", PAPER, _default_goals_config(), map_llm=mapper,
        reduce_llm=generator, feed_context=_feed_assessment.context_for("paper", profile()), feed_out=out)
    assert result.basis == "map_reduce" and out["status"] == "invalid"
    assert out["reason"] == "quote_not_in_original" and out["content"] is None
    assert generator.pydantic_prompt.call_count == mapper.calls == mapper.map_calls == 1


@pytest.mark.parametrize("failure", ["source", "parse", "generation", "verifier", "mapping"])
def test_failure_clears_collector_without_publishing_first_attempt(failure):
    context, generator, out = _feed_assessment.context_for("paper", profile()), Mock(), {"old": True}
    generator.pydantic_prompt.return_value = reply(assessment())
    mapper = _Mapper("Notes.")
    text = "" if failure == "source" else PAPER
    if failure == "parse":
        generator.pydantic_prompt.return_value = ""
    elif failure == "generation":
        generator.pydantic_prompt.side_effect = RuntimeError("scripted failure")
    elif failure == "verifier":
        mapper.pydantic_prompt = Mock(return_value="")
        generator.pydantic_prompt.side_effect = [reply(assessment()), "", ""]
    elif failure == "mapping":
        mapper.note = ""
    with pytest.raises((ValueError, RuntimeError)):
        _map_reduce.map_reduce_digest("Paper", text, _default_goals_config(), map_llm=mapper,
            reduce_llm=generator, feed_context=context, feed_out=out)
    assert out == {}


def test_parse_retry_and_base_correction_use_final_same_call_assessment_only():
    first, corrected = reply(assessment()), reply(assessment())
    first["tldr"] = "Unsupported 99999%."
    corrected["feed_assessment"]["projects"][0]["proposed_change"] = "Final accepted change"
    generator, verifier, out = Mock(), _Verifier(), {}
    generator.pydantic_prompt.side_effect = ["", first, corrected]
    quality_review.assess_digest(title="Paper", full_text=PAPER, config=_default_goals_config(),
        llm=generator, verifier_llm=verifier, feed_context=_feed_assessment.context_for("paper", profile()),
        feed_out=out, response_format=build_response_format(PaperDigest))
    assert out["content"]["projects"][0]["proposed_change"] == "Final accepted change"
    assert len(generator.pydantic_prompt.call_args_list) == 3 and verifier.calls == 1
    for call in generator.pydantic_prompt.call_args_list:
        assert call.kwargs["pydantic_model"] is FeedDigestResponse
        assert call.kwargs["response_format"] == build_response_format(FeedDigestResponse)
    assert out["producer"]["prompt_sha256"] == canonical_hash(generator.pydantic_prompt.call_args.kwargs["prompt"])


@pytest.mark.parametrize("field", ["read_decision", "read_why", "writing_friction", "read_parts"])
def test_typed_feed_response_does_not_fill_missing_base_assessment(field):
    payload = reply(assessment())
    del payload[field]
    generator, out = Mock(), {"old": True}
    generator.pydantic_prompt.return_value = FeedDigestResponse.model_validate(payload)
    with pytest.raises(quality_review.DigestGenerationFailed, match="omitted required"):
        quality_review.assess_digest(title="Paper", full_text=PAPER, config=_default_goals_config(),
            llm=generator, verifier_llm=_Verifier(), feed_context=_feed_assessment.context_for("paper", profile()), feed_out=out)
    assert out == {} and generator.pydantic_prompt.call_count == 2


def test_map_verifier_fallback_keeps_original_and_one_feed_generation():
    from zotero_summarizer.services.library._digest_verification import _Checks

    mapper, generator, out = _Mapper("Generated notes only."), Mock(), {}
    mapper.pydantic_prompt = Mock(return_value={"checks": []})
    verifier = _Verifier()

    def answer(*, prompt, pydantic_model, **kwargs):
        if pydantic_model is FeedDigestResponse:
            return reply(assessment())
        assert pydantic_model is _Checks
        assert untrusted_input(PAPER) in prompt and "Generated notes only." not in prompt
        return verifier.pydantic_prompt(prompt=prompt, pydantic_model=pydantic_model, **kwargs)

    generator.pydantic_prompt.side_effect = answer
    _map_reduce.map_reduce_digest("Paper", PAPER, _default_goals_config(), map_llm=mapper,
        reduce_llm=generator, feed_context=_feed_assessment.context_for("paper", profile()), feed_out=out)
    assert out["status"] == "assessed"
    assert mapper.pydantic_prompt.call_count == 2 and verifier.calls == 1
    assert sum(call.kwargs["pydantic_model"] is FeedDigestResponse for call in generator.pydantic_prompt.call_args_list) == 1


def test_none_preserves_default_exact_call_schema_prompt_and_no_optional_io(monkeypatch):
    generator, verifier = Mock(), _Verifier()
    generator.pydantic_prompt.return_value = _digest()
    config = _default_goals_config()
    expected = quality_review._digest_prompt(config.prompts.paper_digest or quality_review._DEFAULT_DIGEST_PROMPT,
        "Paper", PAPER, "; ".join(config.research_goals) or "(not specified)", "")
    original_import = builtins.__import__

    def no_optional_import(name, *args, **kwargs):
        assert name not in {"zotero_summarizer.services.library._feed_assessment", "zotero_summarizer.models.interpretation"}
        return original_import(name, *args, **kwargs)

    no_io = Mock(side_effect=AssertionError("default must not open files"))
    monkeypatch.setattr(builtins, "__import__", no_optional_import)
    monkeypatch.setattr(Path, "open", no_io)
    result = quality_review.assess_digest(title="Paper", full_text=PAPER, config=config, llm=generator,
                                         verifier_llm=verifier, feed_context=None, feed_out=None)
    assert generator.pydantic_prompt.call_args.kwargs == {"prompt": expected, "pydantic_model": PaperDigest}
    assert result.model_dump() == _digest().model_dump() and generator.pydantic_prompt.call_count == verifier.calls == 1
    no_io.assert_not_called()
