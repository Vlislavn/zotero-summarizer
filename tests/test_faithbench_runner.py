"""faithbench run: resume-by-key, manifest guard, exception rows + retry,
abstention parsing, and the claims trial (digest + cached decomposition)."""
from __future__ import annotations

import json
import types

import pytest

from zotero_summarizer.models import PaperDigest
from zotero_summarizer.services.faithbench._corpus import freeze_paper_text
from zotero_summarizer.services.faithbench._dataset import (
    BenchmarkMeta,
    PaperManifestEntry,
    QAItem,
    TrapItem,
)
from zotero_summarizer.services.faithbench._runner import (
    RunPaths,
    answer_with_retry,
    done_keys,
    latest_by_key,
    load_jsonl,
    parse_answer,
    run_benchmark,
    write_or_check_manifest,
)

# Long enough that the retrieval condition (top-6 of ~1200-char chunks) selects
# a strict subset of the text — a paper shorter than one chunk would make the
# full_text and retrieval prompts legitimately identical.
PAPER_TEXT = (
    "We evaluated GlassNet on the ImageNet dataset. "
    "Training used 1,281,167 images over 90 epochs. "
    "The top-1 accuracy reached 85.3 percent. "
    + " ".join(f"Filler sentence number {i} about an unrelated background topic." for i in range(400))
)


def _fake_config():
    return types.SimpleNamespace(
        quality_review=types.SimpleNamespace(max_text_chars=60_000),
        prompts=types.SimpleNamespace(paper_digest=None),
        research_goals=["clinical agentic AI"],
    )


def _benchmark(tmp_path):
    papers_dir = tmp_path / "papers"
    sha = freeze_paper_text(papers_dir, "P1", PAPER_TEXT)
    meta = BenchmarkMeta(
        version=1, created_at="t", builder_model="B",
        papers=[PaperManifestEntry(item_key="P1", title="Paper P1",
                                   text_sha256=sha, n_chars=len(PAPER_TEXT))],
    )
    items = [
        QAItem(item_id="qa:P1:0", paper_item_key="P1", paper_title="Paper P1",
               paper_text_sha256=sha, question="Which dataset was used?",
               gold_answer="ImageNet", span_start=25, span_end=33, answer_type="entity"),
        TrapItem(item_id="trap:P1:0", paper_item_key="P1", paper_title="Paper P1",
                 paper_text_sha256=sha, question="What was the cohort size?",
                 source_paper_item_key="P2", source_gold_answer="412 patients"),
    ]
    return meta, items, papers_dir


class FakeAnswerer:
    """Answers with JSON; raises when the prompt contains a poison marker."""

    def __init__(self, *, poison: str | None = None):
        self.poison = poison
        self.prompts: list[str] = []

    def prompt(self, prompt, **kwargs):
        self.prompts.append(prompt)
        if self.poison and self.poison in prompt:
            raise RuntimeError("backend fell over")
        if "cohort size" in prompt:
            return json.dumps({"answer": None, "quote": None})
        return json.dumps({"answer": "ImageNet", "quote": "We evaluated GlassNet on the ImageNet dataset."})


def test_run_writes_rows_for_both_conditions_and_records_latency(tmp_path):
    meta, items, papers_dir = _benchmark(tmp_path)
    paths = RunPaths(run_dir=tmp_path / "runs" / "r1")
    paths.run_dir.mkdir(parents=True)
    counts = run_benchmark(
        run_id="r1", meta=meta, items=items, papers_dir=papers_dir, paths=paths,
        llm=FakeAnswerer(), config=_fake_config(), decompose_llm=None,
        conditions=("full_text", "retrieval"), tracks=("qa",), runs=1,
    )
    rows = load_jsonl(paths.responses)
    assert counts == {"executed": 4, "skipped": 0, "failed": 0}
    assert {(r["item_id"], r["condition"]) for r in rows} == {
        ("qa:P1:0", "full_text"), ("qa:P1:0", "retrieval"),
        ("trap:P1:0", "full_text"), ("trap:P1:0", "retrieval"),
    }
    assert all(r["latency_seconds"] is not None for r in rows)
    trap_rows = [r for r in rows if r["item_id"] == "trap:P1:0"]
    assert all(r["parsed"]["abstained"] for r in trap_rows)
    # full_text and retrieval build different prompts
    qa_rows = {r["condition"]: r for r in rows if r["item_id"] == "qa:P1:0"}
    assert qa_rows["full_text"]["prompt_sha256"] != qa_rows["retrieval"]["prompt_sha256"]


def test_resume_skips_done_keys_and_manifest_guard_refuses_mismatch(tmp_path):
    meta, items, papers_dir = _benchmark(tmp_path)
    paths = RunPaths(run_dir=tmp_path / "runs" / "r1")
    paths.run_dir.mkdir(parents=True)
    base = dict(meta=meta, items=items, papers_dir=papers_dir, paths=paths,
                llm=FakeAnswerer(), config=_fake_config(), decompose_llm=None,
                conditions=("full_text",), tracks=("qa",), runs=1)
    first = run_benchmark(run_id="r1", **base)
    assert first["executed"] == 2
    second = run_benchmark(run_id="r1", **base)
    assert second["executed"] == 0 and second["skipped"] == 2

    manifest = {"run_id": "r1", "model": "m1", "benchmark_sha256": "s",
                "conditions": ["full_text"], "tracks": ["qa"], "runs": 1}
    write_or_check_manifest(paths, manifest)
    write_or_check_manifest(paths, dict(manifest))  # identical resume is fine
    with pytest.raises(RuntimeError, match="resume refused"):
        write_or_check_manifest(paths, {**manifest, "model": "OTHER"})


def test_exception_rows_recorded_and_retry_errors_reattempts(tmp_path):
    meta, items, papers_dir = _benchmark(tmp_path)
    paths = RunPaths(run_dir=tmp_path / "runs" / "r1")
    paths.run_dir.mkdir(parents=True)
    base = dict(run_id="r1", meta=meta, items=items, papers_dir=papers_dir, paths=paths,
                config=_fake_config(), decompose_llm=None,
                conditions=("full_text",), tracks=("qa",), runs=1)

    counts = run_benchmark(llm=FakeAnswerer(poison="Which dataset"), **base)
    assert counts["failed"] == 1
    latest = latest_by_key(load_jsonl(paths.responses))
    failed = latest[("qa:P1:0", "full_text", 1)]
    assert failed["status"] == "exception" and "backend fell over" in failed["error"]

    # without --retry-errors the failed key is considered done
    assert ("qa:P1:0", "full_text", 1) in done_keys(load_jsonl(paths.responses), retry_errors=False)
    # with --retry-errors it is re-attempted and the LAST row wins
    counts2 = run_benchmark(llm=FakeAnswerer(), retry_errors=True, **base)
    assert counts2["executed"] == 1 and counts2["failed"] == 0
    healed = latest_by_key(load_jsonl(paths.responses))[("qa:P1:0", "full_text", 1)]
    assert healed["status"] == "ok" and healed["parsed"]["answer"] == "ImageNet"


def test_run_refuses_frozen_text_drift(tmp_path):
    meta, items, papers_dir = _benchmark(tmp_path)
    drifted = meta.model_copy(update={"papers": [
        meta.papers[0].model_copy(update={"text_sha256": "0" * 64})
    ]})
    paths = RunPaths(run_dir=tmp_path / "runs" / "r1")
    paths.run_dir.mkdir(parents=True)
    with pytest.raises(ValueError, match="frozen text drift"):
        run_benchmark(run_id="r1", meta=drifted, items=items, papers_dir=papers_dir,
                      paths=paths, llm=FakeAnswerer(), config=_fake_config(),
                      decompose_llm=None, conditions=("full_text",), tracks=("qa",))


# ---------------------------------------------------------------------------
# Answer parsing
# ---------------------------------------------------------------------------


def test_parse_answer_abstention_and_retry():
    assert parse_answer('{"answer": null, "quote": null}') == {
        "answer": None, "abstained": True, "quote": None,
    }
    # the literal string "null"/"none"/"n/a" is an abstention spelling, not an answer
    for spelled in ('"null"', '"NONE"', '"n/a"'):
        assert parse_answer(f'{{"answer": {spelled}, "quote": null}}')["abstained"] is True
    assert parse_answer('{"answer": "Null Island", "quote": null}')["abstained"] is False
    parsed = parse_answer('noise before {"answer": "85.3", "quote": "q"} noise after')
    assert parsed["answer"] == "85.3" and parsed["abstained"] is False

    class GarbageThenJson:
        def __init__(self):
            self.calls = 0

        def prompt(self, prompt, **kwargs):
            self.calls += 1
            return "no json at all" if self.calls == 1 else '{"answer": "x", "quote": null}'

    llm = GarbageThenJson()
    parsed, _raw = answer_with_retry(llm, "question prompt")
    assert parsed["answer"] == "x" and llm.calls == 2


# ---------------------------------------------------------------------------
# Claims track
# ---------------------------------------------------------------------------


class FakeDigestModel:
    def pydantic_prompt(self, *, prompt, pydantic_model):
        assert pydantic_model is PaperDigest
        return PaperDigest(
            tldr="GlassNet reaches 85.3 percent top-1 on ImageNet.",
            read_decision="read", read_why="strong result", grade="A",
            key_strength="large training set", key_weakness="no ablations",
        )


class FakeDecomposer:
    def __init__(self):
        self.calls = 0

    def prompt(self, prompt, **kwargs):
        self.calls += 1
        return json.dumps({"claims": [
            {"field": "tldr", "claim": "GlassNet reaches 85.3 percent top-1 accuracy on ImageNet."},
            {"field": "key_weakness", "claim": "The paper reports no ablations."},
        ]})


def test_claims_trial_writes_claims_and_caches_decomposition(tmp_path):
    meta, items, papers_dir = _benchmark(tmp_path)
    paths = RunPaths(run_dir=tmp_path / "runs" / "r1")
    paths.run_dir.mkdir(parents=True)
    decomposer = FakeDecomposer()
    counts = run_benchmark(
        run_id="r1", meta=meta, items=items, papers_dir=papers_dir, paths=paths,
        llm=FakeDigestModel(), config=_fake_config(), decompose_llm=decomposer,
        conditions=("full_text",), tracks=("claims",), runs=2,
    )
    assert counts["executed"] == 2  # one digest trial per run_number
    rows = load_jsonl(paths.responses)
    assert all(r["track"] == "claims" and len(r["parsed"]["claims"]) == 2 for r in rows)
    # identical digest both runs -> decomposition cache hit on the second
    assert decomposer.calls == 1


def test_claims_track_requires_decompose_llm(tmp_path):
    meta, items, papers_dir = _benchmark(tmp_path)
    paths = RunPaths(run_dir=tmp_path / "runs" / "r1")
    with pytest.raises(ValueError, match="decompose_llm"):
        run_benchmark(run_id="r1", meta=meta, items=items, papers_dir=papers_dir,
                      paths=paths, llm=FakeDigestModel(), config=_fake_config(),
                      decompose_llm=None, tracks=("claims",))
