"""Unit tests for the review-fleet background job (``fleet``).

Everything heavy is monkeypatched — no LLM, no real queue, no model dir, and the
daemon thread is run INLINE so the job is fully synchronous and deterministic:

  - ``reading_queue.build_reading_queue``  -> a fixed top-K slice;
  - ``deep_review.get_cached_review`` / ``deep_review.start`` / ``deep_review.status``
    -> a cached-hit (no model load) or a one-poll settle;
  - ``verdict_store.upsert``               -> an in-memory spy;
  - ``_flight.run_in_background``          -> inline call (no thread).

The two load-bearing guarantees asserted here:
  1. SINGLE-FLIGHT — a second ``start`` while a run is in flight is a no-op
     (returns the running status, spawns nothing);
  2. SIDECAR-ONLY — the job writes the proposal sidecar but performs NO write to
     ``label_verdicts`` (indirect-prompt-injection rule: a suggestion is never an
     auto-applied label). A tripwire on the label-verdict writer proves it.
"""
from __future__ import annotations

import pytest

from zotero_summarizer.models.triage import ProposedVerdict
from zotero_summarizer.services.library.review_fleet import fleet
from zotero_summarizer.storage import repositories


@pytest.fixture(autouse=True)
def _reset_latch_and_inline_threads(monkeypatch):
    """Each test starts from a clean latch + state, and the 'background' thread
    runs inline so the job finishes before ``start`` returns."""
    fleet._LATCH.finish(None)  # release any slot a prior test left claimed
    with fleet._LOCK:
        fleet._STATE.update(total=0, completed=0, started_at=None, progress={})
    monkeypatch.setattr(fleet._flight, "run_in_background", lambda target: target())
    yield
    fleet._LATCH.finish(None)


@pytest.fixture(autouse=True)
def _label_verdict_tripwire(monkeypatch):
    """Any write to ``label_verdicts`` during a fleet run is a hard failure: the
    fleet must only write its OWN sidecar, never a confirmed label / Zotero."""

    def _forbidden(*_a, **_k):
        raise AssertionError("review_fleet must NOT write label_verdicts")

    monkeypatch.setattr(repositories, "insert_or_update_label_verdict", _forbidden)
    monkeypatch.setattr(repositories, "delete_label_verdict", _forbidden)


def _queue(*keys):
    return {"items": [{"item_key": k} for k in keys]}


def _review(read_decision="read", grade="A", *, relevant=True):
    return {
        "digest": {"read_decision": read_decision, "grade": grade},
        "quality": {"quality_band": "ok"},
        "goal_summaries": [{"relevant": relevant}],
    }


# --- the happy path: cached reviews -> sidecar upserts, no model load --------------


def test_run_writes_a_proposal_per_top_k_item_from_cache(monkeypatch):
    monkeypatch.setattr(fleet.reading_queue, "build_reading_queue", lambda **_k: _queue("A", "B"))
    monkeypatch.setattr(fleet.deep_review, "get_cached_review", lambda key: _review())

    def _no_model(**_kw):
        raise AssertionError("deep_review.start must not run when reviews are cached")

    monkeypatch.setattr(fleet.deep_review, "start", _no_model)

    upserts: dict = {}
    monkeypatch.setattr(fleet.verdict_store, "upsert", lambda key, prop: upserts.update({key: prop}))

    out = fleet.start(top_k=2)

    assert set(upserts) == {"A", "B"}
    # the stored value is a serialized ProposedVerdict (the propose truth-table ran)
    assert upserts["A"]["proposed"] == "must_read"  # read + grade A
    assert upserts["A"]["source"] == "review_fleet"
    ProposedVerdict(**upserts["A"])  # round-trips through the model -> valid shape
    assert out["status"] == "ready"
    assert out["total"] == 2 and out["completed"] == 2


def test_run_slices_to_top_k(monkeypatch):
    monkeypatch.setattr(
        fleet.reading_queue, "build_reading_queue", lambda **_k: _queue("A", "B", "C", "D")
    )
    monkeypatch.setattr(fleet.deep_review, "get_cached_review", lambda key: _review())
    monkeypatch.setattr(fleet.deep_review, "start", lambda **_k: None)
    upserts: list = []
    monkeypatch.setattr(fleet.verdict_store, "upsert", lambda key, prop: upserts.append(key))

    fleet.start(top_k=2)
    assert upserts == ["A", "B"]  # only the top-2, in queue order


# --- the cache MISS path: triggers a serial deep review, polls, re-reads -----------


def test_cache_miss_triggers_deep_review_then_proposes(monkeypatch):
    started: list = []
    monkeypatch.setattr(fleet.reading_queue, "build_reading_queue", lambda **_k: _queue("A"))

    # First read -> miss; after deep_review.start is called, the second read hits.
    reads = {"n": 0}

    def _get_cached(key):
        reads["n"] += 1
        return None if reads["n"] == 1 else _review(grade="C")

    monkeypatch.setattr(fleet.deep_review, "get_cached_review", _get_cached)
    monkeypatch.setattr(fleet.deep_review, "start", lambda **kw: started.append(kw))
    # status settles immediately (not 'running') so the poll loop never sleeps
    monkeypatch.setattr(fleet.deep_review, "status", lambda: {"status": "ready"})
    monkeypatch.setattr(fleet.time, "sleep", lambda _s: pytest.fail("must not sleep when settled"))

    upserts: dict = {}
    monkeypatch.setattr(fleet.verdict_store, "upsert", lambda key, prop: upserts.update({key: prop}))

    fleet.start(top_k=1)

    assert started == [{"item_keys": ["A"]}]  # serial single-key trigger
    assert upserts["A"]["proposed"] == "should_read"  # read + grade C


def test_cache_miss_that_never_materializes_writes_no_proposal(monkeypatch):
    """A review that never appears (no PDF / errored) -> no proposal, but the item
    still counts as completed and the job finishes cleanly."""
    monkeypatch.setattr(fleet.reading_queue, "build_reading_queue", lambda **_k: _queue("A"))
    monkeypatch.setattr(fleet.deep_review, "get_cached_review", lambda key: None)  # always a miss
    monkeypatch.setattr(fleet.deep_review, "start", lambda **_k: None)
    monkeypatch.setattr(fleet.deep_review, "status", lambda: {"status": "ready"})
    monkeypatch.setattr(fleet.time, "sleep", lambda _s: None)
    upserts: dict = {}
    monkeypatch.setattr(fleet.verdict_store, "upsert", lambda key, prop: upserts.update({key: prop}))

    out = fleet.start(top_k=1)
    assert upserts == {}
    assert out["completed"] == 1  # counted even though nothing was proposed


# --- single-flight: a second start while running is a no-op ------------------------


def test_second_start_is_a_noop_while_running(monkeypatch):
    """Claim the slot directly (simulating an in-flight run), then assert ``start``
    returns the running status and spawns NOTHING."""
    assert fleet.try_start() is True  # a run is now 'in flight'

    def _must_not_spawn(_target):
        raise AssertionError("start must not spawn a second run while one is in flight")

    monkeypatch.setattr(fleet._flight, "run_in_background", _must_not_spawn)

    def _must_not_build(**_k):
        raise AssertionError("start must not build the queue while a run is in flight")

    monkeypatch.setattr(fleet.reading_queue, "build_reading_queue", _must_not_build)

    out = fleet.start(top_k=5)
    assert out["status"] == "running"  # the in-flight status, not a fresh run


def test_serial_runs_after_finish_are_allowed(monkeypatch):
    """Once a run finishes, the slot frees and a fresh start runs again."""
    monkeypatch.setattr(fleet.reading_queue, "build_reading_queue", lambda **_k: _queue("A"))
    monkeypatch.setattr(fleet.deep_review, "get_cached_review", lambda key: _review())
    monkeypatch.setattr(fleet.deep_review, "start", lambda **_k: None)
    monkeypatch.setattr(fleet.verdict_store, "upsert", lambda key, prop: None)

    first = fleet.start(top_k=1)
    assert first["status"] == "ready"  # finished (inline) -> slot freed
    second = fleet.start(top_k=1)  # not blocked
    assert second["status"] == "ready"


# --- the sidecar-only guarantee + per-item failure isolation -----------------------


def test_job_writes_sidecar_but_never_label_verdicts(monkeypatch):
    """The defining safety property: a fleet run writes proposals to the sidecar and
    makes NO write to label_verdicts (the tripwire fixture would raise otherwise)."""
    monkeypatch.setattr(fleet.reading_queue, "build_reading_queue", lambda **_k: _queue("A", "B"))
    monkeypatch.setattr(fleet.deep_review, "get_cached_review", lambda key: _review())
    monkeypatch.setattr(fleet.deep_review, "start", lambda **_k: None)
    sidecar: dict = {}
    monkeypatch.setattr(fleet.verdict_store, "upsert", lambda key, prop: sidecar.update({key: prop}))

    out = fleet.start(top_k=2)

    assert set(sidecar) == {"A", "B"}  # sidecar WAS written...
    assert out["completed"] == 2  # ...and the run completed without the tripwire firing


def test_per_item_failure_is_isolated_and_run_continues(monkeypatch):
    """A single bad item is logged and skipped; the rest of the batch still runs."""
    monkeypatch.setattr(fleet.reading_queue, "build_reading_queue", lambda **_k: _queue("A", "B"))
    monkeypatch.setattr(fleet.deep_review, "get_cached_review", lambda key: _review())
    monkeypatch.setattr(fleet.deep_review, "start", lambda **_k: None)

    def _upsert(key, prop):
        if key == "A":
            raise RuntimeError("disk gremlin on A")
        good.append(key)

    good: list = []
    monkeypatch.setattr(fleet.verdict_store, "upsert", _upsert)

    out = fleet.start(top_k=2)
    assert good == ["B"]  # B still processed after A failed
    assert out["completed"] == 2  # both counted; the failure did not abort the run
    assert out["status"] == "ready"  # job-level success despite a per-item error


def test_job_level_failure_sets_error_status(monkeypatch):
    """A failure OUTSIDE the per-item loop (queue build) surfaces as status=error."""

    def _explode(**_k):
        raise RuntimeError("queue build blew up")

    monkeypatch.setattr(fleet.reading_queue, "build_reading_queue", _explode)
    out = fleet.start(top_k=2)
    assert out["status"] == "error"
    assert "queue build blew up" in (out["error"] or "")
