"""Deep review (Stage-2 Read-next): per-item DIGEST, single-flight, cache,
detail surfacing, and route registration.

The full reader + Zotero-DB endpoint behavior is covered by the live smoke
(see the plan); these tests pin the novel wiring with stubs.
"""
from __future__ import annotations

import types

import pytest

from zotero_summarizer.services.library import deep_review
from zotero_summarizer.services.zotero import zotero as zotero_svc
from zotero_summarizer.services._common import read_config, settings as _settings


@pytest.fixture(scope="module")
def config():
    return read_config(_settings().config_path)


@pytest.fixture(autouse=True)
def _reset_state(tmp_path, monkeypatch):
    """Isolate the module-global job state + cache file for every test."""
    deep_review._STATE.update(
        {"running": False, "total": 0, "completed": 0, "error": None, "started_at": None}
    )
    monkeypatch.setattr(deep_review, "_cache_path", lambda: tmp_path / "deep_reviews.json")
    yield


class _StubLLM:
    """Returns a PaperDigest. Raises if 'BAD' appears in the prompt (the prompt
    embeds the title), so a per-item failure can be simulated."""

    def pydantic_prompt(self, *, prompt, pydantic_model):
        if "BAD" in prompt:
            raise RuntimeError("LLM blew up on this one")
        return pydantic_model(
            tldr="What it is.", read_decision="read", read_why="useful",
            read_parts=["Methods"], relevance="fits goals", controversies="c",
            impact="i", unknown_unknowns="u", implementation=["step1"],
            grade="A", soundness=5, novelty=4, significance=4,
            reproducibility=3, clarity=4, key_strength="s", key_weakness="w", confidence=0.9,
        )


class _StubExtractor:
    def __init__(self, text="BODY"):
        self._t = text

    def extract_text(self, pdf_path):
        return self._t


class _StubReader:
    def __init__(self, details):
        self._d = details

    def get_item_detail(self, key):
        return self._d.get(key)


def _fake_state(config, *, extractor, reader):
    return types.SimpleNamespace(
        app_state=types.SimpleNamespace(config=config),
        pdf_extractor=extractor,
        unpaywall_client=None,
        zotero_reader=reader,
    )


def _detail(*, title="T", pdf_path="/x/p.pdf", doi="10.1/x", url="", abstract="a"):
    return {
        "title": title, "pdf_path": pdf_path, "doi": doi, "url": url, "abstract": abstract,
        "authors": [], "tags": [], "collections": [], "annotations": [], "notes": [],
        "has_pdf": bool(pdf_path), "publication_date": "2025", "date_added": "",
    }


def _wire(monkeypatch, config, *, reader, extractor, note_fn=None):
    monkeypatch.setattr(deep_review, "get_state", lambda: _fake_state(config, extractor=extractor, reader=reader))
    monkeypatch.setattr(deep_review, "build_triage_llm", lambda model="sota": _StubLLM())
    # The digest is upserted to Zotero inside _review_one; stub it (no real lib).
    monkeypatch.setattr(zotero_svc, "zotero_upsert_digest_note", note_fn or (lambda _ik, _d: None))


def test_run_job_writes_digest_entry(config, monkeypatch):
    reader = _StubReader({"K1": _detail()})
    _wire(monkeypatch, config, reader=reader, extractor=_StubExtractor("BODY"))

    deep_review._run_job([{"item_key": "K1", "title": "T", "gate_relevance": 3.0}], model="x")

    entry = deep_review.get_cached_review("K1")
    assert entry is not None
    assert entry["digest"]["grade"] == "A" and entry["digest"]["basis"] == "full_text"
    assert entry["digest"]["read_decision"] == "read"
    assert entry["digest"]["tldr"] == "What it is."
    assert entry["gate_relevance"] == 3.0
    assert entry["needs_pdf"] is False
    assert entry["zotero_note_written"] is True and entry["zotero_note_error"] is None
    assert entry["reviewed_at"]
    # The confusing relevance re-score is gone.
    assert "fulltext_composite" not in entry and "quality" not in entry
    assert deep_review.status()["status"] == "ready"
    assert deep_review.status()["completed"] == 1


def test_run_job_marks_needs_pdf_without_local_pdf_and_never_fetches(config, monkeypatch):
    reader = _StubReader({"K1": _detail(pdf_path="", url="https://www.biorxiv.org/x")})

    def _boom_extract(pdf_path):
        raise AssertionError("extract_text must not run without a local PDF")

    def _boom_note(_ik, _d):
        raise AssertionError("no note write without a digest")

    _wire(monkeypatch, config, reader=reader,
          extractor=types.SimpleNamespace(extract_text=_boom_extract), note_fn=_boom_note)

    deep_review._run_job([{"item_key": "K1", "title": "T", "gate_relevance": None}], model="x")

    entry = deep_review.get_cached_review("K1")
    assert entry["needs_pdf"] is True
    assert entry["digest"] is None
    assert entry["zotero_note_written"] is False


def test_run_job_records_note_failure_without_dropping_digest(config, monkeypatch):
    reader = _StubReader({"K1": _detail()})

    def _failing_note(_ik, _d):
        raise RuntimeError("Zotero is open")

    _wire(monkeypatch, config, reader=reader, extractor=_StubExtractor("BODY"), note_fn=_failing_note)
    deep_review._run_job([{"item_key": "K1", "title": "T"}], model="x")

    entry = deep_review.get_cached_review("K1")
    assert entry["digest"]["grade"] == "A"  # digest still produced
    assert entry["zotero_note_written"] is False
    assert "Zotero is open" in entry["zotero_note_error"]


def test_run_job_isolates_per_item_failure(config, monkeypatch):
    reader = _StubReader({"GOOD": _detail(title="GOOD"), "BAD": _detail(title="BAD")})
    _wire(monkeypatch, config, reader=reader, extractor=_StubExtractor("BODY"))

    deep_review._run_job(
        [{"item_key": "GOOD", "title": "GOOD"}, {"item_key": "BAD", "title": "BAD"}], model="x",
    )

    assert deep_review.get_cached_review("GOOD") is not None
    assert deep_review.get_cached_review("BAD") is None  # failed item skipped, not masked
    s = deep_review.status()
    assert s["completed"] == 2 and s["status"] == "ready" and s["error"] is None


def test_assess_digest_maps_fields_and_injects_goals(config):
    captured = {}

    class LLM:
        def pydantic_prompt(self, *, prompt, pydantic_model):
            captured["prompt"] = prompt
            return pydantic_model(tldr="t", read_decision="SKIM", grade="b")

    from zotero_summarizer.services.library import quality_review

    d = quality_review.assess_digest(title="My Paper", full_text="BODY TEXT", config=config, llm=LLM())
    assert d.read_decision == "skim" and d.grade == "B" and d.basis == "full_text"
    assert "My Paper" in captured["prompt"] and "BODY TEXT" in captured["prompt"]
    assert "{research_goals}" not in captured["prompt"]  # goals placeholder was filled


def test_build_digest_note_html_marked_and_escaped():
    from zotero_summarizer.models import PaperDigest
    from zotero_summarizer.services.zotero.pending import DIGEST_NOTE_MARKER, build_digest_note_html

    d = PaperDigest(read_decision="read", grade="A", tldr="About <x> & y")
    h = build_digest_note_html(d)
    assert DIGEST_NOTE_MARKER in h
    assert "&lt;x&gt;" in h and "&amp;" in h          # HTML-escaped
    assert "Quality A" in h and "read" in h


def test_start_is_single_flight(monkeypatch):
    deep_review._STATE["running"] = True

    def _should_not_run(**_):
        raise AssertionError("build_reading_queue must not run while a job is in flight")

    monkeypatch.setattr(deep_review.reading_queue, "build_reading_queue", _should_not_run)
    out = deep_review.start(top_k=5)
    assert out["status"] == "running"


def test_start_empty_queue_finishes_without_spawn(monkeypatch):
    monkeypatch.setattr(deep_review.reading_queue, "build_reading_queue", lambda **k: {"items": []})

    def _should_not_spawn(_target):
        raise AssertionError("no background thread should spawn for an empty queue")

    monkeypatch.setattr(deep_review, "run_in_background", _should_not_spawn)
    out = deep_review.start(top_k=5)
    assert out["total"] == 0
    assert deep_review._STATE["running"] is False


def test_start_with_item_keys_skips_queue(monkeypatch):
    """The per-paper 'Run deeper разбор' button: an explicit item_keys run must
    review exactly those papers (not the top-K queue), pulling gate_relevance
    from the score cache."""
    def _should_not_run(**_):
        raise AssertionError("build_reading_queue must not run for an explicit item_keys run")

    monkeypatch.setattr(deep_review.reading_queue, "build_reading_queue", _should_not_run)
    monkeypatch.setattr(
        deep_review.reading_queue, "get_cached_scoring",
        lambda key: {"composite_score": 4.1} if key == "K1" else None,
    )
    captured = {}
    monkeypatch.setattr(deep_review, "_run_job", lambda items, *, model: captured.update(items=items, model=model))
    monkeypatch.setattr(deep_review, "run_in_background", lambda target: target())

    out = deep_review.start(item_keys=["K1"])
    assert out["total"] == 1
    assert captured["items"] == [{"item_key": "K1", "title": "", "gate_relevance": 4.1}]


def test_build_library_detail_surfaces_deep_review(monkeypatch):
    from zotero_summarizer.services.library import reading_queue, review_detail

    entry = {
        "digest": {"grade": "B", "read_decision": "skim", "tldr": "x", "basis": "full_text"},
        "needs_pdf": False, "gate_relevance": 3.0, "reviewed_at": "2026-05-23T00:00:00Z",
        "zotero_note_written": True, "zotero_note_error": None,
    }
    monkeypatch.setattr(deep_review, "get_cached_review", lambda key: entry if key == "K1" else None)
    monkeypatch.setattr(reading_queue, "get_cached_scoring", lambda key: None)
    monkeypatch.setattr(reading_queue, "live_scoring", lambda item: None)

    payload = review_detail.build_library_detail(_StubReader({"K1": _detail()}), "K1")
    assert payload["deep_review"] == entry
    assert payload["source"] == review_detail.SOURCE_LIBRARY


def test_deep_review_routes_registered():
    from zotero_summarizer.api.app import create_app

    paths = {getattr(route, "path", "") for route in create_app().routes}
    assert "/api/library/deep-review/run" in paths
    assert "/api/library/deep-review/status" in paths
    assert "/api/library/reject-tag" in paths
