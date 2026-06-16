"""Review-fleet (Phase-2 background pre-decision of Read-next verdicts).

Covers the four modules with stubs (no real LLM / Zotero / model load):
  * ``propose.propose_verdict`` — the pure deterministic truth-table (the brain).
  * ``verdict_store`` — atomic JSON sidecar round-trip + clear + corrupt-loud.
  * ``fleet`` — the single-flight serial job: cache reuse, on-demand review, the
    status shape, per-item-failure isolation, and the "no Zotero / no review"
    paths — all with deep_review + reading_queue stubbed.
  * ``prewarm`` — knob resolution (config + env + fail-loud) and the enable gate.
"""
from __future__ import annotations

import types

import pytest

from zotero_summarizer.models.triage import ProposedVerdict
from zotero_summarizer.services.library.review_fleet import (
    fleet,
    propose,
    verdict_store,
)
from zotero_summarizer.services.library.review_fleet import prewarm


# ===========================================================================
# propose.propose_verdict — pure truth-table
# ===========================================================================


def _digest(read_decision="read", grade="A"):
    return {"read_decision": read_decision, "grade": grade}


def _quality(band="neutral", overstatements=None, red_flags=None, grade=""):
    return {
        "quality_band": band,
        "overstatements": overstatements or [],
        "red_flags": red_flags or [],
        "grade": grade,
    }


def _goals(*relevant_flags):
    return [{"relevant": r} for r in relevant_flags]


def test_read_high_grade_proposes_must_read():
    v = propose.propose_verdict(_digest("read", "A"), _quality())
    assert isinstance(v, ProposedVerdict)
    assert v.proposed == "must_read"
    assert v.digest_read_decision == "read"
    assert v.grade == "A"
    assert v.confidence >= 0.8
    assert v.source == "review_fleet"
    assert v.proposed_at  # stamped


def test_read_low_grade_proposes_should_read():
    v = propose.propose_verdict(_digest("read", "C"), _quality())
    assert v.proposed == "should_read"


def test_skim_high_grade_proposes_should_read():
    assert propose.propose_verdict(_digest("skim", "A"), _quality()).proposed == "should_read"


def test_skim_low_grade_proposes_could_read():
    assert propose.propose_verdict(_digest("skim", "D"), _quality()).proposed == "could_read"


def test_skip_goal_miss_proposes_dont_read():
    """A clean skip with NO goal match is the only path that proposes a hide."""
    v = propose.propose_verdict(_digest("skip", "D"), _quality(), goal_summaries=_goals(False, False))
    assert v.proposed == "dont_read"


def test_skip_goal_match_keeps_could_read_not_dont_read():
    """ASYMMETRY: a wrong hide costs more than a wrong keep, so a goal-matched
    skip is biased UP to could_read instead of dont_read."""
    v = propose.propose_verdict(_digest("skip", "D"), _quality(), goal_summaries=_goals(True))
    assert v.proposed == "could_read"


def test_no_digest_never_proposes_a_hide():
    """No read_decision (e.g. no PDF) → a safe could_read, never dont_read."""
    for digest in (None, {}, {"read_decision": "", "grade": ""}):
        v = propose.propose_verdict(digest, None)
        assert v.proposed == "could_read"
        assert v.digest_read_decision == ""


def test_uncertain_band_lowers_confidence_and_flags():
    strong = propose.propose_verdict(_digest("read", "A"), _quality(band="neutral"))
    shaky = propose.propose_verdict(_digest("read", "A"), _quality(band="uncertain"))
    assert "quality_uncertain" in shaky.flags
    assert shaky.confidence < strong.confidence
    assert "uncertain" in shaky.rationale


def test_overstatements_lower_confidence_and_flag():
    strong = propose.propose_verdict(_digest("read", "A"), _quality())
    shaky = propose.propose_verdict(_digest("read", "A"), _quality(overstatements=["claim X unsupported"]))
    assert "overstatements" in shaky.flags
    assert shaky.confidence < strong.confidence


def test_flag_band_and_red_flags_surface_as_flags():
    v = propose.propose_verdict(
        _digest("read", "B"), _quality(band="flag", red_flags=["self-citation ring"])
    )
    assert "quality_flag" in v.flags
    assert "red_flags" in v.flags


def test_grade_falls_back_to_quality_when_digest_ungraded():
    v = propose.propose_verdict(_digest("read", ""), _quality(grade="A"))
    assert v.grade == "A"
    assert v.proposed == "must_read"  # high grade still lifts the verdict


def test_unknown_read_decision_is_normalized_to_empty():
    v = propose.propose_verdict({"read_decision": "MAYBE", "grade": "A"}, _quality())
    assert v.digest_read_decision == ""
    assert v.proposed == "could_read"


def test_confidence_is_bounded_unit_interval():
    # Worst-case stack of penalties must still clamp to >= 0.0.
    v = propose.propose_verdict(_digest("skim", ""), _quality(band="uncertain", overstatements=["a"]))
    assert 0.0 <= v.confidence <= 1.0


def test_propose_is_deterministic():
    a = propose.propose_verdict(_digest("read", "A"), _quality(), goal_summaries=_goals(True))
    b = propose.propose_verdict(_digest("read", "A"), _quality(), goal_summaries=_goals(True))
    assert a.proposed == b.proposed and a.confidence == b.confidence and a.flags == b.flags


def test_goal_summaries_non_list_is_unknown_not_a_miss():
    # A malformed / absent goal board is UNKNOWN, not a goal-MISS: absence of
    # evidence is never evidence to hide, so a skip stays could_read (the
    # no-wrong-hide asymmetry). Only a real evaluated miss licenses dont_read.
    v = propose.propose_verdict(_digest("skip", "D"), _quality(), goal_summaries="not-a-list")
    assert v.proposed == "could_read"
    assert v.confidence < 0.6  # withheld → Override-only on the card


# ===========================================================================
# verdict_store — atomic JSON sidecar
# ===========================================================================


@pytest.fixture
def _store_path(tmp_path, monkeypatch):
    path = tmp_path / "proposed_verdicts.json"
    monkeypatch.setattr(verdict_store, "_cache_path", lambda: path)
    return path


def test_read_all_empty_when_absent(_store_path):
    assert verdict_store.read_all() == {}


def test_upsert_then_read_all_round_trip(_store_path):
    proposal = propose.propose_verdict(_digest("read", "A"), _quality()).model_dump()
    verdict_store.upsert("ABC123", proposal)
    stored = verdict_store.read_all()
    assert set(stored) == {"ABC123"}
    assert stored["ABC123"]["proposed"] == "must_read"


def test_upsert_replaces_existing_key(_store_path):
    verdict_store.upsert("K", {"proposed": "must_read"})
    verdict_store.upsert("K", {"proposed": "could_read"})
    assert verdict_store.read_all()["K"]["proposed"] == "could_read"


def test_upsert_preserves_other_keys(_store_path):
    verdict_store.upsert("A", {"proposed": "must_read"})
    verdict_store.upsert("B", {"proposed": "skip"})
    assert set(verdict_store.read_all()) == {"A", "B"}


def test_clear_removes_only_the_key(_store_path):
    verdict_store.upsert("A", {"proposed": "must_read"})
    verdict_store.upsert("B", {"proposed": "could_read"})
    assert verdict_store.clear("A") is True
    assert set(verdict_store.read_all()) == {"B"}


def test_clear_missing_key_returns_false(_store_path):
    assert verdict_store.clear("nope") is False


def test_upsert_rejects_empty_key(_store_path):
    with pytest.raises(ValueError):
        verdict_store.upsert("", {"proposed": "must_read"})


def test_read_all_raises_loud_on_corrupt_file(_store_path):
    _store_path.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(Exception):
        verdict_store.read_all()


def test_write_is_atomic_no_tmp_left_behind(_store_path):
    verdict_store.upsert("A", {"proposed": "must_read"})
    assert _store_path.exists()
    assert not _store_path.with_suffix(".tmp").exists()


# ===========================================================================
# fleet — single-flight serial job
# ===========================================================================


@pytest.fixture(autouse=True)
def _reset_fleet(tmp_path, monkeypatch):
    """Reset the module-global latch + state and isolate the store file."""
    fleet.finish(error=None)
    with fleet._LOCK:
        fleet._STATE.update({"total": 0, "completed": 0, "started_at": None, "progress": {}})
    monkeypatch.setattr(
        verdict_store, "_cache_path", lambda: tmp_path / "proposed_verdicts.json"
    )
    # Run the "background" job inline so the test is deterministic.
    monkeypatch.setattr(fleet._flight, "run_in_background", lambda target: target())
    yield
    fleet.finish(error=None)


def _stub_queue(monkeypatch, keys):
    monkeypatch.setattr(
        fleet.reading_queue,
        "build_reading_queue",
        lambda **_k: {"items": [{"item_key": k} for k in keys]},
    )


def _cached_review(read_decision="read", grade="A"):
    return {"digest": _digest(read_decision, grade), "quality": _quality(), "goal_summaries": []}


def test_status_idle_before_any_run():
    s = fleet.status()
    assert s["status"] == "idle"
    assert s["completed"] == 0


def test_run_uses_cached_review_without_starting_deep_review(monkeypatch):
    _stub_queue(monkeypatch, ["A", "B"])
    monkeypatch.setattr(fleet.deep_review, "get_cached_review", lambda key: _cached_review())

    def _boom(**_kw):
        raise AssertionError("deep_review.start must not run when reviews are cached")

    monkeypatch.setattr(fleet.deep_review, "start", _boom)

    fleet.start(top_k=2)
    s = fleet.status()
    assert s["status"] == "ready"
    assert s["total"] == 2 and s["completed"] == 2
    stored = verdict_store.read_all()
    assert set(stored) == {"A", "B"}
    assert stored["A"]["proposed"] == "must_read"


def test_run_computes_missing_review_serially(monkeypatch):
    _stub_queue(monkeypatch, ["A"])
    calls = {"start": 0, "status": 0}
    # First get_cached_review (pre-check) miss, then a hit after start+poll.
    seq = iter([None, _cached_review("skim", "B")])
    monkeypatch.setattr(fleet.deep_review, "get_cached_review", lambda key: next(seq))
    monkeypatch.setattr(fleet.deep_review, "start", lambda **_kw: calls.__setitem__("start", calls["start"] + 1))

    def _status():
        calls["status"] += 1
        return {"status": "ready"}  # settles immediately

    monkeypatch.setattr(fleet.deep_review, "status", _status)
    monkeypatch.setattr(fleet.time, "sleep", lambda _s: pytest.fail("must not sleep once settled"))

    fleet.start(top_k=1)
    assert calls["start"] == 1
    assert verdict_store.read_all()["A"]["proposed"] == "should_read"


def test_run_polls_until_deep_review_settles(monkeypatch):
    _stub_queue(monkeypatch, ["A"])
    seq = iter([None, _cached_review()])
    monkeypatch.setattr(fleet.deep_review, "get_cached_review", lambda key: next(seq))
    monkeypatch.setattr(fleet.deep_review, "start", lambda **_kw: None)
    statuses = iter(["running", "running", "ready"])
    monkeypatch.setattr(fleet.deep_review, "status", lambda: {"status": next(statuses)})
    sleeps = {"n": 0}
    monkeypatch.setattr(fleet.time, "sleep", lambda _s: sleeps.__setitem__("n", sleeps["n"] + 1))

    fleet.start(top_k=1)
    assert sleeps["n"] == 2  # polled twice before it went ready
    assert "A" in verdict_store.read_all()


def test_run_skips_item_with_no_review(monkeypatch):
    """A paper that never produces a review (no PDF) is counted-complete but
    stores no proposal — never a guessed verdict."""
    _stub_queue(monkeypatch, ["A"])
    monkeypatch.setattr(fleet.deep_review, "get_cached_review", lambda key: None)
    monkeypatch.setattr(fleet.deep_review, "start", lambda **_kw: None)
    monkeypatch.setattr(fleet.deep_review, "status", lambda: {"status": "ready"})

    fleet.start(top_k=1)
    s = fleet.status()
    assert s["completed"] == 1
    assert verdict_store.read_all() == {}


def test_per_item_failure_is_isolated(monkeypatch):
    """One bad item logs + is skipped; the rest still get proposals."""
    _stub_queue(monkeypatch, ["GOOD", "BAD"])

    def _cached(key):
        if key == "BAD":
            raise RuntimeError("review read blew up")
        return _cached_review()

    monkeypatch.setattr(fleet.deep_review, "get_cached_review", _cached)
    fleet.start(top_k=2)
    s = fleet.status()
    assert s["status"] == "ready"
    assert s["completed"] == 2
    assert set(verdict_store.read_all()) == {"GOOD"}


def test_job_level_failure_sets_error_status(monkeypatch):
    def _explode(**_k):
        raise RuntimeError("queue build crashed")

    monkeypatch.setattr(fleet.reading_queue, "build_reading_queue", _explode)
    fleet.start(top_k=3)
    s = fleet.status()
    assert s["status"] == "error"
    assert "queue build crashed" in s["error"]


def test_single_flight_second_start_is_noop(monkeypatch):
    _stub_queue(monkeypatch, ["A"])
    monkeypatch.setattr(fleet.deep_review, "get_cached_review", lambda key: _cached_review())
    assert fleet.try_start() is True  # claim the slot manually
    # While the slot is held, start() must NOT run the job.
    monkeypatch.setattr(
        fleet.reading_queue, "build_reading_queue",
        lambda **_k: pytest.fail("job must not run while slot is held"),
    )
    s = fleet.start(top_k=1)
    assert s["status"] == "running"
    fleet.finish(error=None)


# ===========================================================================
# prewarm — knob resolution + enable gate (mirrors deep_review_prewarm)
# ===========================================================================


def _config(*, prewarm_k=5, enabled=True):
    return types.SimpleNamespace(
        quality_review=types.SimpleNamespace(prewarm_on_startup_k=prewarm_k, enabled=enabled),
    )


def _app_state(*, reader=object()):
    return types.SimpleNamespace(zotero_reader=reader)


@pytest.fixture(autouse=True)
def _clear_prewarm_env(monkeypatch):
    monkeypatch.delenv(prewarm._ENV_PREWARM_K, raising=False)
    yield


def test_resolve_uses_config_when_env_unset():
    assert prewarm.resolve_prewarm_k(_config(prewarm_k=3)) == 3


def test_resolve_env_supersedes_config(monkeypatch):
    monkeypatch.setenv(prewarm._ENV_PREWARM_K, "7")
    assert prewarm.resolve_prewarm_k(_config(prewarm_k=3)) == 7


def test_resolve_rejects_non_integer_env_loudly(monkeypatch):
    monkeypatch.setenv(prewarm._ENV_PREWARM_K, "lots")
    with pytest.raises(ValueError, match=prewarm._ENV_PREWARM_K):
        prewarm.resolve_prewarm_k(_config())


def test_resolve_rejects_negative_env_loudly(monkeypatch):
    monkeypatch.setenv(prewarm._ENV_PREWARM_K, "-1")
    with pytest.raises(ValueError, match=">= 0"):
        prewarm.resolve_prewarm_k(_config())


def test_schedule_runs_worker_with_resolved_k(monkeypatch):
    seen = {}
    monkeypatch.setattr(prewarm, "_prewarm_worker", lambda k: seen.update(k=k))
    monkeypatch.setattr(prewarm._flight, "run_in_background", lambda target: target())
    assert prewarm.schedule_on_startup(_config(prewarm_k=3), _app_state()) is True
    assert seen["k"] == 3


def test_schedule_skips_when_k_zero(monkeypatch):
    monkeypatch.setattr(prewarm._flight, "run_in_background", _never_spawn)
    assert prewarm.schedule_on_startup(_config(prewarm_k=0), _app_state()) is False


def test_schedule_skips_when_deep_review_disabled(monkeypatch):
    monkeypatch.setattr(prewarm._flight, "run_in_background", _never_spawn)
    assert prewarm.schedule_on_startup(_config(enabled=False), _app_state()) is False


def test_schedule_skips_without_zotero_reader(monkeypatch):
    monkeypatch.setattr(prewarm._flight, "run_in_background", _never_spawn)
    assert prewarm.schedule_on_startup(_config(), _app_state(reader=None)) is False


def test_worker_starts_fleet_with_k(monkeypatch):
    captured = {}
    monkeypatch.setattr(prewarm.fleet, "start", lambda *, top_k: captured.update(top_k=top_k))
    prewarm._prewarm_worker(4)
    assert captured == {"top_k": 4}


def test_worker_swallows_failures(monkeypatch):
    def _boom(*, top_k):
        raise RuntimeError("fleet kickoff blew up")

    monkeypatch.setattr(prewarm.fleet, "start", _boom)
    prewarm._prewarm_worker(2)  # logged + swallowed, no raise


def _never_spawn(_target):
    raise AssertionError("run_in_background must not be called when prewarm is disabled")
