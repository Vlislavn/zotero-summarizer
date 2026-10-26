"""Owned cache/projection/runner controls; all semantic output is scripted input."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_digest_verification import PAPER
from tests.test_feed_assessment_producer import assessment, produce_review, profile, reply
from tests.test_review_cache import _isolate_cache
from test_verification_audit_export import KEY, audit_env, publish  # noqa: F401
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
    assert projected.project_assessments[0].model_dump() == record
    assert projected.assessment_evidence["/projects/0"] == {
        "quote": PAPER, "start": 0, "end": len(PAPER)}
    assert [idea.model_dump() for idea in projected.research_ideas] == [{
        "proposed_change": record["proposed_change"], "source_paper_id": "paper",
        "target_project": "agent harness", "minimal_validation_experiment": record["minimal_validation_experiment"]}]
    assert projected.production_impact == 2 and projected.reproducibility_tier == "blocked"
    assert "No verified code URL" in projected.missing_reproduction_inputs
    assert projected.impact_assessment.model_dump() == assessment()["impact"]
    assert projected.reproduction_assessment.model_dump() == assessment()["reproduction"]
    assert projected.assessment_origin == "ai_generated" and projected.scientific_verification == "unverified"
    assert projected.assessment_evidence["/impact"]["quote"] == PAPER
    assert projected.code_urls == projected.dataset_urls == projected.model_urls == []
    assert view["digest"] == entry["digest"] and view["review_identity"] == entry["review_identity"]
    assert (_review_cache._cache_path().read_bytes(), _review_cache._cache_path().stat().st_mtime_ns) == saved
    assert generator.pydantic_prompt.call_count == mapper.calls == 1
    records, missing, failed, unqualified, evidence = runner._records(
        [(candidate, triage)], lambda _key: view, context.profile)
    assert missing == failed == unqualified == [] and evidence["paper"]["qualified"] is True
    provenance = records[0]["provenance"]
    assert provenance["feed_generation"] == entry["feed_assessment"]
    assert provenance["scientific_verification"] == "unverified" and provenance["abstained"] is False
    assert (_review_cache._cache_path().read_bytes(), _review_cache._cache_path().stat().st_mtime_ns) == saved


@pytest.mark.parametrize("change", ["profile", "source_id", "digest", "source", "generation", "audit",
    "audit_missing", "audit_shape", "original_text", "original_extent", "producer", "schema", "payload", "authority"])
def test_optional_binding_drift_refuses_projection_without_mutating_base(tmp_path, monkeypatch, change):
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
    elif change in {"audit_missing", "audit_shape"}:
        entry["verification_audit"] = None if change == "audit_missing" else []
    elif change in {"original_text", "original_extent"}:
        payload = entry["feed_assessment"]
        if change == "original_text":
            payload["original_text_sha256"] = "a" * 64
        else:
            payload["original_characters"] += 1
        payload["payload_sha256"] = _feed_assessment.fingerprint({
            key: value for key, value in payload.items() if key != "payload_sha256"})
    elif change == "producer":
        entry["feed_assessment"]["contract"]["sources"]["services/library/_feed_assessment.py"] = "a" * 64
    elif change == "schema":
        entry["feed_assessment"]["contract"]["schema"] = "a" * 64
    elif change == "authority":
        entry["feed_assessment"]["scientific_verification"] = "human_approved"
    else:
        entry["feed_assessment"]["content"]["projects"][0]["target_component"] = "Edited component"
    before = deepcopy(entry)

    with pytest.raises(ValueError):
        card.build_card(candidate, triage, entry, selected)
    records, missing, failed, unqualified, evidence = runner._records(
        [(candidate, triage)], lambda _key: entry, selected)
    assert records == missing == unqualified == []
    assert len(failed) == 1 and failed[0]["source_id"] == candidate.source_id
    assert evidence[candidate.source_id]["availability"] == "available"
    assert entry == before and entry["digest"]["tldr"] == before["digest"]["tldr"]


@pytest.mark.parametrize("field", ["sources", "schema"])
def test_current_producer_contract_change_invalidates_intact_saved_optional_data(tmp_path, monkeypatch, field):
    entry, context, _generator, _mapper = produce_review(tmp_path, monkeypatch)
    old = deepcopy(entry)
    changed = deepcopy(_feed_assessment.contract())
    if field == "sources":
        changed[field]["services/library/_feed_assessment.py"] = "a" * 64
    else:
        changed[field] = "a" * 64
    monkeypatch.setattr(_feed_assessment, "contract", lambda: changed)
    with pytest.raises(ValueError, match="binding changed"):
        _feed_assessment.qualify(entry, context)
    assert entry == old


def test_profile_change_invalidates_only_feed_assessment_not_canonical_base(tmp_path, monkeypatch, current_config):
    entry, context, _generator, _mapper = produce_review(tmp_path, monkeypatch)
    _review_cache._write_one("paper", entry)
    before = _review_cache._cache_path().read_bytes()
    altered = context.profile.model_copy(deep=True)
    altered.themes.append("new explicit theme")
    view = _review_cache.get_current_review("paper")
    assert view is not None and view["digest"] == entry["digest"]
    with pytest.raises(ValueError, match="binding changed"):
        _feed_assessment.qualify(view, _feed_assessment.context_for("paper", altered))
    assert _feed_assessment.qualify(view, context)["status"] == "assessed"
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


def test_base_only_scores_code_and_implementation_cannot_invent_impact_or_reproduction(audit_env, monkeypatch):
    import test_verification_audit_export as fixtures
    from zotero_summarizer.models import PaperDigest

    class BaseOnlyClient(fixtures.ScriptedClient):
        def pydantic_prompt(self, *, pydantic_model, **kwargs):
            result = super().pydantic_prompt(pydantic_model=pydantic_model, **kwargs)
            if pydantic_model is PaperDigest:
                return result.model_copy(update={'implementation': ['Add the benchmark'], 'novelty': 4, 'significance': 4})
            return result

    monkeypatch.setattr(fixtures, 'ScriptedClient', BaseOnlyClient)
    entry, _, _ = publish(audit_env)
    entry['code_link'] = {'found': True, 'exists': True, 'relevance': 'matched',
                          'url': 'https://github.com/example/paper'}
    candidate, triage = inputs()
    candidate = candidate.model_copy(update={'source_id': KEY})
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
    monkeypatch.setattr(_feed_assessment, "current_review", lambda key: reviews.get(key))
    start = Mock()
    monkeypatch.setattr(deep_review, "start", start)
    monkeypatch.setattr(deep_review, "status", lambda key: {"status": "ready"})
    assert runner._ensure_reviews(settings, [candidate], 1, context.profile) == {}
    start.assert_not_called()
    changed = context.profile.model_copy(deep=True)
    changed.projects.append("new declared project")
    outcomes = runner._ensure_reviews(settings, [candidate], 1, changed)
    start.assert_called_once()
    assert outcomes == {"paper": "review qualification failed: ValueError: feed assessment binding changed"}
    assert _feed_assessment.qualify(entry, context)["status"] == "assessed"
    with pytest.raises(ValueError, match="binding changed"):
        _feed_assessment.qualify(entry, _feed_assessment.context_for("paper", changed))


def test_changed_producer_cannot_replay_a_previously_positive_cursor_record(environment, tmp_path, monkeypatch):
    from tests.test_research_feed import _seed
    from zotero_summarizer.services.research_feed.profile import load_profile

    settings, reviews = environment
    _seed(settings, "paper", "Agent harness benchmark")
    reviews["paper"], _context, _generator, _mapper = produce_review(
        tmp_path, monkeypatch, selected_profile=load_profile(settings.data_dir))
    first, result = run(settings, review_loader=reviews.get)
    retained = Path(result["json_path"]).read_bytes()
    assert first["cards"][0]["card"]["project_uses"] == ["use_as_benchmark"]
    changed = deepcopy(_feed_assessment.contract())
    changed["sources"]["services/library/_feed_assessment.py"] = "a" * 64
    monkeypatch.setattr(_feed_assessment, "contract", lambda: changed)
    second, _result = run(settings, review_loader=reviews.get)
    assert second["cards"] == [] and second["research_ideas_by_project"] == {}
    assert second["failed"] == [{"source_id": "paper", "error": "ValueError: feed assessment binding changed"}]
    assert second["metadata"]["full_text_evidence"]["paper"]["availability"] == "available"
    assert _generator.pydantic_prompt.call_count == _mapper.calls == 1
    assert Path(result["json_path"]).read_bytes() == retained


@pytest.mark.parametrize("invalid", [False, True])
def test_actual_worker_binds_accepted_attempt_without_native_delivery(tmp_path, monkeypatch, current_config, invalid):
    from tests.test_feed_assessment_producer import reply
    from tests.test_review_source_boundary import _Mapper
    from zotero_summarizer.integrations.llm import build_response_format
    from zotero_summarizer.models import PaperDigest
    from zotero_summarizer.services.library import paper_render
    from zotero_summarizer.services.zotero import zotero

    key = "feed:doi:owned-worker"
    source = tmp_path / "worker-owned.pdf"
    source.write_text(PAPER, encoding="utf-8")
    content = assessment(key)
    if invalid:
        content["projects"][0]["evidence_quote"] = "Not in the owned original."
    generator, mapper = Mock(), _Mapper("Generated notes, not source.")
    generator.pydantic_prompt.return_value = reply(content)
    no_native = Mock(side_effect=AssertionError("feed review must not resolve a native writer"))
    monkeypatch.setattr(zotero, "get_zotero_writer_or_raise", no_native)
    monkeypatch.setattr(paper_render, "_read_state", lambda _key: None)
    monkeypatch.setattr(deep_review._deep_review_layers, "extra_layers", lambda ctx: (None, None, None, None, None))
    context = _feed_assessment.context_for(key, profile())

    deep_review._review_worker({"item_key": key, "pdf_path": str(source)}, {
        "reader": SimpleNamespace(get_item_detail=lambda _key: {"title": "Owned paper", "pdf_path": str(source)}),
        "config": current_config, "extractor": SimpleNamespace(extract_text=lambda _path: PAPER),
        "llm": generator, "llm_map": mapper, "quality_enabled": True,
        "response_format": build_response_format(PaperDigest), "_feed_contexts": {key: context},
    }, "")

    assert deep_review.status(key)["status"] == "ready"
    entry = _review_cache.get_current_review(key)
    assert entry["digest"] and entry["verification_audit"]
    assert entry["zotero_note_written"] is False and entry["zotero_note_error"] is None
    assert entry["feed_assessment"]["review_identity"] == entry["review_identity"]
    assert entry["feed_assessment"]["digest_sha256"] == _feed_assessment.fingerprint(entry["digest"])
    if invalid:
        with pytest.raises(ValueError, match="generation unavailable: invalid"):
            _feed_assessment.qualify(entry, context)
    else:
        assert _feed_assessment.qualify(entry, context)["status"] == "assessed"
    assert generator.pydantic_prompt.call_count == mapper.calls == 1
    no_native.assert_not_called()


def test_start_refuses_retired_experiment_selector_before_setup_or_acquisition(monkeypatch):
    no_work = Mock(side_effect=AssertionError("unsupported selector must precede setup/acquisition"))
    from zotero_summarizer.services.library import _pdf_acquire

    monkeypatch.setattr(_pdf_acquire, "acquire_for_item", no_work)
    monkeypatch.setattr(_pdf_acquire, "acquire_pdf_for", no_work)
    monkeypatch.setattr(deep_review, "_build_ctx", no_work)
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        deep_review.start(item_keys=["paper"], acquire_missing=True,
            interpretation_schema="interpretation-digest-response-v1",
            feed_contexts={"paper": _feed_assessment.context_for("paper", profile())})
    no_work.assert_not_called()


def test_explicit_start_snapshots_context_and_refuses_source_collision_before_setup(monkeypatch):
    selected = profile()
    context = _feed_assessment.context_for("paper", selected)
    no_setup = Mock(side_effect=AssertionError("invalid request must refuse before setup"))
    monkeypatch.setattr(deep_review, "_build_ctx", no_setup)
    with pytest.raises(ValueError, match="exact explicit item keys"):
        deep_review.start(item_keys=["paper"], feed_contexts={"other": context})
    with pytest.raises(ValueError, match="exact explicit item keys"):
        deep_review.start(item_keys=["other"], feed_contexts={"other": context})
    no_setup.assert_not_called()
    selected.projects.append("later mutation")
    assert context.profile.projects == ["agent harness"]
    monkeypatch.setattr(deep_review, "_build_ctx", lambda **_kwargs: {})
    monkeypatch.setattr(deep_review.reading_queue, "get_cached_scoring", lambda _key: None)
    submit = Mock()
    monkeypatch.setattr(deep_review, "_submit", submit)
    deep_review.start(item_keys=["paper"], feed_contexts={"paper": context})
    retained = submit.call_args.args[1]["_feed_contexts"]
    context.profile.projects.append("mutation after handoff")
    assert retained["paper"].profile.projects == ["agent harness"]


@pytest.mark.parametrize('grade', ['', 'A', 'D'])
def test_feed_loader_keeps_raw_qualified_publication_before_card_projection(
    tmp_path, monkeypatch, current_config, grade,
):
    generator = Mock()
    generator.pydantic_prompt.return_value = {**reply(assessment()), 'grade': grade}
    entry, context, generator, mapper = produce_review(
        tmp_path, monkeypatch, config=current_config, generator=generator)
    _review_cache._write_one('paper', entry)
    before = _review_cache._cache_path().read_bytes()

    loaded = _feed_assessment.current_review('paper', config=current_config)

    assert loaded == entry
    assert _feed_assessment.qualify(loaded, context)['status'] == 'assessed'
    candidate, triage = inputs()
    projected = card.build_card(candidate, triage, loaded, context.profile)
    assert projected.project_assessment_status == 'assessed'
    assert projected.project_uses == ['use_as_benchmark']
    assert projected.worth_reading == 'unknown'
    view = _review_cache.get_current_review('paper')
    assert view['digest']['grade'] == ''
    assert view['model_digest']['grade'] == grade
    if grade:
        with pytest.raises(ValueError, match='review_audit_unqualified'):
            _feed_assessment.qualify(view, context)
    assert generator.pydantic_prompt.call_count == mapper.calls == 1
    assert _review_cache._cache_path().read_bytes() == before


@pytest.mark.parametrize('kind', ['intact', 'digest_only', 'missing_audit', 'null_audit', 'malformed_audit',
                                'digest_empty_list', 'digest_list', 'digest_false', 'digest_zero', 'digest_string'])
def test_base_only_default_feed_consumption_requires_retained_base_proof(audit_env, kind):
    from zotero_summarizer.services._common import read_config

    path = audit_env.settings.config_path
    path.write_text(json.dumps(audit_env.config.model_dump(mode='json')))
    audit_env.config = read_config(path, audit_env.settings.calibration_path)
    audit_env.context.state.app_state.config = audit_env.config
    path.write_text(json.dumps(audit_env.config.model_dump(mode='json')))
    entry, client, source = publish(audit_env, strategy=audit_env.config.quality_review.chunk_strategy)
    assert 'feed_assessment' not in entry
    if kind != 'intact':
        entry['digest']['tldr'] = 'Substituted unaudited base <payload> Ω.'
    if kind == 'missing_audit':
        del entry['verification_audit']
    elif kind == 'null_audit':
        entry['verification_audit'] = None
    elif kind == 'malformed_audit':
        entry['verification_audit'] = {'invalid': []}
    malformed_digests = {'digest_empty_list': [], 'digest_list': ['claim'], 'digest_false': False,
                         'digest_zero': 0, 'digest_string': 'broken'}
    if kind in malformed_digests:
        entry['digest'] = malformed_digests[kind]
    _review_cache._write_one(KEY, entry)
    before = _review_cache._cache_path().read_bytes(), source.read_bytes()
    candidate = ResearchCandidate(source_id=KEY, source='app_rss', title='Owned ordinary review')
    triage = ResearchFeedTriage(include=True, score=5, confidence=0.9, rationale='Scripted inclusion')
    selected = profile()

    records, missing, failed, unqualified, evidence = runner._records(
        [(candidate, triage)], _feed_assessment.cached_reader(audit_env.settings), selected)

    assert missing == unqualified == []
    assert evidence[KEY]['availability'] == 'available'
    if kind == 'intact':
        assert len(records) == 1 and failed == []
        assert records[0]['card']['project_assessment_status'] == 'not_assessed'
        assert records[0]['card']['problem'] == entry['digest']['tldr']
        assert evidence[KEY]['qualified'] is True
        assert _feed_assessment.qualify(entry, _feed_assessment.context_for(KEY, selected)) == {'status': 'not_assessed'}
    else:
        assert records == [] and len(failed) == 1
        assert evidence[KEY]['qualified'] is False
        reason = ('review_audit_invalid' if kind == 'malformed_audit' or kind in malformed_digests
                  else 'review_audit_unqualified')
        assert failed[0] == {'source_id': KEY, 'error': f'ValueError: {reason}'}
        with pytest.raises(ValueError):
            _feed_assessment.qualify(entry, _feed_assessment.context_for(KEY, selected))
        with pytest.raises(ValueError):
            card.build_card(candidate, triage, entry, selected, assessment={'status': 'not_assessed'})
    assert (client.generated, client.calls) == (1, 1)
    assert (_review_cache._cache_path().read_bytes(), source.read_bytes()) == before
