"""Legacy omitted-type mode is assumed, not a verified type or an error fallback."""
import json
from types import SimpleNamespace

import pytest

from zotero_summarizer.models import PaperDigest
from zotero_summarizer.services.library import _paper_type_checklists as pc, _quality_prompts as qp, quality_eval

BODY = "This source describes the complete protocol and supports the stated criterion with detailed evidence."
CHECKS = {item.key: "yes" for item in pc.spec_for("generic_empirical").items}


class QualityClient:
    def __init__(self):
        self.calls = []

    def pydantic_prompt(self, *, pydantic_model, **_kwargs):
        self.calls.append(pydantic_model)
        if pydantic_model is qp.RubricLLMResponse:
            return qp.RubricLLMResponse(checks=CHECKS, evidence={key: BODY for key in CHECKS})
        if pydantic_model is qp.OverstatementLLMResponse:
            return qp.OverstatementLLMResponse(overstatements=[])
        raise AssertionError("No classifier, extra generator or quality call expected")


def test_omitted_type_keeps_assumed_generic_contract_and_existing_call_count():
    client = QualityClient()
    result = quality_eval.evaluate_quality(title="Study", full_text=BODY, sections=[], digest={},
                                          llm=client, max_chars=2000, self_consistency_runs=1)
    assert result.paper_type == "generic_empirical" and result.rubric == CHECKS
    assert result.quality_band == "highlight" and result.grade == "A"
    assert client.calls == [qp.RubricLLMResponse]
    assert result.evidence == {key: BODY for key in CHECKS}


def test_search_keeps_its_existing_assumed_mode_and_quality_budget():
    from zotero_summarizer.services.search.review import light_review

    client = QualityClient()
    candidate = SimpleNamespace(title="Study", quality={}, coverage={})
    light_review(candidate, full_text=BODY, sections=[], llm=client, max_chars=2000)
    assert candidate.quality["paper_type"] == "generic_empirical"
    assert candidate.quality["quality_band"] == "highlight"
    assert candidate.coverage["coverage_fraction"] == 1.0
    assert client.calls == [qp.RubricLLMResponse]


@pytest.mark.parametrize("detected", [{"type": "generic_empirical", "uncertain": True},
                                     {"type": "generic_review", "uncertain": True},
                                     {"type": "", "uncertain": True, "source": "error"}])
def test_full_review_never_treats_uncertain_detection_as_assumed_empirical(monkeypatch, detected):
    from zotero_summarizer.services.library import _deep_review_layers, _paper_read_pdf, paper_type
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config

    config = _default_goals_config()
    config.research_goals = []
    client = QualityClient()
    monkeypatch.setattr(paper_type, "detect_safe", lambda *_args: detected)
    monkeypatch.setattr(_paper_read_pdf, "extract_pdf_content", lambda *_args, **_kwargs: {"full_text": BODY})
    ctx = _deep_review_layers.ExtraLayersCtx(item_key="PAPER001", title="Study", pdf_path="/fixture.pdf",
                                            text=BODY, digest_dump={}, llm=client, config=config,
                                            prestige=None, prestige_floor_value=None, lean_tier=True)
    quality, _, stored_type, _, _ = _deep_review_layers.extra_layers(ctx)
    assert stored_type == detected
    assert quality["basis"] == "not_assessed" and quality["paper_type"] == ""
    assert quality["grade"] == "" and quality["coverage_fraction"] is None
    assert client.calls == []


def test_verify_cli_keeps_assumed_rubric_and_existing_quality_call_budget(tmp_path, monkeypatch, capsys):
    from zotero_summarizer.cli import _app, _review_source, build_parser
    from zotero_summarizer.services import _common
    from zotero_summarizer.services.library import _map_reduce
    from zotero_summarizer.services.llm import factory
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config

    path = tmp_path / "paper_read.json"
    path.write_text(json.dumps({"qa_text": BODY}))
    original = path.read_bytes()
    config = _default_goals_config()
    config.quality_review.self_consistency_runs = 1
    config.quality_review.lean_self_consistency_runs = 1
    client = QualityClient()
    generators = []
    monkeypatch.setattr(_app.Settings, "load", lambda **_kwargs: SimpleNamespace(data_dir=tmp_path, config_path=tmp_path / "config"))
    monkeypatch.setattr(_review_source, "read_source", lambda *_args: (path, {}, BODY, "Study", "fixture"))
    monkeypatch.setattr(_common, "read_config", lambda _path: config)
    monkeypatch.setattr(factory, "build_client_for_provider", lambda *_args, **_kwargs: client)
    monkeypatch.setattr(_map_reduce, "digest_for_strategy", lambda *_args, **_kwargs:
                        generators.append(True) or PaperDigest(tldr="A concept.", read_decision="skip"))
    args = build_parser().parse_args(["verify-deep-review", "--item-key", "PAPER001"])
    assert _app._verify_deep_review(args) == 0
    assert len(generators) == 1
    assert client.calls == [qp.RubricLLMResponse, qp.OverstatementLLMResponse]
    assert '"quality_grade": "A"' in capsys.readouterr().out
    assert path.read_bytes() == original
