"""Native machine-contract controls; scripted support is not scientific warrant."""
from unittest.mock import Mock

import pytest

from tests.test_digest_verification import PAPER, _Verifier, _claims, _digest
from tests.test_interpretation_producer import _base, _card, _payload
from zotero_summarizer.models import PaperDigest
from zotero_summarizer.models.interpretation import InterpretationDigestResponse
from zotero_summarizer.services.library import _digest_audit, _digest_verification, quality_review
from zotero_summarizer.services.library._digest_verification import (
    DigestSourceRejected, DigestVerifierUnavailable, _Checks, verify_digest,
)
from zotero_summarizer.services.setup.bootstrap import _default_goals_config


def _table(count, *, rejected=(), optional_start=None):
    return [{"index": index, "supported": index not in rejected,
             "evidence": [] if index in rejected else [1 if optional_start is not None and index >= optional_start else 0]}
            for index in range(count)]


def test_closed_projection_uses_five_roles_and_native_sentence_parts_in_supplied_order():
    base = _digest("First finding. Second finding.").model_copy(update={
        "key_findings": ["Third finding; Fourth finding.", "Fifth finding."],
    })
    assert _claims(base) == [
        "tldr: First finding.", "tldr: Second finding.", "read_why: The digest is sufficient.",
        "key_findings[0]: Third finding;", "key_findings[0]: Fourth finding.", "key_findings[1]: Fifth finding.",
    ]
    card = {"items": [
        {"finding_ref": "/tldr", "inference_strength": "First inference. More inference.",
         "rationale": "First rationale; More rationale.", "scope": "First scope.",
         "uncertainties": ["First uncertainty. More uncertainty.", "Second uncertainty."],
         "unsupported_extrapolations": ["First extension.", "Second extension."], "basis_refs": []},
        {"finding_ref": "/key_findings/1", "inference_strength": "Last inference.", "rationale": "Last rationale.",
         "scope": "Last scope.", "uncertainties": ["Last uncertainty."],
         "unsupported_extrapolations": ["Last extension."], "basis_refs": []},
    ]}
    out = {}
    quality_review._coerce_digest({**base.model_dump(exclude_unset=True), "interpretation": card}, interpretation_out=out)
    out.update(origin="metadata 77777", provenance={"count": 99999})
    expected = (
        "interpretation[0](/tldr).inference_strength: First inference.",
        "interpretation[0](/tldr).inference_strength: More inference.",
        "interpretation[0](/tldr).rationale: First rationale;",
        "interpretation[0](/tldr).rationale: More rationale.",
        "interpretation[0](/tldr).scope: First scope.",
        "interpretation[0](/tldr).uncertainties[0]: First uncertainty.",
        "interpretation[0](/tldr).uncertainties[0]: More uncertainty.",
        "interpretation[0](/tldr).uncertainties[1]: Second uncertainty.",
        "interpretation[0](/tldr).unsupported_extrapolations[0]: First extension.",
        "interpretation[0](/tldr).unsupported_extrapolations[1]: Second extension.",
        "interpretation[1](/key_findings/1).inference_strength: Last inference.",
        "interpretation[1](/key_findings/1).rationale: Last rationale.",
        "interpretation[1](/key_findings/1).scope: Last scope.",
        "interpretation[1](/key_findings/1).uncertainties[0]: Last uncertainty.",
        "interpretation[1](/key_findings/1).unsupported_extrapolations[0]: Last extension.",
    )
    assert quality_review._interpretation_claims(out) == expected
    assert quality_review._interpretation_claims({"status": "unavailable"}) == ()
    assert quality_review._interpretation_claims(None) == ()
    verifier = Mock(wraps=_Verifier())
    verify_digest(base, PAPER, verifier, optional_claims=expected)
    prompt = verifier.pydantic_prompt.call_args.kwargs["prompt"]
    assert "[6] " + expected[0] in prompt and "[20] " + expected[-1] in prompt
    assert "77777" not in prompt and "99999" not in prompt


def test_numeric_rejection_is_typed_sorted_and_has_no_model_call_or_checks():
    base = _digest("First finding. Second finding.")
    optional = ("interpretation[0](/tldr).scope: Unreported 99999%.",)
    verifier = _Verifier()
    with pytest.raises(DigestSourceRejected) as caught:
        verify_digest(base, PAPER, verifier, optional_claims=optional)
    exc = caught.value
    assert exc.phase == "numeric" and exc.base_count == len(_claims(base)) == 3
    assert exc.rejected_indices == (3,) and exc.checks is None and verifier.calls == 0
    legacy = DigestSourceRejected("legacy")
    assert str(legacy) == "legacy" and legacy.phase is None and legacy.checks is None


def test_semantic_rejection_exposes_a_complete_deep_copy_with_global_indices():
    base = _digest()
    count = len(_claims(base))
    raw = _Checks.model_validate({"checks": list(reversed(_table(count + 2, rejected=(count + 1,))))})
    verifier = Mock()
    verifier.pydantic_prompt.return_value = raw
    with pytest.raises(DigestSourceRejected) as caught:
        verify_digest(base, PAPER, verifier, optional_claims=("optional: First.", "optional: Second."))
    exc = caught.value
    assert exc.phase == "semantic" and exc.base_count == count and exc.rejected_indices == (count + 1,)
    assert exc.checks.model_dump() == raw.model_dump()
    raw.checks[-1].evidence.clear()
    assert exc.checks.checks[-1].evidence == [0]
    assert verifier.pydantic_prompt.call_count == 1


def test_optional_numeric_rejection_removes_whole_card_then_verifies_base_once():
    card = _card("Unreported 99999%.")
    card["items"].append({**_card()["items"][0], "finding_ref": "/tldr"})
    generator = Mock()
    generator.pydantic_prompt.return_value = _payload(card)
    verifier, out, audit = Mock(wraps=_Verifier()), {}, {}
    result = quality_review.assess_digest(title="Paper", full_text=PAPER, config=_default_goals_config(),
                                         llm=generator, verifier_llm=verifier, interpretation_out=out,
                                         verification_audit=audit)
    count = len(_claims(result))
    diagnostic = {"phase": "numeric", "base_count": count, "rejected_indices": [count + 1]}
    assert result.model_dump() == _base().model_dump()
    assert out == {"origin": "ai_generated", "scientific_verification": "unverified", "status": "unavailable",
                   "unavailable_reason": "native_optional_support_not_established", "optional_rejection": diagnostic}
    assert generator.pydantic_prompt.call_count == verifier.pydantic_prompt.call_count == 1
    assert "interpretation[" not in verifier.pydantic_prompt.call_args.kwargs["prompt"]
    assert audit["lineage"][0]["optional_rejection"] == diagnostic
    assert audit["lineage"][0]["outcome"] == "accepted"
    assert {claim["field"] for claim in audit["claims"]} == {"tldr", "key_findings"}


def test_reversed_optional_semantic_rejection_reuses_only_complete_supported_base(monkeypatch):
    card = _card()
    card["items"].append({**card["items"][0], "finding_ref": "/tldr"})
    text = PAPER + " " + "Additional extracted study context uses held-out comparisons. " * 24
    count = len(_claims(_base()))
    rejected = count + 9
    verifier = Mock()
    verifier.pydantic_prompt.return_value = {"checks": list(reversed(
        _table(count + 10, rejected=(rejected,), optional_start=count)
    ))}
    generator = Mock()
    generator.pydantic_prompt.return_value = _payload(card)
    audit_builder = Mock(wraps=_digest_audit.build_verification_audit)
    monkeypatch.setattr(_digest_audit, "build_verification_audit", audit_builder)
    out, audit = {}, {}
    result = quality_review.assess_digest(title="Paper", full_text=text, config=_default_goals_config(),
                                         llm=generator, verifier_llm=verifier, interpretation_out=out,
                                         verification_audit=audit)
    passed_checks = audit_builder.call_args.args[1]
    assert [check.index for check in passed_checks.checks] == list(range(count))
    assert all(check.supported and check.evidence == [0] for check in passed_checks.checks)
    assert type(result) is PaperDigest and result.model_dump() == _base().model_dump()
    assert out["status"] == "unavailable" and "items" not in out and "native_support" not in out
    assert out["scientific_verification"] == "unverified"
    assert out["optional_rejection"] == {"phase": "semantic", "base_count": count, "rejected_indices": [rejected]}
    assert generator.pydantic_prompt.call_count == verifier.pydantic_prompt.call_count == 1
    assert all(claim["index"] < count for claim in audit["claims"])
    assert [passage["id"] for passage in audit["passages"]] == [0]
    assert audit["contradiction_adjudication"] == "not_performed"


def test_accepted_optional_support_is_separate_from_base_audit_and_scientific_state(monkeypatch):
    text = PAPER + " " + "Additional extracted study context uses held-out comparisons. " * 24
    count = len(_claims(_base()))
    verifier = Mock()
    verifier.pydantic_prompt.return_value = {"checks": list(reversed(_table(count + 5, optional_start=count)))}
    generator = Mock()
    generator.pydantic_prompt.return_value = _payload(_card())
    builder = Mock(wraps=_digest_audit.build_verification_audit)
    monkeypatch.setattr(_digest_audit, "build_verification_audit", builder)
    out, audit = {}, {}
    result = quality_review.assess_digest(title="Paper", full_text=text, config=_default_goals_config(),
                                         llm=generator, verifier_llm=verifier, interpretation_out=out,
                                         verification_audit=audit)
    assert [check.index for check in builder.call_args.args[1].checks] == list(range(count))
    assert [passage["id"] for passage in audit["passages"]] == [0]
    assert "native_support" not in audit and "interpretation" not in result.model_dump()
    assert out["status"] == "schema_validated_ai_draft" and out["origin"] == "ai_generated"
    assert out["scientific_verification"] == "unverified" and out["items"] == _card()["items"]
    support = out["native_support"]
    assert support["base_count"] == count and support["global_indices"] == list(range(count, count + 5))
    assert support["base_digest_sha256"] == _digest_audit.canonical_hash(result.model_dump(mode="json"))
    assert support["optional_claims"] == list(quality_review._interpretation_claims(out))
    assert support["validated_checks"] == _table(count + 5, optional_start=count)[count:]
    assert generator.pydantic_prompt.call_count == verifier.pydantic_prompt.call_count == 1


@pytest.mark.parametrize("phase", ["numeric", "semantic"])
@pytest.mark.parametrize("mixed", [False, True])
def test_base_or_mixed_rejection_uses_one_correction_with_fresh_projection_and_offset(phase, mixed):
    first = _payload(_card("First rationale."))
    corrected_card = _card("Corrected rationale.")
    corrected_card["items"][0]["finding_ref"] = "/tldr"
    corrected = {**_payload(corrected_card), "tldr": "Corrected finding. Another corrected finding."}
    first_count = len(_claims(_base()))
    corrected_base = _base().model_copy(update={"tldr": corrected["tldr"]})
    count = len(_claims(corrected_base))
    generator = Mock()
    verifier = Mock()
    if phase == "numeric":
        first["tldr"] = "Unreported 99999%."
        if mixed:
            first["interpretation"]["items"][0]["rationale"] = "Unreported 88888%."
        verifier.pydantic_prompt.return_value = {"checks": _table(count + 5)}
    else:
        rejected = (0, first_count + 1) if mixed else (0,)
        verifier.pydantic_prompt.side_effect = [{"checks": _table(first_count + 5, rejected=rejected)},
                                              {"checks": list(reversed(_table(count + 5)))}]
    generator.pydantic_prompt.side_effect = [first, corrected]
    out, audit = {}, {}
    result = quality_review.assess_digest(title="Paper", full_text=PAPER, config=_default_goals_config(),
                                         llm=generator, verifier_llm=verifier, interpretation_out=out,
                                         verification_audit=audit)
    assert result.model_dump() == corrected_base.model_dump() and count == first_count + 1
    assert generator.pydantic_prompt.call_count == 2
    assert verifier.pydantic_prompt.call_count == (1 if phase == "numeric" else 2)
    assert "failed source verification" in generator.pydantic_prompt.call_args_list[1].kwargs["prompt"]
    assert out["items"] == corrected_card["items"] and out["scientific_verification"] == "unverified"
    assert out["native_support"]["base_count"] == count
    assert out["native_support"]["global_indices"] == list(range(count, count + 5))
    assert all("First rationale" not in claim for claim in out["native_support"]["optional_claims"])
    assert "interpretation[0](/tldr).rationale: Corrected rationale." in out["native_support"]["optional_claims"]
    assert [entry["outcome"] for entry in audit["lineage"]] == ["support_not_established", "accepted"]
    assert "optional_rejection" not in out


@pytest.mark.parametrize("phase", ["numeric", "semantic"])
def test_second_base_rejection_fails_closed_and_clears_both_public_collectors(phase):
    payload = _payload(_card())
    if phase == "numeric":
        payload["tldr"] = "Unreported 99999%."
    generator = Mock()
    generator.pydantic_prompt.side_effect = [payload, payload]
    verifier = _Verifier(supported=False)
    out, audit = {"stale": True}, {"stale": True}
    with pytest.raises(DigestSourceRejected):
        quality_review.assess_digest(title="Paper", full_text=PAPER, config=_default_goals_config(),
                                     llm=generator, verifier_llm=verifier, interpretation_out=out,
                                     verification_audit=audit)
    assert out == audit == {} and generator.pydantic_prompt.call_count == 2
    assert verifier.calls == (0 if phase == "numeric" else 2)


def test_optional_numeric_removal_cannot_accept_a_semantically_rejected_base():
    generator = Mock()
    generator.pydantic_prompt.side_effect = [_payload(_card("Unreported 99999%.")), _payload(_card())]
    verifier, out = _Verifier(supported=False), {"stale": True}
    with pytest.raises(DigestSourceRejected):
        quality_review.assess_digest(title="Paper", full_text=PAPER, config=_default_goals_config(),
                                     llm=generator, verifier_llm=verifier, interpretation_out=out)
    assert out == {} and generator.pydantic_prompt.call_count == verifier.calls == 2


@pytest.mark.parametrize("defect", ["missing_base", "duplicate_base", "invalid_optional_evidence"])
@pytest.mark.parametrize("recovery", ["repair", "fallback", "unavailable"])
def test_malformed_combined_checks_never_partially_accept_and_fallback_keeps_projection(defect, recovery):
    count = len(_claims(_base()))
    valid = _table(count + 5, rejected=(count + 4,))
    bad = _table(count + 5, rejected=(count + 4,))
    if defect == "missing_base":
        bad = bad[1:]
    elif defect == "duplicate_base":
        bad[1] = dict(bad[0])
    else:
        bad[count]["evidence"] = [999]
    verifier, generator = Mock(), Mock()
    verifier.pydantic_prompt.side_effect = [{"checks": bad}, {"checks": valid if recovery == "repair" else bad}]
    generator.pydantic_prompt.side_effect = [_payload(_card())] + (
        [{"checks": valid}] if recovery == "fallback" else [{"checks": bad}, {"checks": bad}]
    )
    out, audit = {"stale": True}, {"stale": True}
    if recovery == "unavailable":
        with pytest.raises(DigestVerifierUnavailable):
            quality_review.assess_digest(title="Paper", full_text=PAPER, config=_default_goals_config(),
                                         llm=generator, verifier_llm=verifier, interpretation_out=out,
                                         verification_audit=audit)
        assert out == audit == {} and generator.pydantic_prompt.call_count == 3
    else:
        quality_review.assess_digest(title="Paper", full_text=PAPER, config=_default_goals_config(),
                                     llm=generator, verifier_llm=verifier, interpretation_out=out,
                                     verification_audit=audit)
        assert out["status"] == "unavailable" and "native_support" not in out
        assert audit["lineage"][0]["outcome"] == "accepted"
        assert audit["lineage"][0]["optional_rejection"]["phase"] == "semantic"
        assert generator.pydantic_prompt.call_count == (2 if recovery == "fallback" else 1)
    assert verifier.pydantic_prompt.call_count == 2
    assert generator.pydantic_prompt.call_args_list[0].kwargs["pydantic_model"] is InterpretationDigestResponse
    if recovery != "repair":
        first_prompt = verifier.pydantic_prompt.call_args_list[0].kwargs["prompt"]
        fallback_call = generator.pydantic_prompt.call_args_list[1]
        assert fallback_call.kwargs == {"prompt": first_prompt, "pydantic_model": _Checks}
        assert "interpretation[0](/key_findings/0).rationale:" in first_prompt


@pytest.mark.parametrize("defect", ["missing_base", "duplicate_base", "invalid_optional_evidence", "wrong_offset", "wrong_rejection", "no_checks"])
def test_invalid_typed_rejection_cannot_be_reused_or_trigger_generation_correction(monkeypatch, defect):
    count = len(_claims(_base()))
    rows = _table(count + 5, rejected=(count,))
    if defect == "missing_base":
        rows = rows[1:]
    elif defect == "duplicate_base":
        rows[1] = dict(rows[0])
    elif defect == "invalid_optional_evidence":
        rows[count + 1]["evidence"] = [999]
    exc = DigestSourceRejected("scripted typed rejection", phase="semantic",
                               base_count=count + (defect == "wrong_offset"),
                               rejected_indices=(count + 1 if defect == "wrong_rejection" else count,),
                               checks=None if defect == "no_checks" else _Checks.model_validate({"checks": rows}))
    monkeypatch.setattr(quality_review, "_verify_generated_digest", Mock(side_effect=exc))
    generator = Mock()
    generator.pydantic_prompt.return_value = _payload(_card())
    out = {"stale": True}
    with pytest.raises(DigestVerifierUnavailable):
        quality_review.assess_digest(title="Paper", full_text=PAPER, config=_default_goals_config(),
                                     llm=generator, verifier_llm=_Verifier(), interpretation_out=out)
    assert out == {} and generator.pydantic_prompt.call_count == 1


@pytest.mark.parametrize("card", [None, _card()])
def test_c_never_accepts_unknown_checks_as_a_valid_base_or_empty_card(monkeypatch, card):
    monkeypatch.setattr(quality_review, "_verify_generated_digest", Mock(return_value=None))
    generator = Mock()
    generator.pydantic_prompt.return_value = _payload(card)
    out = {"stale": True}
    with pytest.raises(DigestVerifierUnavailable):
        quality_review.assess_digest(title="Paper", full_text=PAPER, config=_default_goals_config(),
                                     llm=generator, verifier_llm=_Verifier(), interpretation_out=out)
    assert out == {} and generator.pydantic_prompt.call_count == 1


def test_optional_numeric_rejection_cannot_pass_when_base_verifiers_are_unavailable():
    generator, verifier = Mock(), Mock()
    generator.pydantic_prompt.side_effect = [_payload(_card("Unreported 99999%.")), "", ""]
    verifier.pydantic_prompt.return_value = ""
    out, audit = {"stale": True}, {"stale": True}
    with pytest.raises(DigestVerifierUnavailable):
        quality_review.assess_digest(title="Paper", full_text=PAPER, config=_default_goals_config(),
                                     llm=generator, verifier_llm=verifier, interpretation_out=out,
                                     verification_audit=audit)
    assert out == audit == {} and verifier.pydantic_prompt.call_count == 2
    assert generator.pydantic_prompt.call_count == 3
    assert all(call.kwargs["pydantic_model"] is _Checks for call in generator.pydantic_prompt.call_args_list[1:])
    assert all("interpretation[" not in call.kwargs["prompt"] for call in verifier.pydantic_prompt.call_args_list)


def test_off_does_not_pass_optional_keyword_to_the_existing_verifier(monkeypatch):
    verifier_call = Mock(wraps=verify_digest)
    monkeypatch.setattr(_digest_verification, "verify_digest", verifier_call)
    generator = Mock()
    generator.pydantic_prompt.return_value = _digest()
    quality_review.assess_digest(title="Paper", full_text=PAPER, config=_default_goals_config(),
                                 llm=generator, verifier_llm=_Verifier())
    assert set(verifier_call.call_args.kwargs) == {"research_goals"}
    assert generator.pydantic_prompt.call_args.kwargs["pydantic_model"] is PaperDigest
