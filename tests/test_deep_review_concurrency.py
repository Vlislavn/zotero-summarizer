"""Deep-review concurrency: per-item jobs over one provider-aware pool.

The behaviour the user asked for — "run both at once for an API, queue for a local
model" — plus the guardrails: per-item single-flight (the SAME paper isn't reviewed
twice), per-item status isolation (each panel sees its OWN paper), and a locked cache
merge (two concurrent workers never clobber each other's entry).

The real review pipeline is stubbed (``_review_one`` → a controllable fake that blocks
on an Event), so these tests exercise the registry + pool + cache plumbing only,
deterministically, with no LLM / model load.
"""
from __future__ import annotations

import threading
import time
import types

import pytest

from zotero_summarizer.services.library import deep_review


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Fresh job registry + pool + cache file per test; shut the pool down after."""
    with deep_review._LOCK:
        deep_review._JOBS.clear()
    monkeypatch.setattr(deep_review, "_cache_path", lambda: tmp_path / "deep_reviews.json")
    monkeypatch.setattr(deep_review, "_POOL", None)
    monkeypatch.setattr(deep_review, "_POOL_SIZE", 0)
    monkeypatch.setattr(deep_review, "_try_rebuild_render", lambda _k: None)
    yield
    pool = deep_review._POOL
    if pool is not None:
        pool.shutdown(wait=False)


def _wire(monkeypatch, *, is_local, max_sub=None):
    """Stub the run ctx so only the provider's locality/cap matters (the fake
    ``_review_one`` ignores the rest)."""
    provider = types.SimpleNamespace(is_local=is_local, max_sub_concurrency=max_sub, lean_deep_review=False)
    monkeypatch.setattr(deep_review, "_build_ctx", lambda: {"_provider": provider})
    monkeypatch.setattr(deep_review.reading_queue, "get_cached_scoring", lambda _key: None)


def _entry(grade="A"):
    return {"digest": {"grade": grade}, "needs_pdf": False, "reviewed_at": "t"}


def _wait_ready(item_key, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if deep_review.status(item_key)["status"] in ("ready", "error"):
            return
        time.sleep(0.01)
    raise AssertionError(f"{item_key} did not finish in {timeout}s: {deep_review.status(item_key)}")


def test_remote_runs_two_papers_concurrently_with_isolated_status(monkeypatch):
    """A remote provider (pool ≥2) reviews a 2nd paper WHILE the 1st runs; each paper's
    status is independent; the locked cache merge keeps BOTH entries."""
    _wire(monkeypatch, is_local=False, max_sub=2)
    started = {"A": threading.Event(), "B": threading.Event()}
    release = threading.Event()

    def _fake_review_one(item, **_kw):
        started[item["item_key"]].set()
        assert release.wait(timeout=5)  # hold both workers mid-review
        return _entry()

    monkeypatch.setattr(deep_review, "_review_one", _fake_review_one)

    deep_review.start(item_keys=["A"])
    deep_review.start(item_keys=["B"])  # NOT blocked by A
    assert started["A"].wait(timeout=5) and started["B"].wait(timeout=5)  # both in flight at once

    assert deep_review.status("A")["status"] == "running"
    assert deep_review.status("B")["status"] == "running"
    assert deep_review.status()["status"] == "running"  # aggregate: a review IS running

    release.set()
    _wait_ready("A")
    _wait_ready("B")
    assert deep_review.get_cached_review("A")["digest"]["grade"] == "A"
    assert deep_review.get_cached_review("B")["digest"]["grade"] == "A"  # neither write lost


def test_local_queues_the_second_paper(monkeypatch):
    """A local provider (pool size 1) serialises: the 2nd paper is accepted but QUEUES —
    it only runs after the 1st finishes (one on-device model at a time, RAM-safe)."""
    _wire(monkeypatch, is_local=True)
    order: list[str] = []
    a_started = threading.Event()
    a_release = threading.Event()

    def _fake_review_one(item, **_kw):
        key = item["item_key"]
        order.append(key)
        if key == "A":
            a_started.set()
            assert a_release.wait(timeout=5)
        return _entry()

    monkeypatch.setattr(deep_review, "_review_one", _fake_review_one)

    deep_review.start(item_keys=["A"])
    assert a_started.wait(timeout=5)
    deep_review.start(item_keys=["B"])  # queued behind A on the size-1 pool

    time.sleep(0.1)
    assert order == ["A"]  # B has NOT started executing while A holds the worker

    a_release.set()
    _wait_ready("A")
    _wait_ready("B")
    assert order == ["A", "B"]  # B ran strictly AFTER A


def test_same_paper_twice_reviews_once(monkeypatch):
    """Per-item single-flight: clicking 'Run deeper review' twice on the SAME paper while
    it's running is a no-op (one review, not two racing writes)."""
    _wire(monkeypatch, is_local=False, max_sub=2)
    calls: list[str] = []
    started = threading.Event()
    release = threading.Event()

    def _fake_review_one(item, **_kw):
        calls.append(item["item_key"])
        started.set()
        assert release.wait(timeout=5)
        return _entry()

    monkeypatch.setattr(deep_review, "_review_one", _fake_review_one)

    deep_review.start(item_keys=["A"])
    assert started.wait(timeout=5)
    deep_review.start(item_keys=["A"])  # already running → ignored
    release.set()
    _wait_ready("A")
    assert calls == ["A"]  # reviewed exactly once


def test_status_unknown_item_is_idle(monkeypatch):
    """A paper with no tracked job reports ``idle`` (so a panel for a never-reviewed
    paper doesn't pick up someone else's progress)."""
    _wire(monkeypatch, is_local=False, max_sub=2)
    assert deep_review.status("NEVER")["status"] == "idle"
    assert deep_review.status()["status"] == "idle"  # aggregate with no jobs
