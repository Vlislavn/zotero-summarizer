"""Owned cache/projection/runner controls; all semantic output is scripted input."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_digest_verification import PAPER
from tests.test_feed_assessment_producer import assessment, produce_review, profile
from tests.test_review_cache import _isolate_cache
from tests.test_research_feed_cursor import environment, run
from zotero_summarizer.models import ResearchCandidate, ResearchFeedTriage
from zotero_summarizer.services.library import _feed_assessment, _review_cache, _review_identity, deep_review
from zotero_summarizer.services.research_feed import card, runner
from zotero_summarizer.services.setup.bootstrap import _default_goals_config


@pytest.fixture
def current_config(monkeypatch):
    config = _default_goals_config()
    monkeypatch.setattr(_review_identity, "state", lambda: SimpleNamespace(app_state=SimpleNamespace(config=config)))
    monkeypatch.setattr(_review_identity, "settings", lambda: SimpleNamespace(summary_timeout_seconds=120))
    return config


def inputs():
    return (ResearchCandidate(source_id="paper", source="app_rss", title="Topical lure"),
            ResearchFeedTriage(include=True, score=5, confidence=0.9, rationale="Scripted inclusion"))


def test_produced_positive_projects_and_real_ideas_survive_normal_current_cache(tmp_path, monkeypatch, current_config):
    entry, context, generator, mapper = produce_review(tmp_path, monkeypatch)
    _review_cache._write_one("paper", entry)
    saved = _review_cache._cache_path().read_bytes(), _review_cache._cache_path().stat().st_mtime_ns
    view = _review_cache.get_current_review("paper")
    candidate, triage = inputs()

    projected = card.build_card(candidate, triage, view, context.profile)

    assert projected.project_assessment_status == "assessed"
    assert projected.project_uses == ["use_as_benchmark"]
    record = assessment()["projects"][0]
    assert projected.project_assessments[0].model_dump(exclude={"original_evidence"}) == record
    assert projected.project_assessments[0].original_evidence.model_dump() == {
        "quote": PAPER, "start": 0, "end": len(PAPER), "pdf_page": None}
    assert [idea.model_dump() for idea in projected.research_ideas] == [{
        "proposed_change": record["proposed_change"], "source_paper_id": "paper",
        "target_project": "agent harness", "minimal_validation_experiment": record["minimal_validation_experiment"]}]
    assert projected.production_impact == 2 and projected.reproducibility_tier == "one_week"
    assert projected.impact_assessment.model_dump() == assessment()["impact"]
    assert projected.reproduction_assessment.model_dump() == assessment()["reproduction"]
    assert projected.assessment_origin == "ai_generated" and projected.scientific_verification == "unverified"
    assert projected.assessment_evidence["/impact"].quote == PAPER
    assert projected.code_urls == projected.dataset_urls == projected.model_urls == []
    assert view["digest"] == entry["digest"] and view["review_identity"] == entry["review_identity"]
    assert (_review_cache._cache_path().read_bytes(), _review_cache._cache_path().stat().st_mtime_ns) == saved
    assert generator.pydantic_prompt.call_count == mapper.calls == 1
    provenance = runner._provenance(candidate, view, context.profile)
    assert provenance["feed_generation"] == entry["feed_assessment"]["producer"]
    assert provenance["scientific_verification"] == "unverified" and provenance["abstained"] is False


@pytest.mark.parametrize("change", ["profile", "source_id", "digest", "source", "generation", "audit", "producer", "schema", "payload", "authority"])
def test_optional_binding_drift_withholds_all_positive_fields_but_keeps_base(tmp_path, monkeypatch, change):
    entry, context, _generator, _mapper = produce_review(tmp_path, monkeypatch)
    selected = context.profile.model_copy(deep=True)
    candidate, triage = inputs()
    if change == "profile":
        selected.projects.append("new project")
    elif change == "source_id":
        candidate.source_id = "foreign-paper"
    elif change == "digest":
        entry["digest"]["methods"] = "Changed normalized base"
    elif change in {"source", "generation"}:
        entry["review_identity"][change + "_sha256"] = "a" * 64
    elif change == "audit":
        entry["verification_audit"]["source"]["original_text_sha256"] = "a" * 64
    elif change in {"producer", "schema"}:
        entry["feed_assessment"]["producer"][change + "_sha256"] = "a" * 64
    elif change == "authority":
        entry["feed_assessment"]["scientific_verification"] = "human_approved"
    else:
        entry["feed_assessment"]["content"]["projects"][0]["target_component"] = "Edited component"
    before = deepcopy(entry)

    projected = card.build_card(candidate, triage, entry, selected)

    assert projected.project_uses == [] and projected.research_ideas == []
    assert projected.project_assessments == []
    assert projected.reproducibility_tier == "unknown" and projected.production_impact == 0
    assert projected.assessment_origin is None and projected.scientific_verification is None
    assert entry == before and projected.problem == entry["digest"]["tldr"]


@pytest.mark.parametrize("field", ["producer_sha256", "schema_sha256"])
def test_current_producer_contract_change_invalidates_intact_saved_optional_data(tmp_path, monkeypatch, field):
    entry, context, _generator, _mapper = produce_review(tmp_path, monkeypatch)
    old = deepcopy(entry)
    current = _feed_assessment._contract_identity()
    monkeypatch.setattr(_feed_assessment, "_contract_identity", lambda: {**current, field: "a" * 64})
    qualification = _feed_assessment.qualify(entry, context)
    assert qualification == {"status": "not_assessed", "reason": "feed_contract_changed", "qualified": False}
    assert entry == old


def test_profile_change_invalidates_only_feed_assessment_not_canonical_base(tmp_path, monkeypatch, current_config):
    entry, context, _generator, _mapper = produce_review(tmp_path, monkeypatch)
    _review_cache._write_one("paper", entry)
    before = _review_cache._cache_path().read_bytes()
    altered = context.profile.model_copy(deep=True)
    altered.themes.append("new explicit theme")
    view = _review_cache.get_current_review("paper")
    assert view is not None and view["digest"] == entry["digest"]
    assert _feed_assessment.qualify(view, _feed_assessment.context_for("paper", altered)) == {
        "status": "not_assessed", "reason": "feed_context_changed", "qualified": False}
    assert _feed_assessment.qualify(view, context)["qualified"] is True
    assert _review_cache._cache_path().read_bytes() == before


def test_base_source_change_rejects_current_review_even_with_valid_optional_binding(tmp_path, monkeypatch, current_config):
    entry, context, _generator, _mapper = produce_review(tmp_path, monkeypatch)
    _review_cache._write_one("paper", entry)
    assert _review_cache.get_current_review("paper") is not None
    source = Path(entry["review_identity"]["source_path"])
    source.write_bytes(b"R" * source.stat().st_size)
    assert _review_cache.get_current_review("paper") is None
    assert _review_cache._read_all(readonly=True)["paper"] == entry


@pytest.mark.parametrize("case", ["no_fit", "unknown", "partial"])
def test_nonpositive_and_partial_records_do_not_manufacture_ideas(tmp_path, monkeypatch, case):
    content = assessment()
    record = content["projects"][0]
    record.update(assessment="not_applicable" if case == "no_fit" else "unknown",
                  use_kind="not_applicable" if case == "no_fit" else None,
                  mechanism="", target_component="", proposed_change="", minimal_validation_experiment="")
    if case == "partial":
        content["projects"] = []
    entry, context, _generator, _mapper = produce_review(tmp_path, monkeypatch, content=content)
    candidate, triage = inputs()
    projected = card.build_card(candidate, triage, entry, context.profile)
    assert projected.project_assessment_status == ("assessed" if case == "no_fit" else "partial")
    assert projected.project_uses == (["not_applicable"] if case == "no_fit" else [])
    assert projected.research_ideas == []


def test_base_only_scores_code_and_implementation_cannot_invent_impact_or_reproduction():
    from tests.test_research_feed import _review

    candidate, triage = inputs()
    entry = _review("paper")
    projected = card.build_card(candidate, triage, entry, profile())
    assert projected.production_impact == projected.research_impact == projected.personal_novelty == 0
    assert projected.impact_assessment is None and projected.reproduction_assessment is None
    assert projected.reproducibility_tier == "unknown" and projected.research_ideas == []


def test_generation_reuses_matching_feed_profile_and_regenerates_after_profile_change(tmp_path, monkeypatch):
    from tests.test_research_feed import _settings

    settings = _settings(tmp_path)
    entry, context, _generator, _mapper = produce_review(tmp_path, monkeypatch)
    reviews = {"paper": entry}
    candidate, _triage = inputs()
    monkeypatch.setattr(deep_review, "get_current_review", lambda key: reviews.get(key))
    start = Mock()
    monkeypatch.setattr(deep_review, "start", start)
    monkeypatch.setattr(deep_review, "status", lambda key: {"status": "ready"})
    assert runner._ensure_reviews(settings, [candidate], 1, context.profile) == {}
    start.assert_not_called()
    changed = context.profile.model_copy(deep=True)
    changed.projects.append("new declared project")
    outcomes = runner._ensure_reviews(settings, [candidate], 1, changed)
    start.assert_called_once()
    assert outcomes == {"paper": "deep review did not produce a current feed assessment"}
    assert _feed_assessment.qualify(entry, context)["qualified"] is True
    assert not _feed_assessment.qualify(entry, _feed_assessment.context_for("paper", changed))["qualified"]


def test_changed_producer_cannot_replay_a_previously_positive_cursor_record(environment, tmp_path, monkeypatch):
    from tests.test_research_feed import _seed
    from zotero_summarizer.services.research_feed.profile import load_profile

    settings, reviews = environment
    _seed(settings, "paper", "Agent harness benchmark")
    reviews["paper"], _context, _generator, _mapper = produce_review(
        tmp_path, monkeypatch, selected_profile=load_profile(settings.data_dir))
    first, result = run(settings)
    retained = Path(result["json_path"]).read_bytes()
    assert first["cards"][0]["card"]["project_uses"] == ["use_as_benchmark"]
    old = _feed_assessment._contract_identity()
    monkeypatch.setattr(_feed_assessment, "_contract_identity", lambda: {**old, "producer_sha256": "a" * 64})
    second, _result = run(settings)
    assert second["metadata"]["candidate_cursor"]["cards_reused"] == 0
    assert second["cards"][0]["card"]["project_uses"] == []
    assert second["cards"][0]["card"]["research_ideas"] == []
    assert Path(result["json_path"]).read_bytes() == retained


def test_worker_c_feed_conflict_refuses_before_acquisition(monkeypatch):
    no_work = Mock(side_effect=AssertionError("conflict must precede acquisition"))
    from zotero_summarizer.services.library import _pdf_acquire

    monkeypatch.setattr(_pdf_acquire, "acquire_for_item", no_work)
    deep_review._review_worker({"item_key": "paper"}, {
        "_acquire_missing": True, "interpretation_schema": "interpretation-digest-response-v1",
        "_feed_contexts": {"paper": _feed_assessment.context_for("paper", profile())},
    }, "")
    assert deep_review.status("paper")["status"] == "error"
    assert deep_review.status("paper")["error"] == "All 1 item(s) failed: ValueError: feed_context conflicts with interpretation selection"
    no_work.assert_not_called()


def test_explicit_start_snapshots_context_and_refuses_source_collision_before_setup(monkeypatch):
    selected = profile()
    context = _feed_assessment.context_for("paper", selected)
    no_setup = Mock(side_effect=AssertionError("invalid request must refuse before setup"))
    monkeypatch.setattr(deep_review, "_build_ctx", no_setup)
    with pytest.raises(ValueError, match="exact explicit item keys"):
        deep_review.start(item_keys=["paper"], feed_contexts={"other": context})
    with pytest.raises(ValueError, match="source mismatch"):
        deep_review.start(item_keys=["other"], feed_contexts={"other": context})
    no_setup.assert_not_called()
    selected.projects.append("later mutation")
    assert context.profile.projects == ["agent harness"]
    retained = _feed_assessment.validate_contexts({"paper": context}, ["paper"])
    context.profile.projects.append("mutation after handoff")
    assert retained["paper"].profile.projects == ["agent harness"]
