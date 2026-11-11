"""Incomplete evaluator output is unknown assessment, never a smaller perfect rubric."""
import pytest

from zotero_summarizer.models import PaperDigest
from zotero_summarizer.services.library import _paper_type_checklists as pc, _quality_prompts as qp, quality_eval

BODY = "The central contribution is explicitly stated and supported with enough source detail for this synthetic admission check."


class RubricClient:
    def __init__(self, samples):
        self.samples = iter(samples)
        self.calls = []

    def pydantic_prompt(self, *, pydantic_model, **_kwargs):
        self.calls.append(pydantic_model)
        if pydantic_model is qp.RubricLLMResponse:
            checks = next(self.samples)
            return qp.RubricLLMResponse(checks=checks, evidence={key: BODY for key in checks})
        if pydantic_model is qp.OverstatementLLMResponse:
            return qp.OverstatementLLMResponse(overstatements=[])
        raise AssertionError("Unexpected assessment call")


def evaluate(client, paper_type, runs=1):
    return quality_eval.evaluate_quality(title="Synthetic", full_text=BODY, sections=[], digest={},
                                        llm=client, max_chars=2000, paper_type=paper_type,
                                        self_consistency_runs=runs)


@pytest.mark.parametrize("paper_type", ["position", "generic_empirical", "clinical_prediction"])
@pytest.mark.parametrize("bad", [None, "", "unknown", "ye"])
def test_every_requested_item_requires_an_explicit_valid_verdict(paper_type, bad):
    keys = [item.key for item in pc.spec_for(paper_type).items]
    checks = dict.fromkeys(keys, "yes")
    if bad is None:
        checks.pop(keys[-1])
    else:
        checks[keys[-1]] = bad
    client = RubricClient([checks])
    with pytest.raises(ValueError, match="incomplete or invalid rubric.*" + keys[-1]):
        evaluate(client, paper_type)
    assert client.calls == [qp.RubricLLMResponse]


@pytest.mark.parametrize("index", [0, 1, 2])
def test_any_incomplete_self_consistency_sample_cannot_certify_coverage(index):
    checks = dict.fromkeys([item.key for item in pc.spec_for("position").items], "yes")
    samples = [dict(checks) for _ in range(3)]
    samples[index].pop("scope_match")
    with pytest.raises(ValueError, match="incomplete or invalid rubric.*scope_match"):
        evaluate(RubricClient(samples), "position", runs=3)


def test_empty_sample_list_is_not_an_assessed_rubric():
    with pytest.raises(ValueError, match="no samples"):
        quality_eval._aggregate_rubric([], ["thesis"], BODY)


@pytest.mark.parametrize("verdict", ["yes", "na"])
def test_complete_explicit_verdicts_preserve_real_na_and_normalization(verdict):
    checks = dict.fromkeys([item.key for item in pc.spec_for("position").items], " NA ")
    checks["thesis"] = " YES " if verdict == "yes" else " NA "
    checks["irrelevant_extra_key"] = "ignored"
    result = evaluate(RubricClient([checks]), "position")
    assert result.coverage_applicable == (1 if verdict == "yes" else 0)
    assert result.grade == ("A" if verdict == "yes" else "")
    assert result.quality_band == ("highlight" if verdict == "yes" else "uncertain")


def test_sparse_position_rubric_reaches_existing_unassessed_projection(monkeypatch):
    from zotero_summarizer.services.library import _deep_review_layers as layers, _paper_read_pdf, paper_type
    from zotero_summarizer.services.library.review_fleet import propose
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config

    config = _default_goals_config()
    config.research_goals = []
    config.quality_review.lean_self_consistency_runs = 1
    config.quality_review.self_verification = False
    monkeypatch.setattr(_paper_read_pdf, "extract_pdf_content", lambda *_args, **_kwargs: {"full_text": BODY})
    monkeypatch.setattr(paper_type, "detect_safe", lambda *_args: {"type": "position", "uncertain": False})
    raw = PaperDigest(tldr="A reusable idea.", grade="A", basis="full_text", read_decision="read",
                      read_parts=["Section 2"], original_value="The comparison.", novelty=5,
                      significance=4, writing_friction="low").model_dump()
    client = RubricClient([{"thesis": "yes"}])
    ctx = layers.ExtraLayersCtx(item_key="SYNTH001", title="Synthetic", pdf_path="fixture.pdf", text=BODY,
                               digest_dump=raw, llm=client, config=config, prestige=None,
                               prestige_floor_value=None, lean_tier=True)
    quality, _, _, _, _ = layers.extra_layers(ctx)
    assert quality["grade"] == "" and quality["coverage_fraction"] is None
    assert quality["basis"] == "not_assessed" and quality["quality_band"] == "uncertain"
    assert quality["missing_critical"] == quality["red_flags"] == []
    goals = [{"retrieval_state": "hit", "relevant": True, "abstained": False}]
    projected = propose.project_review({"model_digest": raw, "quality": quality, "goal_summaries": goals})
    assert projected["digest"]["grade"] == "" and projected["digest"]["read_decision"] == "skim"
    assert projected["model_digest"]["grade"] == "A" and raw["read_decision"] == "read"
    assert client.calls == [qp.RubricLLMResponse]
