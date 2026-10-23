"""Source-qualified proposals through existing review, cache and weekly boundaries.

Scripted model replies test plumbing, never scientific correctness/usefulness.
"""
from copy import deepcopy
from functools import partial
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_digest_verification import PAPER, _Verifier, _digest
from tests.test_research_feed import _review, _seed, _settings
from tests.test_research_feed_boundaries import START, END
from zotero_summarizer import cli
from zotero_summarizer.models import ResearchCandidate, ResearchProfile, SummarizeResponse
from zotero_summarizer.models.research_feed import FeedDigestResponse, FeedProjectAssessment
from zotero_summarizer.services.library import _feed_assessment as feed, deep_review, quality_review
from zotero_summarizer.services.library import _map_reduce
from zotero_summarizer.services.research_feed import runner
from zotero_summarizer.services.research_feed.cursor import CandidateCursor
from zotero_summarizer.services.research_feed.ranking import rank_candidates
from zotero_summarizer.services.research_feed.profile import load_profile
from zotero_summarizer.services.setup.bootstrap import _default_goals_config


def _assessment(key, projects=("agent harness",), *, quote=PAPER):
    return {"projects": [{
        "source_id": key, "target_project": project, "assessment": "applicable",
        "use_kind": "use_as_benchmark", "rationale": "Test held-out task generalization",
        "mechanism": "Reuse the held-out task split", "target_component": "harness evaluation runner",
        "proposed_change": "Add held-out tasks to the evaluation suite",
        "minimal_validation_experiment": "Compare baseline and candidate on the same held-out tasks",
        "evidence_quote": quote,
    } for project in projects], "reproduction": None, "impact": None}


class Generator:
    def __init__(self, key, content=None):
        self.key, self.content, self.calls, self.models = key, content, [], []

    def pydantic_prompt(self, *, prompt, pydantic_model, **kwargs):
        self.calls.append(prompt)
        self.models.append(pydantic_model)
        payload = _digest().model_dump()
        payload["feed_assessment"] = self.content if self.content is not None else _assessment(self.key)
        return pydantic_model.model_validate(payload)


def _generated_entry(key, profile, *, content=None, strategy="rank"):
    config = _default_goals_config()
    config.quality_review.chunk_strategy = strategy
    generator, verifier, out = Generator(key, content), _Verifier(), {}
    verifier.prompt = lambda _prompt: PAPER
    digest = _map_reduce.digest_for_strategy(
        "Agent evaluation", PAPER, config, map_llm=verifier, reduce_llm=generator,
        budget=_map_reduce.ChunkBudget(8000, 8000, 1),
        feed_context=feed.context_for(key, profile), feed_out=out,
    )
    entry = _review(key)
    from zotero_summarizer.services.library.review_fleet.propose import apply_reading_policy
    entry["digest"], entry["model_read_decision"], entry["reading_policy_flags"] = apply_reading_policy(
        digest.model_dump(), entry["quality"], entry["goal_summaries"])
    return feed.bind(entry, out), generator, verifier


@pytest.mark.parametrize("strategy", ["rank", "prefix", "map_reduce"])
def test_same_review_call_generates_original_bound_project_proposal(strategy):
    profile = ResearchProfile(themes=["evaluation"], projects=["agent harness"])
    entry, generator, verifier = _generated_entry("paper", profile, strategy=strategy)
    assessment = feed.qualify(entry, feed.context_for("paper", profile))

    assert len(generator.calls) == 1 and generator.models == [FeedDigestResponse]
    assert verifier.calls == 1
    assert assessment["status"] == "assessed"
    assert assessment["evidence"]["/projects/0"] == {"quote": PAPER, "start": 0, "end": len(PAPER)}
    assert assessment["scientific_verification"] == "unverified"
    assert "feed_assessment" not in entry["digest"]


@pytest.mark.parametrize("content", [
    {"projects": []}, _assessment("wrong"),
    _assessment("paper", quote="Generated notes assert an unavailable result."),
])
def test_failed_optional_assessment_keeps_verified_base_without_extra_model_calls(content):
    profile = ResearchProfile(themes=[], projects=["agent harness"])
    entry, generator, verifier = _generated_entry("paper", profile, content=content)

    assert entry["digest"]["tldr"] == _digest().tldr
    assert len(generator.calls) == verifier.calls == 1
    with pytest.raises(ValueError):
        feed.qualify(entry, feed.context_for("paper", profile))


def test_unknown_partial_and_explicit_no_fit_remain_distinct():
    profile = ResearchProfile(themes=[], projects=["agent harness", "activation oracle"])
    content = _assessment("paper", projects=("agent harness",))
    content["projects"][0].update(assessment="not_applicable", use_kind="not_applicable", mechanism="",
        target_component="", proposed_change="", minimal_validation_experiment="")
    entry, _, _ = _generated_entry("paper", profile, content=content)
    result = feed.qualify(entry, feed.context_for("paper", profile))
    assert result["status"] == "partial"
    assert result["content"]["projects"][0]["assessment"] == "not_applicable"
    unknown = {**content["projects"][0], "assessment": "unknown", "use_kind": None, "evidence_quote": ""}
    assert FeedProjectAssessment.model_validate(unknown).assessment == "unknown"


@pytest.mark.parametrize("change", ["profile", "digest", "source", "evidence", "status"])
def test_changed_binding_or_semantically_invalid_envelope_cannot_reuse_positives(change):
    profile = ResearchProfile(themes=[], projects=["agent harness"])
    entry, _, _ = _generated_entry("paper", profile)
    if change == "profile":
        profile.projects.append("activation oracle")
    elif change == "digest":
        entry["digest"]["tldr"] = "changed"
    elif change == "source":
        entry["review_identity"] = {**entry["review_identity"], "source_sha256": "c" * 64}
    else:
        envelope = entry["feed_assessment"]
        if change == "evidence":
            envelope["evidence"]["/projects/0"]["end"] += 1
        else:
            envelope["status"] = "partial"
        envelope["payload_sha256"] = feed.fingerprint({k: v for k, v in envelope.items() if k != "payload_sha256"})
    with pytest.raises(ValueError):
        feed.qualify(entry, feed.context_for("paper", profile))


def test_explicit_effort_estimate_is_unverified_not_inferred_from_code():
    profile = ResearchProfile(themes=[], projects=["agent harness"])
    content = _assessment("paper")
    content["reproduction"] = {"tier": "one_week", "rationale": "Author describes the setup",
                               "missing_inputs": [], "evidence_quote": PAPER}
    entry, _, _ = _generated_entry("paper", profile, content=content)
    candidate = ResearchCandidate(source_id="paper", source="app_rss", title="Agent evaluation")
    triage = runner.triage_candidate(candidate, {"decision": "selected"}, profile)
    records, missing, failed = runner._records([(candidate, triage)], lambda _key: entry, profile)
    card = records[0]["card"]
    assert card["reproducibility_tier"] == "one_week"
    assert card["reproducibility_rationale"].startswith("AI estimate, unverified:")
    assert card["research_ideas"][0]["target_project"] == "agent harness"
    assert card["impact_assessment"] is None
    assert not missing and not failed


def test_warm_projection_never_suppresses_current_generation_error():
    profile = ResearchProfile(themes=[], projects=["agent harness"])
    candidate = ResearchCandidate(source_id="paper", source="app_rss", title="Agent evaluation")
    triage = runner.triage_candidate(candidate, {"decision": "selected"}, profile)
    for _ in range(2):
        records, _, failed = runner._records([(candidate, triage)], _review, profile, {"paper": "provider failed"})
        assert len(records) == 1
        assert records[0]["card"]["project_uses"] == []
        assert records[0]["card"]["research_ideas"] == []
        assert failed == [{"source_id": "paper", "error": "provider failed"}]


@pytest.mark.parametrize("bad", [None, "abstract", "unbound"])
def test_unqualified_source_is_not_project_science(bad):
    profile = ResearchProfile(themes=[], projects=[])
    candidate = ResearchCandidate(source_id="paper", source="app_rss", title="Agent evaluation")
    triage = runner.triage_candidate(candidate, {"decision": "selected"}, profile)
    review = _review("paper")
    if bad is None:
        review.update(needs_pdf=True, digest=None, acquire_outcome="needs_login")
    elif bad == "abstract":
        review["digest"]["basis"] = "abstract_only"
    else:
        review["review_identity"] = {}
    records, _, failed = runner._records([(candidate, triage)], lambda _key: review, profile)
    assert not records and len(failed) == 1


def test_cli_generation_and_rerun_publish_v1_without_replaying_old_cards(tmp_path, monkeypatch, capsys):
    from zotero_summarizer.services import lifecycle, research_feed
    settings = _settings(tmp_path)
    _seed(settings, "paper", "Agent evaluation")
    stored, generators, starts = {}, [], []

    def start(**kwargs):
        starts.append(kwargs)
        for key in kwargs["item_keys"]:
            context = kwargs["feed_contexts"][key]
            entry, generator, _ = _generated_entry(key, context.profile)
            stored[key] = entry
            generators.append(generator)
        return {"accepted": True}

    monkeypatch.setattr(lifecycle, "startup", Mock())
    monkeypatch.setattr(deep_review, "start", start)
    monkeypatch.setattr(deep_review, "status", lambda _key: {"status": "ready"})
    monkeypatch.setattr(feed, "current_review", lambda key: deepcopy(stored.get(key)))
    monkeypatch.setattr(research_feed, "run_weekly", partial(runner.run_weekly, review_loader=lambda key: deepcopy(stored.get(key))))
    args = ["research-feed", "run", "--from", START.isoformat(), "--to", END.isoformat(),
            "--project-root", str(tmp_path)]
    assert cli.main(args) == 0
    first = json.loads(capsys.readouterr().out)
    original = Path(first["json_path"]).read_bytes()
    assert cli.main(args) == 0
    second = json.loads(capsys.readouterr().out)
    payload = json.loads(Path(second["json_path"]).read_text())
    assert len(starts) == len(generators) == 1
    assert len(generators[0].calls) == 1
    assert payload["metadata"]["schema_version"] == 1 and payload["cards"][0]["card"]["research_ideas"]
    assert payload["research_ideas_by_project"]["agent harness"]
    assert first["json_path"] != second["json_path"]
    assert Path(first["json_path"]).read_bytes() == original
    assert not (settings.data_dir / "research_feed" / "state.json").read_text().count("record_sha256")


def test_abstract_success_cache_is_source_profile_and_config_bound(tmp_path, monkeypatch):
    from zotero_summarizer.services.triage import summarization
    from zotero_summarizer.services.research_feed import cursor
    settings = _settings(tmp_path)
    app = SimpleNamespace(app_state=SimpleNamespace(config=_default_goals_config()), resolve_stage_client=lambda _stage: object())
    model = Mock(return_value=SummarizeResponse(
        executive_summary="A retained abstract result.", relevance_score=4,
        triage_rationale="Synthetic declared relevance.",
        composite_relevance_score=4, reading_priority="should_read",
    ))
    monkeypatch.setattr(cursor, "state", lambda: app)
    monkeypatch.setattr(summarization, "run_abstract_dry", model)
    candidate = ResearchCandidate(source_id="new", source="app_rss", title="Agent evaluation", abstract=PAPER)
    profile = load_profile(settings.data_dir)
    first = CandidateCursor(settings).row(candidate, profile)
    assert CandidateCursor(settings).row(candidate, profile) == first
    assert model.call_count == 1
    profile.projects.append("new project")
    CandidateCursor(settings).row(candidate, profile)
    assert model.call_count == 2
    CandidateCursor(settings).row(candidate.model_copy(update={"abstract": PAPER + " Updated."}), profile)
    assert model.call_count == 3


def test_source_access_error_propagates_instead_of_becoming_unsupported_science(monkeypatch):
    from zotero_summarizer.services.library import _review_cache, _review_identity
    monkeypatch.setattr(_review_cache, "get_cached_review", _review)
    monkeypatch.setattr(_review_identity, "current_review_identity", Mock(side_effect=PermissionError("denied")))
    with pytest.raises(PermissionError):
        feed.current_review("paper")


def test_same_size_same_timestamp_pdf_replacement_invalidates_source(tmp_path):
    import os
    from zotero_summarizer.services.library._review_identity import _source_sha
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"before")
    stamp, before = path.stat(), _source_sha(str(path))
    path.write_bytes(b"after!")
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert _source_sha(str(path)) != before


def test_current_cohort_blend_uses_continuous_not_rounded_relevance():
    profile = ResearchProfile(themes=[], projects=[])
    candidates = [ResearchCandidate(source_id=key, source="app_rss", title=key) for key in ("a", "b")]
    rows = {key: {"decision": "selected", "composite_score": value} for key, value in (("a", 4.1), ("b", 4.4))}
    assessed = [(c, runner.triage_candidate(c, rows[c.source_id], profile)) for c in candidates]
    ranked, metadata, failed = rank_candidates(assessed, rows)
    assert [c.source_id for c, _ in ranked] == ["b", "a"]
    assert metadata["inputs"]["a"]["relevance"] == 4.1 and not failed
