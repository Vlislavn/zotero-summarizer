"""Source-mode public-JSON projection checks, not free-text meaning evaluation.

The rendered triage note intentionally does not consume this legacy field.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import test_corpus_prefilter_authority as prefilter_tests
import test_method_source_admission as admission_tests
from zotero_summarizer import runtime
from zotero_summarizer.api.routes import review as review_routes
from zotero_summarizer.models import RefinedSummary, SummarizeRequest, SummarizeResponse
from zotero_summarizer.models import triage as triage_models
from zotero_summarizer.services.library import review, review_summary
from zotero_summarizer.services.triage import _json_output, prompts, summarization
from zotero_summarizer.services.triage.feeds import _gate, _tick_phases, _triage
from zotero_summarizer.services.zotero import _notes
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import feeds

config = admission_tests.config


@pytest.fixture(autouse=True)
def _source_bindings(record_property):
    root = Path(__file__).resolve().parents[1]
    paths = [Path(__file__).resolve()]
    for module in (admission_tests, prefilter_tests, runtime, triage_models, summarization, prompts,
                   _json_output, _triage, _gate, _tick_phases, review_routes,
                   review, review_summary, _notes, feeds):
        path = Path(module.__file__).resolve()
        assert path.is_relative_to(root), f"Unexpected source root: {path}"
        paths.append(path)
    for path in paths:
        record_property(str(path), hashlib.sha256(path.read_bytes()).hexdigest())


def _payload(advice):
    value = RefinedSummary(
        executive_summary="Synthetic overview; no scientific acceptance claim.",
        key_sections_to_read=["Table 2 — synthetic comparison"],
        relevance_to_research="Synthetic research connection.",
        controversial_points="Synthetic uncertainty.",
        industry_academy_impact="Synthetic impact.",
        unknown_unknowns="Synthetic unknown.",
        implementation_quickstart="Synthetic adaptation.",
        key_findings=["Synthetic finding A.", "Synthetic finding B."],
        methods="Synthetic method.",
        limitations="Synthetic limitation.",
        method_and_code={
            "what_it_does": "Synthetic operation.",
            "what_is_new": "Synthetic change.",
            "how_it_works": ["Synthetic step."],
            "evaluation": "Synthetic evaluation.",
            "artifacts": [],
            "how_i_could_use_it": "Synthetic use.",
        },
    ).model_dump(mode="json")
    value["should_deep_read"] = advice
    return value


def _assert_other_fields_preserved(result, supplied):
    expected = RefinedSummary.model_validate(supplied).model_dump(mode="json")
    actual = result.model_dump(mode="json")
    assert {key: actual[key] for key in expected if key != "should_deep_read"} == {
        key: value for key, value in expected.items() if key != "should_deep_read"
    }
    assert result.relevance_score == 3
    assert result.composite_relevance_score == 2.5
    assert result.reading_priority == "could_read"
    assert result.triage_rationale == "Fixture assessment"
    assert result.triage_confidence == 0.5


@pytest.mark.parametrize("advice", [True, "Yes", "No", False],
                         ids=["bool-true", "string-yes", "string-no", "bool-false"])
@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
def test_abstract_public_summary_excludes_exact_affirmative_legacy_token(
    tmp_path, monkeypatch, config, record_property, advice, repair,
):
    settings = Settings.load(project_root=tmp_path)
    config.prestige.enabled = False
    runtime.set_context(runtime.AppContext(
        settings=settings,
        state=runtime.RuntimeState(app_state=SimpleNamespace(config=config)),
    ))
    supplied = _payload(advice)
    typed = RefinedSummary.model_validate(supplied)
    assert typed.should_deep_read == ("Yes" if advice in (True, "Yes") else "No")
    llm = Mock()
    llm.prompt.side_effect = (["{malformed"] if repair else []) + [json.dumps(supplied)]
    admission_tests.wire_pipeline(monkeypatch, config, llm, "Unused PDF seam.")
    item = prefilter_tests._seed_feed(settings)

    candidate, error, fatal = _triage._triage_one(
        item, log_prefix="abstract-boundary", triage_llm=llm,
    )

    assert error is None and fatal is False
    assert candidate is not None
    result = candidate.summary
    assert isinstance(result, SummarizeResponse)
    _assert_other_fields_preserved(result, supplied)
    assert llm.prompt.call_count == (2 if repair else 1)
    llm.pydantic_prompt.assert_called_once()
    assert llm.pydantic_prompt.call_args.kwargs["pydantic_model"] is triage_models.TriageResult
    _tick_phases.record_tick_decisions(
        _tick_phases._TickResults([(item, candidate)], [], [], [], [], []),
        tick_id="abstract-boundary", review_mode=True,
    )
    with feeds.open_triage_conn(settings.triage_db_path) as conn:
        row = dict(conn.execute("SELECT * FROM processed_feed_items").fetchone())
    assert row["decision"] == feeds.DECISION_AWAITING_REVIEW
    assert row["error"] is None
    stored = json.loads(row["shap_contribs_json"])
    assert stored["summary_schema_version"] == 1
    assert stored["summary"] == result.model_dump(mode="json")
    assert review_summary.pick_stored_summary(row) == result
    app = FastAPI()
    app.include_router(review_routes.router)
    with TestClient(app) as client:
        response = client.get("/api/feeds/review")
    assert response.status_code == 200
    public = response.json()
    assert public["count"] == 1
    api_summary = public["items"][0]["summary"]
    assert api_summary == stored["summary"]
    # Counterevidence to any claim that this legacy field is rendered note advice.
    assert _notes.build_triage_note_html("Synthetic", result, include_provenance=False) == (
        _notes.build_triage_note_html(
            "Synthetic", result.model_copy(update={"should_deep_read": "No"}),
            include_provenance=False,
        )
    )
    record_property("abstract_boundary_io", json.dumps({
        "provider_payload": supplied,
        "normalized_advice": typed.should_deep_read,
        "dto": result.model_dump(mode="json"),
        "stored_summary": stored["summary"],
        "api_summary": api_summary,
        "refine_calls": llm.prompt.call_count,
        "triage_calls": llm.pydantic_prompt.call_count,
        "scope": "Exact legacy JSON token only; no canonical read or rendered-advice claim.",
    }))
    if advice in (False, "No"):
        assert api_summary["should_deep_read"] == "No"
    assert api_summary["should_deep_read"] == "No"


def test_valid_fulltext_control_keeps_typed_summary(monkeypatch, config, record_property):
    supplied = _payload("Digest is enough")
    llm = Mock()
    llm.prompt.return_value = json.dumps(supplied)
    admission_tests.wire_pipeline(monkeypatch, config, llm, "Synthetic full-text source.\n" * 200)

    result = summarization.run_pipeline(SummarizeRequest(
        title="Synthetic full-text control", pdf_path="/synthetic.pdf",
    ))

    _assert_other_fields_preserved(result, supplied)
    assert result.should_deep_read == "Digest is enough"
    llm.prompt.assert_called_once()
    llm.pydantic_prompt.assert_called_once()
    record_property("fulltext_control", result.model_dump_json())


@pytest.mark.parametrize("malformed", ["{malformed", '{"executive_summary": {}}'],
                         ids=["invalid-json", "invalid-type"])
def test_unrecovered_abstract_refinement_remains_error(
    monkeypatch, config, record_property, malformed,
):
    llm = Mock()
    llm.prompt.side_effect = [malformed, malformed]
    admission_tests.wire_pipeline(monkeypatch, config, llm, "Unused PDF seam.")

    with pytest.raises(ValueError) as failure:
        summarization.run_abstract_pipeline(SummarizeRequest(
            title="Synthetic malformed control", abstract="Synthetic abstract.",
        ))

    assert llm.prompt.call_count == 2
    llm.pydantic_prompt.assert_not_called()
    record_property("malformed_control", json.dumps({
        "error_type": type(failure.value).__name__, "error": str(failure.value),
        "refine_calls": 2, "triage_calls": 0,
    }))


@pytest.mark.parametrize("stage", ["refine", "triage"])
def test_abstract_provider_failure_is_not_success(monkeypatch, config, record_property, stage):
    llm = Mock()
    llm.prompt.return_value = json.dumps(_payload("No"))
    admission_tests.wire_pipeline(monkeypatch, config, llm, "Unused PDF seam.")
    problem = RuntimeError("Synthetic provider failure")
    if stage == "refine":
        llm.prompt.side_effect = problem
    else:
        llm.pydantic_prompt.side_effect = problem

    with pytest.raises(RuntimeError) as failure:
        summarization.run_abstract_pipeline(SummarizeRequest(
            title="Synthetic provider control", abstract="Synthetic abstract.",
        ))

    assert failure.value is problem
    assert llm.prompt.call_count == 1
    assert llm.pydantic_prompt.call_count == (0 if stage == "refine" else 1)
    record_property("provider_failure_control", json.dumps({
        "stage": stage, "error_type": type(problem).__name__,
        "refine_calls": llm.prompt.call_count,
        "triage_calls": llm.pydantic_prompt.call_count,
    }))


@pytest.fixture
def _retained_refinements(monkeypatch):
    retained = []
    refine = summarization._refine_with_retry

    def retain(*args, **kwargs):
        refined = refine(*args, **kwargs)
        retained.append(refined)
        return refined

    monkeypatch.setattr(summarization, "_refine_with_retry", retain)
    return retained


@pytest.mark.parametrize("mode,advice", [
    pytest.param("live", "Read the entire paper.", id="live-affirmative-prose"),
    pytest.param("live", "Digest is enough", id="live-negative-prose"),
    pytest.param("live", "legacy-mode-Δ", id="live-opaque-string"),
    pytest.param("live", "", id="live-empty-string"),
    pytest.param("dry", "Read the entire paper.", id="dry-affirmative-prose"),
    pytest.param("dry", "Digest is enough", id="dry-negative-prose"),
    pytest.param("dry", "legacy-mode-Δ", id="dry-opaque-string"),
    pytest.param("dry", "", id="dry-empty-string"),
    pytest.param("pdf", True, id="pdf-bool-true"),
    pytest.param("pdf", "Yes", id="pdf-string-yes"),
])
@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
def test_legacy_advice_projection_depends_only_on_source_mode(
    monkeypatch, config, record_property, _retained_refinements, mode, advice, repair,
):
    supplied = _payload(advice)
    typed = RefinedSummary.model_validate(supplied)
    source = "Synthetic source without URLs."
    req = SummarizeRequest(
        title="Synthetic source-mode control", abstract=source,
        pdf_path="/synthetic.pdf" if mode == "pdf" else "",
    )
    llm = Mock()
    llm.prompt.side_effect = (["{malformed"] if repair else []) + [json.dumps(supplied)]
    admission_tests.wire_pipeline(monkeypatch, config, llm, source)

    if mode == "pdf":
        result = summarization.run_pipeline(req)
    elif mode == "dry":
        result = summarization.run_abstract_dry(config, req, llm)
    else:
        result = summarization.run_abstract_pipeline(req)

    assert isinstance(result, SummarizeResponse)
    _assert_other_fields_preserved(result, supplied)
    assert _retained_refinements == [typed]
    assert llm.prompt.call_count == (2 if repair else 1)
    llm.pydantic_prompt.assert_called_once_with(
        prompt=summarization._build_triage_prompt(
            config, req, typed, summarization.corpus.empty_corpus_match_result(),
        ),
        pydantic_model=triage_models.TriageResult,
    )
    expected_advice = typed.should_deep_read if mode == "pdf" else "No"
    record_property("source_mode_projection_io", json.dumps({
        "mode": mode,
        "phase": "repair" if repair else "first",
        "provider_payload": supplied,
        "original_refined": _retained_refinements[0].model_dump(mode="json"),
        "dto": result.model_dump(mode="json"),
        "expected_public_advice": expected_advice,
        "triage_prompt_preserved": True,
        "refine_calls": llm.prompt.call_count,
        "triage_calls": llm.pydantic_prompt.call_count,
        "scope": "Machine source-mode projection only; no semantic or live-model acceptance.",
    }))
    assert result.should_deep_read == expected_advice
