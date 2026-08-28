"""QualityReview grade normalization + the row_quality parse contract."""
from __future__ import annotations

import pytest

from zotero_summarizer.models import PaperDigest, QualityReview
from zotero_summarizer.services.triage.daily_select import _candidate as cand


def test_quality_review_grade_normalization():
    assert QualityReview(grade="a").grade == "A"
    assert QualityReview(grade="B)").grade == "B"
    assert QualityReview(grade="x").grade == ""
    old = PaperDigest.model_validate({"read_decision": "read"})
    assert old.skip_parts == [] and old.estimated_read_minutes is None and old.original_value == ""
    skip = PaperDigest(read_decision="skip", read_parts=["Methods"], original_value="nuance")
    assert skip.read_parts == [] and skip.original_value == ""
    with pytest.raises(ValueError, match="exact read target"):
        PaperDigest(read_decision="skim")
    friction = PaperDigest(read_decision="read", read_parts=["§2"], writing_friction="high",
                           writing_reasons=["Core construct is used before it is defined."])
    assert friction.read_decision == "skim"
    with pytest.raises(ValueError, match="concrete reason"):
        PaperDigest(writing_friction="high")


def test_digest_prompt_is_conservative_and_action_specific():
    from zotero_summarizer.services.library.quality_review import _DEFAULT_DIGEST_PROMPT

    for required in ("Start at `skip`", "ONLY if all hold", '"skip_parts"',
                     '"estimated_read_minutes"', '"original_value"', '"writing_friction"',
                     "BEFORE choosing", "one stated axis", "weak evidence", "zero counterexamples"):
        assert required in _DEFAULT_DIGEST_PROMPT


def test_digest_llm_cannot_silently_default_the_writing_assessment():
    from zotero_summarizer.services.library.quality_review import _coerce_digest

    with pytest.raises(ValueError, match="omitted required reading or writing"):
        _coerce_digest('{"read_decision":"skip"}')


def test_new_reading_action_requires_decision_evidence():
    from zotero_summarizer.services.library.quality_review import _coerce_digest

    base = {
        "read_decision": "read", "read_why": "Changes the design decision.",
        "read_parts": ["§4.2, Table 3"], "skip_parts": [],
        "estimated_read_minutes": 18, "original_value": "The ablation details.",
        "writing_friction": "low", "writing_reasons": [],
    }
    assert _coerce_digest(base).read_decision == "read"
    for missing in ("read_why", "read_parts", "estimated_read_minutes", "original_value"):
        broken = {**base, missing: [] if missing == "read_parts" else (None if missing == "estimated_read_minutes" else "")}
        with pytest.raises(ValueError):
            _coerce_digest(broken)


def test_row_quality_parse_and_contract():
    assert cand.row_quality({"quality_review_json": ""}) == {}
    assert cand.row_quality({"quality_review_json": '{"grade":"B"}'})["grade"] == "B"
    with pytest.raises(ValueError):
        cand.row_quality({"quality_review_json": "[1,2]"})  # JSON, but not an object


def test_build_response_format_is_strict_json_schema():
    """The decoder-level constraint: {type: json_schema, strict: true} wrapping the
    PaperDigest schema. A vLLM-style endpoint enforces this at the logit level."""
    from zotero_summarizer.models import PaperDigest
    from zotero_summarizer.services.library.quality_review import build_response_format

    rf = build_response_format(PaperDigest)
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["name"] == "PaperDigest"
    assert "properties" in rf["json_schema"]["schema"]  # the real PaperDigest schema


class _RfCaptureLLM:
    """Captures the response_format kwarg passed to pydantic_prompt; returns a minimal
    valid PaperDigest so assess_digest's parse path succeeds."""
    def __init__(self):
        self.captured_rf = "UNSET"

    def pydantic_prompt(self, *, prompt, pydantic_model, **kwargs):
        self.captured_rf = kwargs.get("response_format", None)
        return PaperDigest(
            tldr="ok", read_decision="skip", read_why="The digest is sufficient.",
            read_parts=[], skip_parts=[], estimated_read_minutes=None, original_value="",
            writing_friction="low", writing_reasons=[],
        )


def test_assess_digest_threads_response_format_to_llm():
    """assess_digest must forward response_format to pydantic_prompt (the decoder-level path).
    Regression for the structured-output wiring (default None = prompt-level)."""
    from zotero_summarizer.services.library.quality_review import assess_digest, build_response_format
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config

    cfg = _default_goals_config()
    rf = build_response_format(PaperDigest)
    llm = _RfCaptureLLM()
    assess_digest(title="T", full_text="body text " * 200, config=cfg, llm=llm,
                  max_chars=60000, response_format=rf)
    assert llm.captured_rf is rf  # forwarded to the pydantic_prompt call
