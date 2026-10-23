"""Persisted RSS stage replay through real synthetic SQLite/report publication."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from unittest.mock import Mock

import pytest

from tests.test_research_feed import _review, _seed, _settings
from zotero_summarizer.services.library import _review_cache, deep_review
from zotero_summarizer.services.research_feed import card, cursor, runner
from zotero_summarizer.services.research_feed.profile import load_profile, profile_path

START = datetime(2026, 8, 20, tzinfo=timezone.utc)
END = datetime(2026, 8, 29, tzinfo=timezone.utc)


@pytest.fixture
def environment(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    reviews = {}
    # The existing current reader still executes; only its source/currentness adapter is synthetic.
    monkeypatch.setattr(_review_cache, "_read_all", lambda **_kwargs: deepcopy(reviews))
    monkeypatch.setattr(
        _review_cache, "review_is_current", lambda entry, _key, **_kw: bool(entry) and entry.get("current", True)
    )
    return settings, reviews


@contextmanager
def stage_calls():
    targets = {runner._CANONICAL_TRIAGE.__code__: "triage", runner._CANONICAL_CARD.__code__: "card",
               runner.rank_candidates.__code__: "rank"}
    counts = {name: 0 for name in targets.values()}
    previous = sys.getprofile()

    def observe(frame, event, arg):
        if event == "call" and frame.f_code in targets:
            counts[targets[frame.f_code]] += 1
        if previous is not None:
            previous(frame, event, arg)

    sys.setprofile(observe)
    try:
        yield counts
    finally:
        sys.setprofile(previous)


def run(settings, **kwargs):
    result = runner.run_weekly(settings, start=kwargs.pop("start", START), end=kwargs.pop("end", END), **kwargs)
    return json.loads(Path(result["json_path"]).read_text()), result


def state_path(settings):
    return settings.data_dir / "research_feed" / "state.json"


def test_repeat_and_reconstructed_cursor_skip_stages_but_keep_artifact_and_full_rank(environment):
    settings, reviews = environment
    _seed(settings, "paper", "Agent harness benchmark")
    reviews["paper"] = _review("paper")
    with stage_calls() as first_calls:
        first, result = run(settings)
    original = Path(result["json_path"]).read_bytes()
    assert first_calls == {"triage": 1, "card": 1, "rank": 1}
    # A new cursor instance owns no in-memory results from the preceding invocation.
    rebuilt = cursor.CandidateCursor(settings)
    assert rebuilt.metadata["prior"] == "available"
    with stage_calls() as repeated_calls:
        second, second_result = run(settings)
    assert repeated_calls == {"triage": 0, "card": 0, "rank": 1}
    assert first["cards"] == second["cards"]
    assert first["metadata"]["ranking"] == second["metadata"]["ranking"]
    assert second["metadata"]["candidate_cursor"]["triage_reused"] == 1
    assert second["metadata"]["candidate_cursor"]["cards_reused"] == 1
    assert second_result["json_path"] != result["json_path"]
    assert Path(result["json_path"]).read_bytes() == original


def test_rejected_stage_replays_without_freezing_membership_after_native_change(environment):
    settings, reviews = environment
    for key in ("paper-a", "paper-bb"):
        _seed(settings, key, f"Agent harness benchmark {key}")
        reviews[key] = _review(key)
    with sqlite3.connect(settings.triage_db_path) as connection:
        connection.execute("UPDATE processed_feed_items SET decision='user_rejected' WHERE stable_feed_key='paper-bb'")
    run(settings)
    with stage_calls() as repeated:
        rejected, _ = run(settings)
    assert repeated == {"triage": 0, "card": 0, "rank": 1}
    assert rejected["metadata"]["source_history"]["paper-bb"]["admission"]["reason"] == "native_veto"
    with sqlite3.connect(settings.triage_db_path) as connection:
        connection.execute("UPDATE processed_feed_items SET decision='selected' WHERE stable_feed_key='paper-bb'")
    with stage_calls() as changed:
        admitted, _ = run(settings)
    assert changed == {"triage": 1, "card": 1, "rank": 1}
    assert {row["candidate"]["source_id"] for row in admitted["cards"]} == {"paper-a", "paper-bb"}


def test_generate_reuses_current_review_without_another_model_start(environment, monkeypatch, tmp_path):
    settings, reviews = environment
    _seed(settings, "paper", "Agent harness benchmark")

    def finish(*, item_keys, **_kwargs):
        from tests.test_feed_assessment_producer import produce_review

        for key in item_keys:
            reviews[key], _context, _generator, _mapper = produce_review(
                tmp_path, monkeypatch, selected_profile=load_profile(settings.data_dir), source_id=key)

    start = Mock(side_effect=finish)
    monkeypatch.setattr(deep_review, "start", start)
    monkeypatch.setattr(deep_review, "status", lambda _key: {"status": "done"})
    first, _ = run(settings, generate_reviews=True)
    with stage_calls() as calls:
        second, _ = run(settings, generate_reviews=True)
    assert start.call_count == 1
    assert first["cards"] == second["cards"]
    assert calls == {"triage": 0, "card": 0, "rank": 1}


def test_budget_cut_then_larger_budget_reuses_triage_not_unprocessed_cards_and_dedups_tags(environment):
    settings, reviews = environment
    for key in ("paper-a", "paper-bb"):
        _seed(settings, key, f"Agent harness benchmark {key}", materialized=f"Z-{key}")
        reviews[key] = _review(key)
    first, _ = run(settings, card_budget=1, queue_zotero=True, dry_run=False)
    with stage_calls() as calls:
        second, _ = run(settings, card_budget=2, queue_zotero=True, dry_run=False)
    assert calls == {"triage": 0, "card": 1, "rank": 1}
    assert len(first["cards"]) == 1 and len(second["cards"]) == 2
    assert first["metadata"]["ranking"] == second["metadata"]["ranking"]
    assert second["metadata"]["writebacks"]["succeeded"] == 1
    with stage_calls() as repeated:
        third, _ = run(settings, card_budget=2, queue_zotero=True, dry_run=False)
    assert repeated == {"triage": 0, "card": 0, "rank": 1}
    assert third["metadata"]["writebacks"]["succeeded"] == 0
    run(settings, card_budget=2, queue_zotero=True, dry_run=True)
    with sqlite3.connect(settings.triage_db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM pending_changes").fetchone()[0] == 2


def test_overlapping_and_reranged_windows_keep_whole_current_cohort(environment):
    settings, reviews = environment
    for key, day in (("a", 23), ("bb", 25), ("ccc", 27)):
        _seed(settings, key, f"Agent harness benchmark {key}")
        reviews[key] = _review(key)
        with sqlite3.connect(settings.triage_db_path) as connection:
            connection.execute("UPDATE rss_items SET publication_date=? WHERE stable_feed_key=?",
                               (f"2026-08-{day}T00:00:00Z", key))
    run(settings, end=datetime(2026, 8, 26, tzinfo=timezone.utc))
    with stage_calls() as overlap:
        second, _ = run(settings, start=datetime(2026, 8, 24, tzinfo=timezone.utc))
    assert overlap == {"triage": 1, "card": 1, "rank": 1}
    assert set(second["metadata"]["ranking"]["inputs"]) == {"bb", "ccc"}
    with stage_calls() as rerange:
        third, _ = run(settings)
    assert rerange == {"triage": 0, "card": 0, "rank": 1}
    assert set(third["metadata"]["ranking"]["inputs"]) == {"a", "bb", "ccc"}


@pytest.mark.parametrize("change", ["content", "profile", "native_score", "native_model", "review", "contract"])
def test_relevant_input_changes_invalidate_only_qualified_work(environment, monkeypatch, change):
    settings, reviews = environment
    _seed(settings, "paper", "Agent harness benchmark")
    reviews["paper"] = _review("paper")
    run(settings)
    if change == "content":
        with sqlite3.connect(settings.triage_db_path) as connection:
            connection.execute("UPDATE rss_items SET abstract='changed authoritative original'")
            connection.execute("UPDATE processed_feed_items SET abstract='changed authoritative original'")
    elif change == "profile":
        profile = load_profile(settings.data_dir)
        profile.projects.append("new declared project")
        profile_path(settings.data_dir).write_text(profile.model_dump_json())
    elif change in {"native_score", "native_model"}:
        column, value = ("composite_score", 3.25) if change == "native_score" else ("model_version", "native-model-v2")
        with sqlite3.connect(settings.triage_db_path) as connection:
            connection.execute(f"UPDATE processed_feed_items SET {column}=?", (value,))
    elif change == "review":
        reviews["paper"]["provenance"]["model"] = "review-model-v2"
        reviews["paper"]["review_identity"]["generation_sha256"] = hashlib.sha256(
            b"synthetic-new-review-generation").hexdigest()
    else:
        original = cursor.stage_identity
        monkeypatch.setattr(cursor, "stage_identity", lambda: original() + "-changed-stage-schema")
    with stage_calls() as calls:
        payload, _ = run(settings)
    assert calls == {"triage": int(change != "review"), "card": 1, "rank": 1}
    assert len(payload["cards"]) == 1


@pytest.mark.parametrize("failure", ["missing_review", "card_error"])
def test_incomplete_candidate_stage_remains_retryable(environment, monkeypatch, failure):
    settings, reviews = environment
    _seed(settings, "paper", "Agent harness benchmark")
    if failure == "card_error":
        reviews["paper"] = _review("paper")
        topic_tags = card.topic_tags
        monkeypatch.setattr(card, "topic_tags", Mock(side_effect=RuntimeError("synthetic card failure")))
    first, _ = run(settings)
    assert first["cards"] == []
    entry = json.loads(state_path(settings).read_text())["candidate_cursor"]["entries"]["paper"]
    assert entry["card"] is None
    if failure == "card_error":
        assert first["failed"]
        monkeypatch.setattr(card, "topic_tags", topic_tags)
    else:
        assert first["manual_full_text_required"] == []
        assert first["unqualified_reviews"][0]["candidate"]["source_id"] == "paper"
        assert first["unqualified_reviews"][0]["reason"] == "review_missing"
        reviews["paper"] = _review("paper")
    with stage_calls() as calls:
        second, _ = run(settings)
    assert calls == {"triage": 0, "card": 1, "rank": 1}
    assert len(second["cards"]) == 1 and not second["failed"]


def test_publication_failure_never_commits_successful_cursor_and_preserves_old_reports(environment, monkeypatch):
    settings, reviews = environment
    _seed(settings, "paper", "Agent harness benchmark")
    reviews["paper"] = _review("paper")
    _, result = run(settings)
    old_report = Path(result["json_path"]).read_bytes()
    old_state = state_path(settings).read_bytes()
    with sqlite3.connect(settings.triage_db_path) as connection:
        connection.execute("UPDATE processed_feed_items SET model_version='changed'")
    persist = runner.persist
    monkeypatch.setattr(runner, "persist", Mock(side_effect=OSError("synthetic publication failure")))
    with pytest.raises(OSError):
        run(settings)
    assert state_path(settings).read_bytes() == old_state
    assert Path(result["json_path"]).read_bytes() == old_report
    monkeypatch.setattr(runner, "persist", persist)
    with stage_calls() as calls:
        payload, _ = run(settings)
    assert calls == {"triage": 1, "card": 1, "rank": 1}
    assert len(payload["cards"]) == 1


@pytest.mark.parametrize("stage", ["triage", "card", "review_loader"])
def test_injected_callbacks_retain_invocation_and_external_state_semantics(environment, monkeypatch, stage):
    settings, reviews = environment
    _seed(settings, "paper", "Agent harness benchmark")
    reviews["paper"] = _review("paper")
    run(settings)
    calls, external = [], ["first"]
    options = {}
    if stage == "triage":
        original = runner.triage_candidate

        def callback(candidate, row, profile):
            calls.append(external[0])
            return original(candidate, row, profile).model_copy(update={"rationale": external[0]})

        monkeypatch.setattr(runner, "triage_candidate", callback)
    elif stage == "card":
        original = runner.build_card

        def callback(*args):
            calls.append(external[0])
            return original(*args).model_copy(update={"problem": external[0]})

        monkeypatch.setattr(runner, "build_card", callback)
    else:
        def callback(key):
            calls.append(external[0])
            review = _review(key)
            review["digest"]["tldr"] = external[0]
            return review

        options["review_loader"] = callback
    first, _ = run(settings, **options)
    external[0] = "second"
    second, _ = run(settings, **options)
    assert calls == ["first", "second"]
    field = first["cards"][0]["triage"] if stage == "triage" else first["cards"][0]["card"]
    changed = second["cards"][0]["triage"] if stage == "triage" else second["cards"][0]["card"]
    name = "rationale" if stage == "triage" else "problem"
    assert field[name] == "first" and changed[name] == "second"
