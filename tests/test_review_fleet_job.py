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
        fleet._STATE.update(
            total=0, completed=0, proposed=0, no_fetchable_source=0,
            needs_library_login=0, needs_login_items=[], failed=0, started_at=None, progress={},
        )
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
    assert out["proposed"] == 2  # every processed pick yielded a verdict


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


# --- next-5 advance: skip already-decided picks so a re-run walks the queue ---------


def test_select_keys_skips_proposed_and_labeled_picks():
    """The 'predict the NEXT 5' rule: rows that already carry a ``proposed_verdict``
    (the fleet's own output) or a ``user_priority`` (the human's label) are skipped,
    so selection advances to the still-undecided picks, capped at ``top_k``."""
    queue = {
        "items": [
            {"item_key": "done", "proposed_verdict": {"proposed": "must_read"}},
            {"item_key": "labeled", "user_priority": "should_read"},
            {"item_key": "fresh1"},
            {"item_key": "fresh2"},
            {"item_key": "fresh3"},
        ]
    }
    assert fleet._select_keys(queue, top_k=2) == ["fresh1", "fresh2"]


def test_select_keys_returns_fewer_when_undecided_tail_is_short():
    """When the queue's undecided tail is shorter than top_k, take what's there."""
    queue = {
        "items": [
            {"item_key": "done", "proposed_verdict": {"proposed": "could_read"}},
            {"item_key": "fresh1"},
        ]
    }
    assert fleet._select_keys(queue, top_k=5) == ["fresh1"]


def test_run_only_proposes_for_undecided_picks(monkeypatch):
    """End-to-end through ``start()``: a queue whose top picks are already decided
    yields proposals only for the next undecided ones — a re-run advances."""
    queue = {
        "items": [
            {"item_key": "A", "proposed_verdict": {"proposed": "must_read"}},
            {"item_key": "B", "user_priority": "should_read"},
            {"item_key": "C"},
            {"item_key": "D"},
        ]
    }
    monkeypatch.setattr(fleet.reading_queue, "build_reading_queue", lambda **_k: queue)
    monkeypatch.setattr(fleet.deep_review, "get_cached_review", lambda key: _review())
    monkeypatch.setattr(fleet.deep_review, "start", lambda **_k: None)
    upserts: list = []
    monkeypatch.setattr(fleet.verdict_store, "upsert", lambda key, prop: upserts.append(key))

    fleet.start(top_k=2)
    assert upserts == ["C", "D"]  # A (proposed) and B (labeled) skipped


# --- the cache MISS path: triggers a serial deep review, polls, re-reads -----------


def test_cache_miss_triggers_deep_review_then_proposes(monkeypatch):
    started: list = []
    monkeypatch.setattr(fleet.reading_queue, "build_reading_queue", lambda **_k: _queue("A"))

    # Empty cache -> the ONE batched deep_review.start reviews A INTO the cache (grade C).
    cache: dict = {}

    def _start(*, item_keys=None, pdf_overrides=None, **_kw):
        started.append({"item_keys": list(item_keys or []), "pdf_overrides": pdf_overrides})
        for k in item_keys or []:
            cache[k] = _review(grade="C")
        return {"accepted": True}  # our job claimed the slot — the C1 contract

    monkeypatch.setattr(fleet.deep_review, "get_cached_review", lambda key: cache.get(key))
    monkeypatch.setattr(fleet.deep_review, "start", _start)
    # status settles immediately (not 'running') so the poll loop never sleeps
    monkeypatch.setattr(fleet.deep_review, "status", lambda: {"status": "ready"})
    monkeypatch.setattr(fleet.time, "sleep", lambda _s: pytest.fail("must not sleep when settled"))

    upserts: dict = {}
    monkeypatch.setattr(fleet.verdict_store, "upsert", lambda key, prop: upserts.update({key: prop}))

    fleet.start(top_k=1)

    assert started == [{"item_keys": ["A"], "pdf_overrides": None}]  # one batched trigger
    assert upserts["A"]["proposed"] == "should_read"  # read + grade C


def test_cache_miss_that_never_materializes_reports_done_empty(monkeypatch):
    """A review that never appears (deep review errored / produced nothing) -> the
    item is counted as PROCESSED and FAILED, the run proposes nothing, and the
    status is the honest ``done_empty`` (not a false ``ready``)."""
    monkeypatch.setattr(fleet.reading_queue, "build_reading_queue", lambda **_k: _queue("A"))
    monkeypatch.setattr(fleet.deep_review, "get_cached_review", lambda key: None)  # always a miss
    # Our job IS accepted but produces no cache entry → genuinely failed (C1: the
    # accepted-but-empty path stops, not an infinite re-claim loop).
    monkeypatch.setattr(fleet.deep_review, "start", lambda **_k: {"accepted": True})
    monkeypatch.setattr(fleet.deep_review, "status", lambda: {"status": "ready"})
    monkeypatch.setattr(fleet.time, "sleep", lambda _s: None)
    upserts: dict = {}
    monkeypatch.setattr(fleet.verdict_store, "upsert", lambda key, prop: upserts.update({key: prop}))

    out = fleet.start(top_k=1)
    assert upserts == {}
    assert out["completed"] == 1  # counted even though nothing was proposed
    assert out["proposed"] == 0 and out["failed"] == 1
    assert out["status"] == "done_empty"


def test_run_over_fulltextless_papers_reports_done_empty(monkeypatch):
    """Picks have a cached ``needs_pdf`` review AND no PDF source can be acquired (no
    arXiv/OA, no proxied source) -> the fleet reports the honest ``done_empty`` /
    ``no_fetchable_source`` rather than a false ``ready``."""
    import types

    from zotero_summarizer.services.library import _pdf_acquire
    from zotero_summarizer.services.zotero import zotero as zmod

    monkeypatch.setattr(fleet.reading_queue, "build_reading_queue", lambda **_k: _queue("A", "B"))
    # cached review EXISTS but carries no usable full text (the deep_review needs_pdf case)
    monkeypatch.setattr(fleet.deep_review, "get_cached_review",
                        lambda key: {"needs_pdf": True, "digest": None})
    monkeypatch.setattr(zmod, "get_zotero_reader_or_raise", lambda: types.SimpleNamespace(
        get_item_detail=lambda k: {"has_pdf": False, "url": "", "doi": ""}))
    # No source can be acquired, and it's NOT a login issue → no_fetchable_source.
    monkeypatch.setattr(_pdf_acquire, "acquire_pdf_for",
                        lambda key, detail: _pdf_acquire.AcquireResult(path=None, needs_login=False))

    def _no_model(**_kw):
        raise AssertionError("deep_review.start must not run when no PDF was acquired")

    monkeypatch.setattr(fleet.deep_review, "start", _no_model)
    upserts: dict = {}
    monkeypatch.setattr(fleet.verdict_store, "upsert", lambda key, prop: upserts.update({key: prop}))

    out = fleet.start(top_k=2)
    assert upserts == {}  # nothing stored
    assert out["proposed"] == 0 and out["no_fetchable_source"] == 2
    assert out["status"] == "done_empty"


def test_needs_library_login_when_proxied_source_unreachable(monkeypatch):
    """A proxied/paywalled source EXISTS but the browser isn't logged in → the pick is
    the actionable ``needs_library_login`` (distinct from ``no_fetchable_source``)."""
    import types

    from zotero_summarizer.services.library import _pdf_acquire
    from zotero_summarizer.services.zotero import zotero as zmod

    monkeypatch.setattr(fleet.reading_queue, "build_reading_queue", lambda **_k: _queue("A"))
    monkeypatch.setattr(fleet.deep_review, "get_cached_review",
                        lambda key: {"needs_pdf": True, "digest": None})
    monkeypatch.setattr(zmod, "get_zotero_reader_or_raise", lambda: types.SimpleNamespace(
        get_item_detail=lambda k: {"has_pdf": False, "url": "https://www.nature.com/x", "doi": "10.1/x"}))
    monkeypatch.setattr(_pdf_acquire, "acquire_pdf_for",
                        lambda key, detail: _pdf_acquire.AcquireResult(path=None, needs_login=True))
    monkeypatch.setattr(fleet.deep_review, "start",
                        lambda **_k: pytest.fail("must not re-review when no PDF was acquired"))
    monkeypatch.setattr(fleet.verdict_store, "upsert", lambda key, prop: pytest.fail("nothing to propose"))

    out = fleet.start(top_k=1)
    assert out["proposed"] == 0 and out["needs_library_login"] == 1
    assert out["status"] == "done_empty"
    # the gated pick is surfaced as a sign-in link (open → log in → re-Predict)
    assert out["needs_login_items"] == [{"item_key": "A", "title": "A", "url": "https://www.nature.com/x"}]


def test_run_job_scans_the_whole_library_not_a_fixed_window(monkeypatch):
    """Regression for the heavily-labeled silent no-op: the fleet must rank a large
    slice (not max(top_k*8, 40)) so undecided picks BELOW the pinned/labeled prefix
    are reachable. (With ~38+ labeled papers a 40-row window selected zero keys.)"""
    captured: dict = {}

    def _build(**kw):
        captured.update(kw)
        return _queue("A")

    monkeypatch.setattr(fleet.reading_queue, "build_reading_queue", _build)
    monkeypatch.setattr(fleet.deep_review, "get_cached_review", lambda key: _review())
    monkeypatch.setattr(fleet.deep_review, "start", lambda **_k: None)
    monkeypatch.setattr(fleet.verdict_store, "upsert", lambda key, prop: None)

    fleet.start(top_k=5)
    assert captured.get("limit", 0) >= 1000  # whole-library scan, not a 40-row window


def test_needs_pdf_pick_acquires_then_re_reviews_and_proposes(monkeypatch):
    """The acquisition path: a PDF-less pick reviews to needs_pdf, the fleet acquires a
    PDF (OA/browser), then a FORCED re-review FROM THAT PATH (via pdf_overrides) produces
    a digest, then a verdict is proposed."""
    import types

    from zotero_summarizer.services.library import _pdf_acquire
    from zotero_summarizer.services.zotero import zotero as zmod

    monkeypatch.setattr(fleet.reading_queue, "build_reading_queue", lambda **_k: _queue("A"))

    cache: dict = {}
    starts: list = []

    def _start(*, item_keys=None, pdf_overrides=None, **_kw):
        starts.append({"item_keys": list(item_keys or []), "pdf_overrides": pdf_overrides})
        for k in item_keys or []:
            # pass 1 (no override) -> needs_pdf; forced re-review WITH the path -> a digest.
            has_override = bool(pdf_overrides and k in pdf_overrides)
            cache[k] = _review(grade="A") if has_override else {"needs_pdf": True, "digest": None}
        return {"accepted": True}

    monkeypatch.setattr(fleet.deep_review, "get_cached_review", lambda key: cache.get(key))
    monkeypatch.setattr(fleet.deep_review, "start", _start)
    monkeypatch.setattr(fleet.deep_review, "status", lambda: {"status": "ready"})
    monkeypatch.setattr(fleet.time, "sleep", lambda _s: None)
    monkeypatch.setattr(zmod, "get_zotero_reader_or_raise", lambda: types.SimpleNamespace(
        get_item_detail=lambda k: {"has_pdf": False, "url": "", "doi": ""}))
    monkeypatch.setattr(_pdf_acquire, "acquire_pdf_for",
                        lambda key, detail: _pdf_acquire.AcquireResult(path="/tmp/A.pdf"))
    upserts: dict = {}
    monkeypatch.setattr(fleet.verdict_store, "upsert", lambda key, prop: upserts.update({key: prop}))

    out = fleet.start(top_k=1)
    # pass 1 reviews from Zotero (needs_pdf), pass 2 re-reviews WITH the acquired path.
    assert [s["pdf_overrides"] for s in starts] == [None, {"A": "/tmp/A.pdf"}]
    assert upserts["A"]["proposed"] == "must_read"          # read + grade A
    assert out["proposed"] == 1 and out["status"] == "ready"


def test_stale_digestless_cache_is_recomputed_not_trusted(monkeypatch):
    """A cached review with NO digest that is NOT needs_pdf is a stale FAILURE:
    ``_usable_cache`` treats it as a miss (returns None) so the pick lands in the
    to-review batch and is recomputed (deep review works now), rather than being
    trusted as-is and skipped forever."""
    # Stale failed entry: had a PDF (needs_pdf falsy) but produced no digest.
    monkeypatch.setattr(fleet.deep_review, "get_cached_review",
                        lambda key: {"needs_pdf": False, "digest": None})
    assert fleet._usable_cache("A") is None  # a miss -> goes into the to_review batch


def test_needs_pdf_cache_is_reused_without_re_reviewing(monkeypatch):
    """A genuine ``needs_pdf`` cache is USABLE — re-reviewing a paper with no PDF is
    futile, so ``_usable_cache`` returns it and the pick skips the review batch (no
    model load)."""
    entry = {"needs_pdf": True, "digest": None}
    monkeypatch.setattr(fleet.deep_review, "get_cached_review", lambda key: entry)
    assert fleet._usable_cache("A") == entry


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
