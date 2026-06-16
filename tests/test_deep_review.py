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
        {"running": False, "total": 0, "completed": 0, "error": None, "started_at": None, "progress": {}}
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
        # The deep_review stage now resolves its client via the runtime state.
        resolve_stage_client=lambda stage, **_k: _StubLLM(),
        # Provider drives concurrency (is_local → serial) AND the deep-review tier
        # (lean_deep_review → cheap tier). A local+lean stub keeps the job
        # single-threaded, deterministic, and on the lean tier in tests.
        resolve_stage_provider=lambda stage: types.SimpleNamespace(is_local=True, lean_deep_review=True),
    )


def _detail(*, title="T", pdf_path="/x/p.pdf", doi="10.1/x", url="", abstract="a"):
    return {
        "title": title, "pdf_path": pdf_path, "doi": doi, "url": url, "abstract": abstract,
        "authors": [], "tags": [], "collections": [], "annotations": [], "notes": [],
        "has_pdf": bool(pdf_path), "publication_date": "2025", "date_added": "",
    }


def _wire(monkeypatch, config, *, reader, extractor, note_fn=None):
    monkeypatch.setattr(deep_review, "get_state", lambda: _fake_state(config, extractor=extractor, reader=reader))
    # The digest is upserted to Zotero inside _review_one; stub it (no real lib).
    monkeypatch.setattr(zotero_svc, "zotero_upsert_digest_note", note_fn or (lambda _ik, _d: None))
    # Keep ORCHESTRATION tests hermetic: stub the heavy enrichment layers (real
    # quality-eval + goal-summaries pull a local LLM + a 1.3GB embedder and have
    # their own unit tests). This test asserts the orchestrator ATTACHES them.
    def _stub_layers(ctx):
        goals = list(getattr(ctx.config, "research_goals", []) or [])
        return {"quality_band": "neutral"}, [{"goal": g, "retrieval_state": "miss"} for g in goals]
    monkeypatch.setattr(deep_review, "_extra_layers", _stub_layers)


def test_status_exposes_progress_field():
    """The polled status carries a `progress` dict (live phase + sub-step) so the
    UI can show what a running review is doing; {} when idle."""
    s = deep_review.status()
    assert "progress" in s and s["progress"] == {}


def test_run_job_clears_progress_when_done(config, monkeypatch):
    """A finished run resets progress to {} so the next poll doesn't show a stale
    phase from the last review."""
    reader = _StubReader({"K1": _detail()})
    _wire(monkeypatch, config, reader=reader, extractor=_StubExtractor("BODY"))
    deep_review._run_job([{"item_key": "K1", "title": "T"}])
    assert deep_review.status()["progress"] == {}


def test_run_job_writes_digest_entry(config, monkeypatch):
    reader = _StubReader({"K1": _detail()})
    _wire(monkeypatch, config, reader=reader, extractor=_StubExtractor("BODY"))

    deep_review._run_job([{"item_key": "K1", "title": "T", "gate_relevance": 3.0}])

    entry = deep_review.get_cached_review("K1")
    assert entry is not None
    assert entry["digest"]["grade"] == "A" and entry["digest"]["basis"] == "full_text"
    assert entry["digest"]["read_decision"] == "read"
    assert entry["digest"]["tldr"] == "What it is."
    assert entry["gate_relevance"] == 3.0
    assert entry["needs_pdf"] is False
    assert entry["zotero_note_written"] is True and entry["zotero_note_error"] is None
    assert entry["reviewed_at"]
    # The confusing relevance re-score is gone; the goal-aligned layers are attached
    # (quality verdict + per-goal board), each independently-skippable.
    assert "fulltext_composite" not in entry
    assert "quality" in entry and "goal_summaries" in entry
    assert isinstance(entry["goal_summaries"], list)
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

    deep_review._run_job([{"item_key": "K1", "title": "T", "gate_relevance": None}])

    entry = deep_review.get_cached_review("K1")
    assert entry["needs_pdf"] is True
    assert entry["digest"] is None
    assert entry["zotero_note_written"] is False


def test_run_job_records_note_failure_without_dropping_digest(config, monkeypatch):
    reader = _StubReader({"K1": _detail()})

    def _failing_note(_ik, _d):
        raise RuntimeError("Zotero is open")

    _wire(monkeypatch, config, reader=reader, extractor=_StubExtractor("BODY"), note_fn=_failing_note)
    deep_review._run_job([{"item_key": "K1", "title": "T"}])

    entry = deep_review.get_cached_review("K1")
    assert entry["digest"]["grade"] == "A"  # digest still produced
    assert entry["zotero_note_written"] is False
    assert "Zotero is open" in entry["zotero_note_error"]


def test_run_job_isolates_per_item_failure(config, monkeypatch):
    reader = _StubReader({"GOOD": _detail(title="GOOD"), "BAD": _detail(title="BAD")})
    _wire(monkeypatch, config, reader=reader, extractor=_StubExtractor("BODY"))

    deep_review._run_job(
        [{"item_key": "GOOD", "title": "GOOD"}, {"item_key": "BAD", "title": "BAD"}],
    )

    assert deep_review.get_cached_review("GOOD") is not None
    assert deep_review.get_cached_review("BAD") is None  # failed item skipped, not masked
    s = deep_review.status()
    assert s["completed"] == 2 and s["status"] == "ready" and s["error"] is None


def test_run_job_all_items_failed_surfaces_job_error(config, monkeypatch):
    """When EVERY item raises (e.g. the deep_review LLM endpoint is unreachable),
    the job must surface a job-level error — not report a clean 'ready' with an
    empty cache, which silently hid the unreachable-MLX failure in the UI.

    Regression for the 2026-06-14 'Run deeper review does nothing' report: the
    digest LLM pointed at a dead endpoint, every item failed, yet status stayed
    'ready' / error=None so DeepReviewSection rendered nothing."""
    reader = _StubReader({"BAD": _detail(title="BAD")})
    _wire(monkeypatch, config, reader=reader, extractor=_StubExtractor("BODY"))
    # Give the resolved provider a base_url so the message names the dead endpoint.
    monkeypatch.setattr(
        deep_review, "get_state",
        lambda: types.SimpleNamespace(
            app_state=types.SimpleNamespace(config=config),
            pdf_extractor=_StubExtractor("BODY"), unpaywall_client=None, zotero_reader=reader,
            resolve_stage_client=lambda stage, **_k: _StubLLM(),
            resolve_stage_provider=lambda stage: types.SimpleNamespace(
                is_local=True, base_url="http://127.0.0.1:8080/v1"
            ),
        ),
    )

    deep_review._run_job([{"item_key": "BAD", "title": "BAD"}])

    assert deep_review.get_cached_review("BAD") is None  # nothing cached
    s = deep_review.status()
    assert s["status"] == "error"
    assert s["completed"] == 1
    assert "LLM blew up" in s["error"]            # the per-item cause is surfaced
    assert "127.0.0.1:8080" in s["error"]         # the dead endpoint is named


def test_lean_tier_uses_lean_max_text_chars(config, monkeypatch):
    """A provider flagged lean_deep_review feeds assess_digest the cheaper
    lean_max_text_chars; a non-lean provider feeds the full max_text_chars
    (the tier-aware speedup, keyed on provider.lean_deep_review)."""
    reader = _StubReader({"K1": _detail()})
    _wire(monkeypatch, config, reader=reader, extractor=_StubExtractor("BODY TEXT"))
    seen: list[int | None] = []
    real = deep_review.quality_review.assess_digest
    monkeypatch.setattr(
        deep_review.quality_review, "assess_digest",
        lambda **kw: (seen.append(kw.get("max_chars")), real(**kw))[1],
    )
    # _wire's fake provider is lean_deep_review=True → lean tier.
    deep_review._run_job([{"item_key": "K1", "title": "T"}])
    assert seen[-1] == config.quality_review.lean_max_text_chars

    # A non-lean provider → full max_text_chars.
    monkeypatch.setattr(deep_review, "get_state", lambda: types.SimpleNamespace(
        app_state=types.SimpleNamespace(config=config), pdf_extractor=_StubExtractor("BODY TEXT"),
        unpaywall_client=None, zotero_reader=reader,
        resolve_stage_client=lambda s, **_k: _StubLLM(),
        resolve_stage_provider=lambda s: types.SimpleNamespace(is_local=False, lean_deep_review=False),
    ))
    deep_review._run_job([{"item_key": "K1", "title": "T"}])
    assert seen[-1] == config.quality_review.max_text_chars


def test_mlx_shape_loopback_but_not_lean_uses_full_tier(config, monkeypatch):
    """Regression (2026-06-15 /verify finding): a loopback-but-not-lean provider —
    the MLX shape `is_local=True, lean_deep_review=False` — must get the FULL tier:
    full max_text_chars on the digest, the full self_consistency_runs, and per-goal
    summaries (NOT batched). Before the fix the tier keyed on is_local, so MLX (on
    127.0.0.1:8080) wrongly ran the lean tier and silently degraded the digest."""
    from zotero_summarizer.services.library import _paper_goal_summaries, quality_eval

    reader = _StubReader({"K1": _detail()})
    # NOT _wire: we want the real _extra_layers so we can capture the tier values it
    # forwards to evaluate_quality + summarize_for_goals.
    monkeypatch.setattr(deep_review, "get_state", lambda: types.SimpleNamespace(
        app_state=types.SimpleNamespace(config=config), pdf_extractor=_StubExtractor("BODY TEXT"),
        unpaywall_client=None, zotero_reader=reader,
        resolve_stage_client=lambda s, **_k: _StubLLM(),
        resolve_stage_provider=lambda s: types.SimpleNamespace(is_local=True, lean_deep_review=False),
    ))
    monkeypatch.setattr(zotero_svc, "zotero_upsert_digest_note", lambda _ik, _d: None)

    seen_digest: list[int | None] = []
    real = deep_review.quality_review.assess_digest
    monkeypatch.setattr(
        deep_review.quality_review, "assess_digest",
        lambda **kw: (seen_digest.append(kw.get("max_chars")), real(**kw))[1],
    )
    captured = {}
    monkeypatch.setattr(
        quality_eval, "evaluate_quality",
        lambda **kw: captured.update(runs=kw.get("self_consistency_runs"), eval_max=kw.get("max_chars"))
        or types.SimpleNamespace(model_dump=lambda: {"quality_band": "neutral"}),
    )
    monkeypatch.setattr(
        _paper_goal_summaries, "summarize_for_goals",
        lambda **kw: captured.update(batch=kw.get("batch")) or [],
    )

    deep_review._run_job([{"item_key": "K1", "title": "T"}])

    assert seen_digest[-1] == config.quality_review.max_text_chars          # full digest cap, not lean
    assert captured["runs"] == config.quality_review.self_consistency_runs  # full rubric runs (3), not 1
    assert captured["eval_max"] == config.quality_review.max_text_chars     # full eval cap
    assert captured["batch"] is False                                       # per-goal, NOT batched


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
    """The per-paper 'Run deeper review' button: an explicit item_keys run must
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
    monkeypatch.setattr(deep_review, "_run_job", lambda items, **_kw: captured.update(items=items))
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
