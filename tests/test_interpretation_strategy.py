"""Strategy relay machine controls; scripted native support is not scientific warrant."""
import builtins
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.test_digest_verification import PAPER, _claims, _digest
from tests.test_interpretation_producer import InterpretationDigestResponse, _base, _card, _payload
from tests.test_interpretation_verification import _table
from tests.test_review_source_boundary import _Mapper
from zotero_summarizer.integrations.llm import build_response_format
from zotero_summarizer.models import PaperDigest
from zotero_summarizer.services.library import _digest_audit, _map_reduce, quality_review
from zotero_summarizer.services.library._digest_verification import DigestVerifierUnavailable, _Checks
from zotero_summarizer.services.setup.bootstrap import _default_goals_config


@pytest.mark.parametrize("strategy", ["rank", "prefix", "map_reduce", "direct_map_reduce"])
@pytest.mark.parametrize("format_arg", [None, {}, {"type": "json_object"}])
@pytest.mark.parametrize("initial", ["empty", "prior_card"])
def test_explicit_collector_reaches_native_assessment_unchanged_with_original_evidence(monkeypatch, strategy, format_arg, initial):
    config = _default_goals_config()
    config.quality_review.chunk_strategy = "map_reduce" if strategy == "direct_map_reduce" else strategy
    source = PAPER + "\n\n" + "Background source context. " * 80 + "\n\n" + PAPER + " UNSELECTED_TAIL"
    cap, chunk_chars = 600, 600
    mapper = _Mapper("Generated notes, not original source evidence.")
    generator = Mock()
    generator.pydantic_prompt.return_value = _payload(_card())
    out = {} if initial == "empty" else {"origin": "ai_generated", "status": "schema_validated_ai_draft", **_card("Prior card.")}
    audit = {"prior_audit": True}
    assessment = Mock(wraps=_map_reduce.assess_digest)
    map_reduce = Mock(wraps=_map_reduce.map_reduce_digest)
    verification = Mock(wraps=quality_review._verify_generated_digest)
    monkeypatch.setattr(_map_reduce, "assess_digest", assessment)
    monkeypatch.setattr(_map_reduce, "map_reduce_digest", map_reduce)
    monkeypatch.setattr(quality_review, "_verify_generated_digest", verification)
    direct = strategy == "direct_map_reduce"
    invoke = _map_reduce.map_reduce_digest if direct else _map_reduce.digest_for_strategy
    extra = {"chunk_chars": chunk_chars, "sub_concurrency": 1} if direct else {
        "budget": _map_reduce.ChunkBudget(cap, chunk_chars, 1),
    }
    result = invoke("Paper", source, config, map_llm=mapper, reduce_llm=generator,
                    response_format=format_arg, verification_audit=audit, interpretation_out=out, **extra)
    mapped = strategy in {"map_reduce", "direct_map_reduce"}
    selected = source if mapped else (source[:cap] if strategy == "prefix" else
                                      quality_review.select_review_text([], source, budget=cap))
    assert type(result) is PaperDigest
    assert result.basis == ("map_reduce" if mapped else "full_text")
    assert "interpretation" not in result.model_dump(mode="json")
    assert assessment.call_count == verification.call_count == generator.pydantic_prompt.call_count == mapper.calls == 1
    assert assessment.call_args.kwargs["interpretation_out"] is out
    assert assessment.call_args.kwargs["verification_audit"] is audit
    assert verification.call_args.args[1] == selected
    if mapped:
        assert map_reduce.call_count == 1 and map_reduce.call_args.kwargs["interpretation_out"] is out
        chunks = _map_reduce.split_chunks(source, chunk_chars)
        combined = "\n\n".join(f"[chunk {i + 1}/{len(chunks)}]\n{mapper.note}" for i in range(len(chunks)))
        assert assessment.call_args.kwargs["full_text"] == combined
        assert assessment.call_args.kwargs["verification_text"] == source
        assert assessment.call_args.kwargs["max_chars"] == len(combined) + 1
        assert mapper.map_calls == len(chunks)
    else:
        map_reduce.assert_not_called()
        assert mapper.map_calls == 0
        assert "verification_text" not in assessment.call_args.kwargs
        assert len(source) > len(selected) and len(selected) <= cap
    assert audit["source"]["original_characters"] == len(source)
    assert audit["source"]["selected_characters"] == len(selected)
    assert audit["source"]["coverage"] == ("complete_extraction" if mapped else "partial")
    for passage in audit["passages"]:
        span = passage["selected_span"]
        assert passage["text"] == selected[span["start"]:span["end"]]
        for fragment in passage["original_fragments"]:
            original, chosen = fragment["original_span"], fragment["selected_span"]
            if original is not None:
                assert source[original["start"]:original["end"]] == selected[chosen["start"]:chosen["end"]]
    assert out["items"] == _card()["items"]
    assert out["origin"] == "ai_generated" and out["scientific_verification"] == "unverified"
    assert out["status"] == "schema_validated_ai_draft"
    assert audit["interpretation_evidence"]["status"] == "accepted_native_support"
    assert audit["verified_interpretation"] == out and "prior_audit" not in audit
    support = out["native_support"]
    count = len(_claims(PaperDigest.model_validate(audit["verified_digest"])))
    assert support["base_count"] == count and support["global_indices"] == list(range(count, count + 5))
    assert audit["verified_digest"]["basis"] == "full_text"
    assert support["base_digest_sha256"] == audit["identity"]["verified_digest_sha256"]
    assert support["base_digest_sha256"] == _digest_audit.canonical_hash(audit["verified_digest"])
    assert (support["base_digest_sha256"] != _digest_audit.canonical_hash(result.model_dump(mode="json"))) is mapped
    generated = generator.pydantic_prompt.call_args.kwargs
    assert generated["pydantic_model"] is InterpretationDigestResponse
    assert {key: value for key, value in generated.items() if key not in {"prompt", "pydantic_model"}} == (
        {"response_format": build_response_format(InterpretationDigestResponse)} if format_arg else {}
    )


@pytest.mark.parametrize("strategy", ["rank", "prefix", "map_reduce", "direct_map_reduce"])
@pytest.mark.parametrize("format_arg", [None, {}, {"type": "json_object"}])
@pytest.mark.parametrize("explicit_none", [False, True], ids=["omitted", "explicit_none"])
def test_off_preserves_native_kwargs_prompt_model_calls_and_no_optional_import_io(monkeypatch, strategy, format_arg, explicit_none):
    config = _default_goals_config()
    config.quality_review.chunk_strategy = "map_reduce" if strategy == "direct_map_reduce" else strategy
    config.prompts.paper_digest = "Title: {title}\nGoals: {research_goals}\nOriginal: {full_text}"
    mapper = _Mapper("Generation notes only.")
    generator = Mock()
    generator.pydantic_prompt.return_value = _digest()
    assessment = Mock(wraps=_map_reduce.assess_digest)
    no_io = Mock(side_effect=AssertionError("OFF must not load optional schema/contracts or do file IO"))
    original_import = builtins.__import__

    def no_c_import(name, *args, **kwargs):
        assert name != "zotero_summarizer.models.interpretation"
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(_map_reduce, "assess_digest", assessment)
    monkeypatch.setattr(builtins, "__import__", no_c_import)
    monkeypatch.setattr("builtins.open", no_io)
    monkeypatch.setattr(Path, "open", no_io)
    monkeypatch.setattr(_digest_audit, "_contract_hashes", no_io)
    direct = strategy == "direct_map_reduce"
    invoke = _map_reduce.map_reduce_digest if direct else _map_reduce.digest_for_strategy
    extra = {"chunk_chars": 8000, "sub_concurrency": 1} if direct else {
        "budget": _map_reduce.ChunkBudget(1000, 8000, 1),
    }
    if explicit_none:
        extra["interpretation_out"] = None
    result = invoke("Paper", PAPER, config, map_llm=mapper, reduce_llm=generator,
                    focus_prompt="Methods", response_format=format_arg, **extra)
    mapped = strategy in {"map_reduce", "direct_map_reduce"}
    generation_text = "[chunk 1/1]\n" + mapper.note if mapped else PAPER
    expected = {"title": "Paper", "full_text": generation_text, "config": config, "llm": generator,
                "focus_prompt": "Methods", "max_chars": len(generation_text) + 1 if mapped else 1000,
                "verifier_llm": mapper}
    if mapped:
        expected["verification_text"] = PAPER
        if format_arg:
            expected["response_format"] = format_arg
    else:
        expected.update(prefix=strategy == "prefix", response_format=format_arg)
    assert assessment.call_count == 1 and assessment.call_args.kwargs == expected
    assert "interpretation_out" not in assessment.call_args.kwargs
    assert "verification_audit" not in assessment.call_args.kwargs
    goals = "; ".join(g for g in (config.research_goals or []) if str(g).strip()) or "(not specified)"
    prompt = quality_review._digest_prompt(config.prompts.paper_digest, "Paper", generation_text, goals, "Methods")
    assert generator.pydantic_prompt.call_args.kwargs == {
        "prompt": prompt, "pydantic_model": PaperDigest,
        **({"response_format": format_arg} if format_arg else {}),
    }
    assert type(result) is PaperDigest and "interpretation" not in result.model_dump()
    assert result.model_dump() == _digest().model_copy(update={"basis": "map_reduce" if mapped else "full_text"}).model_dump()
    assert generator.pydantic_prompt.call_count == mapper.calls == 1
    assert mapper.map_calls == (1 if mapped else 0)
    no_io.assert_not_called()


@pytest.mark.parametrize("strategy", ["rank", "prefix", "map_reduce", "direct_map_reduce"])
@pytest.mark.parametrize("source", ["", " \n\t"])
def test_source_admission_failure_clears_prior_collector_before_any_native_call(monkeypatch, strategy, source):
    config = _default_goals_config()
    config.quality_review.chunk_strategy = "map_reduce" if strategy == "direct_map_reduce" else strategy
    mapper, generator = Mock(), Mock()
    out = {"origin": "ai_generated", "status": "schema_validated_ai_draft", **_card("Prior card.")}
    assessment = Mock(wraps=_map_reduce.assess_digest)
    monkeypatch.setattr(_map_reduce, "assess_digest", assessment)
    direct = strategy == "direct_map_reduce"
    invoke = _map_reduce.map_reduce_digest if direct else _map_reduce.digest_for_strategy
    extra = {"chunk_chars": 600} if direct else {"budget": _map_reduce.ChunkBudget(600, 600, 1)}
    with pytest.raises(ValueError, match="non-empty source text"):
        invoke("Paper", source, config, map_llm=mapper, reduce_llm=generator, interpretation_out=out, **extra)
    assert out == {}
    assessment.assert_not_called()
    mapper.prompt.assert_not_called()
    mapper.pydantic_prompt.assert_not_called()
    generator.pydantic_prompt.assert_not_called()


@pytest.mark.parametrize("direct", [False, True], ids=["dispatcher", "direct"])
@pytest.mark.parametrize("sub_concurrency", [1, 2], ids=["serial", "parallel"])
@pytest.mark.parametrize("failure", ["empty", "exception"])
def test_partial_map_failure_clears_prior_collector_without_reduction_or_verification(monkeypatch, direct, sub_concurrency, failure):
    config = _default_goals_config()
    config.quality_review.chunk_strategy = "map_reduce"
    source = PAPER * 15
    chunks = _map_reduce.split_chunks(source, 600)
    assert len(chunks) > 1
    mapper, generator = Mock(), Mock()
    failed = "" if failure == "empty" else RuntimeError("scripted map failure")
    mapper.prompt.side_effect = [PAPER, failed, *([PAPER] * (len(chunks) - 2))]
    out = {"origin": "ai_generated", "status": "schema_validated_ai_draft", **_card("Prior card.")}
    assessment = Mock(wraps=_map_reduce.assess_digest)
    monkeypatch.setattr(_map_reduce, "assess_digest", assessment)
    invoke = _map_reduce.map_reduce_digest if direct else _map_reduce.digest_for_strategy
    extra = {"chunk_chars": 600, "sub_concurrency": sub_concurrency} if direct else {
        "budget": _map_reduce.ChunkBudget(600, 600, sub_concurrency),
    }
    error = ValueError if failure == "empty" else RuntimeError
    message = "empty chunk summary" if failure == "empty" else "scripted map failure"
    with pytest.raises(error, match=message):
        invoke("Paper", source, config, map_llm=mapper, reduce_llm=generator, interpretation_out=out, **extra)
    assert out == {} and mapper.prompt.call_count >= 2
    assessment.assert_not_called()
    mapper.pydantic_prompt.assert_not_called()
    generator.pydantic_prompt.assert_not_called()


@pytest.mark.parametrize("strategy", ["rank", "prefix", "map_reduce", "direct_map_reduce"])
def test_native_parse_retry_and_base_correction_retain_only_final_collector_and_original_source(monkeypatch, strategy):
    config = _default_goals_config()
    config.quality_review.chunk_strategy = "map_reduce" if strategy == "direct_map_reduce" else strategy
    rejected = _payload(_card("Rejected attempt."))
    rejected["tldr"] = "Unreported 99999%."
    final_card = _card("Final attempt.")
    generator = Mock()
    generator.pydantic_prompt.side_effect = ["", rejected, _payload(final_card)]
    mapper = _Mapper("Generation notes mention 99999%, not source evidence.")
    verification = Mock(wraps=quality_review._verify_generated_digest)
    monkeypatch.setattr(quality_review, "_verify_generated_digest", verification)
    out, audit = {"prior_card": True}, {"prior_audit": True}
    direct = strategy == "direct_map_reduce"
    invoke = _map_reduce.map_reduce_digest if direct else _map_reduce.digest_for_strategy
    extra = {} if direct else {"budget": _map_reduce.ChunkBudget(1000, 8000, 1)}
    result = invoke("Paper", PAPER, config, map_llm=mapper, reduce_llm=generator,
                    interpretation_out=out, verification_audit=audit, response_format={"type": "json_object"}, **extra)
    assert type(result) is PaperDigest and generator.pydantic_prompt.call_count == 3
    assert verification.call_count == 2 and mapper.calls == 1
    assert all(call.args[1] == PAPER for call in verification.call_args_list)
    assert mapper.map_calls == (1 if strategy in {"map_reduce", "direct_map_reduce"} else 0)
    assert all(call.kwargs["pydantic_model"] is InterpretationDigestResponse for call in generator.pydantic_prompt.call_args_list)
    assert all(call.kwargs["response_format"] == build_response_format(InterpretationDigestResponse)
               for call in generator.pydantic_prompt.call_args_list)
    assert out["items"] == final_card["items"] and "prior_card" not in out
    assert audit["verified_interpretation"] == out and "prior_audit" not in audit
    assert [step["outcome"] for step in audit["lineage"]] == ["support_not_established", "accepted"]
    assert audit["passages"][0]["text"] == PAPER
    assert out["native_support"]["base_digest_sha256"] == audit["identity"]["verified_digest_sha256"]


@pytest.mark.parametrize("direct", [False, True], ids=["dispatcher", "direct"])
def test_optional_number_in_map_notes_is_not_original_source_support(direct):
    config = _default_goals_config()
    config.quality_review.chunk_strategy = "map_reduce"
    mapper = _Mapper("Generated notes claim an unreported 99999%.")
    generator = Mock()
    generator.pydantic_prompt.return_value = _payload(_card("Unreported 99999%."))
    out, audit = {"prior_card": True}, {}
    invoke = _map_reduce.map_reduce_digest if direct else _map_reduce.digest_for_strategy
    extra = {} if direct else {"budget": _map_reduce.ChunkBudget(1000, 8000, 1)}
    result = invoke("Paper", PAPER, config, map_llm=mapper, reduce_llm=generator,
                    interpretation_out=out, verification_audit=audit, **extra)
    assert type(result) is PaperDigest and result.basis == "map_reduce"
    assert generator.pydantic_prompt.call_count == mapper.map_calls == mapper.calls == 1
    assert out["status"] == "unavailable" and out["scientific_verification"] == "unverified"
    assert out["unavailable_reason"] == "native_optional_support_not_established"
    assert "items" not in out and "native_support" not in out and "prior_card" not in out
    count = len(_claims(_base()))
    assert out["optional_rejection"] == {"phase": "numeric", "base_count": count, "rejected_indices": [count + 1]}
    assert audit["passages"][0]["text"] == PAPER and audit["lineage"][0]["outcome"] == "accepted"


@pytest.mark.parametrize("direct", [False, True], ids=["dispatcher", "direct"])
def test_optional_semantic_rejection_reuses_native_base_without_reduce_regeneration(direct):
    config = _default_goals_config()
    config.quality_review.chunk_strategy = "map_reduce"
    mapper, generator = Mock(), Mock()
    mapper.prompt.return_value = "Generation notes only."
    count = len(_claims(_base()))
    mapper.pydantic_prompt.return_value = {"checks": list(reversed(_table(count + 5, rejected=(count + 1,))))}
    generator.pydantic_prompt.return_value = _payload(_card())
    out, audit = {"prior_card": True}, {}
    invoke = _map_reduce.map_reduce_digest if direct else _map_reduce.digest_for_strategy
    extra = {} if direct else {"budget": _map_reduce.ChunkBudget(1000, 8000, 1)}
    result = invoke("Paper", PAPER, config, map_llm=mapper, reduce_llm=generator,
                    interpretation_out=out, verification_audit=audit, **extra)
    assert type(result) is PaperDigest and result.basis == "map_reduce"
    assert generator.pydantic_prompt.call_count == mapper.prompt.call_count == mapper.pydantic_prompt.call_count == 1
    assert out["optional_rejection"] == {"phase": "semantic", "base_count": count, "rejected_indices": [count + 1]}
    assert out["status"] == "unavailable" and "items" not in out and "native_support" not in out
    assert audit["passages"][0]["text"] == PAPER and audit["lineage"][0]["outcome"] == "accepted"
    assert all(claim["index"] < count for claim in audit["claims"])


@pytest.mark.parametrize("strategy", ["rank", "prefix", "map_reduce", "direct_map_reduce"])
def test_native_unavailable_fallback_clears_both_collectors_and_never_returns_base(strategy):
    config = _default_goals_config()
    config.quality_review.chunk_strategy = "map_reduce" if strategy == "direct_map_reduce" else strategy
    mapper, generator = Mock(), Mock()
    mapper.prompt.return_value = "Generation notes only."
    mapper.pydantic_prompt.return_value = ""
    generator.pydantic_prompt.side_effect = [_payload(_card()), "", ""]
    out, audit = {"prior_card": True}, {"prior_audit": True}
    direct = strategy == "direct_map_reduce"
    invoke = _map_reduce.map_reduce_digest if direct else _map_reduce.digest_for_strategy
    extra = {} if direct else {"budget": _map_reduce.ChunkBudget(1000, 8000, 1)}
    with pytest.raises(DigestVerifierUnavailable):
        invoke("Paper", PAPER, config, map_llm=mapper, reduce_llm=generator,
               interpretation_out=out, verification_audit=audit, **extra)
    assert out == audit == {}
    assert mapper.pydantic_prompt.call_count == 2 and generator.pydantic_prompt.call_count == 3
    assert [call.kwargs["pydantic_model"] for call in generator.pydantic_prompt.call_args_list] == [
        InterpretationDigestResponse, _Checks, _Checks,
    ]
    assert mapper.prompt.call_count == (1 if strategy in {"map_reduce", "direct_map_reduce"} else 0)
