"""faithbench run: resume-by-key, manifest guard, exception rows + retry,
abstention parsing, and the claims trial (digest + cached decomposition)."""
from __future__ import annotations

import csv
import json

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
    RunInputs,
    RunOptions,
    RunPaths,
    answer_with_retry,
    done_keys,
    latest_by_key,
    load_jsonl,
    parse_answer,
    run_benchmark,
    write_or_check_manifest,
    generation_identity,
)


@pytest.fixture(autouse=True)
def _isolate_digest_generation(monkeypatch):
    from zotero_summarizer.services.library import _digest_verification
    monkeypatch.setattr(_digest_verification, "verify_digest", lambda *args, **kwargs: None)

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
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config

    config = _default_goals_config()
    config.research_goals = ["clinical agentic AI"]
    config.quality_review.max_text_chars = 60_000
    return config


def _generation_models(claims=False):
    from zotero_summarizer.models.providers import resolve_stage

    model = resolve_stage(_fake_config().llm_routing, "deep_review").model_copy(update={"model": "m1"})
    return model, model.model_copy(update={"model": "decomposer"}) if claims else None


def _approve_benchmark(path, items):
    from zotero_summarizer.services.faithbench._dataset import (
        export_review_csv, require_review_approval, review_path,
    )

    csv_path = review_path(path)
    export_review_csv(csv_path, items, {})
    with csv_path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows([{**row, "approved": "yes"} for row in rows])
    return require_review_approval(path, items)


def _prepare_run(inputs, options, *, run_id="r1", config=None, models=None):
    """Explicit test artifact setup; the production runner never invents a manifest."""
    import hashlib
    from zotero_summarizer.services.faithbench._dataset import _benchmark_sha, save_benchmark

    config = config or _fake_config()
    models = models or _generation_models("claims" in options.tracks)
    path = inputs.paths.run_dir / "benchmark.jsonl"
    save_benchmark(path, inputs.meta, inputs.items)
    review_sha = _approve_benchmark(path, inputs.items) if "qa" in options.tracks else None
    manifest = {
        "run_id": run_id, "model": models[0].model, "provider_name": models[0].provider.name,
        "base_url": models[0].provider.base_url, "benchmark_path": str(path),
        "benchmark_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "benchmark_content_sha256": _benchmark_sha(inputs.meta, inputs.items),
        "benchmark_review_sha256": review_sha,
        "generation_sha256": generation_identity(config, models),
        "research_goals": list(config.research_goals), "conditions": list(options.conditions),
        "tracks": list(options.tracks), "runs": options.runs, "limit": options.limit,
        "serial": options.serial, "max_workers": options.max_workers,
    }
    write_or_check_manifest(inputs.paths, manifest)
    return manifest


def _benchmark(tmp_path):
    papers_dir = tmp_path / "papers"
    sha = freeze_paper_text(papers_dir, "P1", PAPER_TEXT)
    source_text = "This study enrolled 412 patients."
    source_sha = freeze_paper_text(papers_dir, "P2", source_text)
    meta = BenchmarkMeta(
        version=1, created_at="t", builder_model="B",
        papers=[PaperManifestEntry(item_key="P1", title="Paper P1",
                                   text_sha256=sha, n_chars=len(PAPER_TEXT)),
                PaperManifestEntry(item_key="P2", title="Source P2", text_sha256=source_sha,
                                   n_chars=len(source_text))],
    )
    items = [
        QAItem(item_id="qa:P1:0", paper_item_key="P1", paper_title="Paper P1",
               paper_text_sha256=sha, question="Which dataset was used?",
               gold_answer="ImageNet", span_start=PAPER_TEXT.index("ImageNet"),
               span_end=PAPER_TEXT.index("ImageNet") + len("ImageNet"), answer_type="entity"),
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
    _prepare_run(RunInputs(meta, items, papers_dir, paths), RunOptions(tracks=("qa",)))
    counts = run_benchmark(
        run_id="r1", inputs=RunInputs(meta=meta, items=items, papers_dir=papers_dir, paths=paths),
        llm=FakeAnswerer(), config=_fake_config(), decompose_llm=None, generation_models=_generation_models(),
        options=RunOptions(conditions=("full_text", "retrieval"), tracks=("qa",), runs=1),
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
    base = dict(inputs=RunInputs(meta=meta, items=items, papers_dir=papers_dir, paths=paths),
                llm=FakeAnswerer(), config=_fake_config(), decompose_llm=None, generation_models=_generation_models(),
                options=RunOptions(conditions=("full_text",), tracks=("qa",), runs=1))
    manifest = _prepare_run(base["inputs"], base["options"])
    first = run_benchmark(run_id="r1", **base)
    assert first["executed"] == 2
    second = run_benchmark(run_id="r1", **base)
    assert second["executed"] == 0 and second["skipped"] == 2

    write_or_check_manifest(paths, dict(manifest))  # identical resume is fine
    with pytest.raises(RuntimeError, match="resume refused"):
        write_or_check_manifest(paths, {**manifest, "model": "OTHER"})


def test_exception_rows_recorded_and_retry_errors_reattempts(tmp_path):
    meta, items, papers_dir = _benchmark(tmp_path)
    paths = RunPaths(run_dir=tmp_path / "runs" / "r1")
    paths.run_dir.mkdir(parents=True)
    base = dict(run_id="r1", inputs=RunInputs(meta=meta, items=items, papers_dir=papers_dir, paths=paths),
                config=_fake_config(), decompose_llm=None, generation_models=_generation_models())
    options = RunOptions(conditions=("full_text",), tracks=("qa",), runs=1)
    _prepare_run(base["inputs"], options)

    counts = run_benchmark(llm=FakeAnswerer(poison="Which dataset"), options=options, **base)
    assert counts["failed"] == 1
    latest = latest_by_key(load_jsonl(paths.responses))
    failed = latest[("qa:P1:0", "full_text", 1)]
    assert failed["status"] == "exception" and "backend fell over" in failed["error"]

    # without --retry-errors the failed key is considered done
    assert ("qa:P1:0", "full_text", 1) in done_keys(load_jsonl(paths.responses), retry_errors=False)
    # with --retry-errors it is re-attempted and the LAST row wins
    retry_options = RunOptions(conditions=("full_text",), tracks=("qa",), runs=1, retry_errors=True)
    counts2 = run_benchmark(llm=FakeAnswerer(), options=retry_options, **base)
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
    # A changed manifest digest now addresses a different (missing) text version.
    _prepare_run(RunInputs(drifted, items, papers_dir, paths), RunOptions(conditions=("full_text",), tracks=("qa",)))
    with pytest.raises(FileNotFoundError):
        run_benchmark(run_id="r1",
                      inputs=RunInputs(meta=drifted, items=items, papers_dir=papers_dir, paths=paths),
                      llm=FakeAnswerer(), config=_fake_config(),
                      decompose_llm=None, generation_models=_generation_models(),
                      options=RunOptions(conditions=("full_text",), tracks=("qa",)))


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
            read_parts=["§4 Results"], skip_parts=[], estimated_read_minutes=15,
            original_value="The complete experimental evidence.",
            key_strength="large training set", key_weakness="no ablations",
            writing_friction="low", writing_reasons=[],
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
    # This test measures one paper's repeated digest, not the QA/trap source cohort.
    meta, items = meta.model_copy(update={"papers": meta.papers[:1]}), []
    paths = RunPaths(run_dir=tmp_path / "runs" / "r1")
    paths.run_dir.mkdir(parents=True)
    decomposer = FakeDecomposer()
    _prepare_run(RunInputs(meta, items, papers_dir, paths), RunOptions(conditions=("full_text",), tracks=("claims",), runs=2))
    counts = run_benchmark(
        run_id="r1", inputs=RunInputs(meta=meta, items=items, papers_dir=papers_dir, paths=paths),
        llm=FakeDigestModel(), config=_fake_config(), decompose_llm=decomposer, generation_models=_generation_models(True),
        options=RunOptions(conditions=("full_text",), tracks=("claims",), runs=2),
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
        run_benchmark(run_id="r1",
                      inputs=RunInputs(meta=meta, items=items, papers_dir=papers_dir, paths=paths),
                      llm=FakeDigestModel(), config=_fake_config(),
                      decompose_llm=None, generation_models=_generation_models(), options=RunOptions(tracks=("claims",)))


@pytest.mark.parametrize("ledger", ["responses", "judgments"])
@pytest.mark.parametrize("tail", [b'{"item_id": "unfinished', b'{"text": "\xc3', b"complete_without_newline"])
def test_resume_recovers_only_unterminated_tail(tmp_path, ledger, tail):
    from zotero_summarizer.services.faithbench._judge import judge_run

    meta, items, papers_dir = _benchmark(tmp_path)
    paths = RunPaths(tmp_path / "run")
    paths.run_dir.mkdir()
    inputs = RunInputs(meta, items, papers_dir, paths)
    answerer = FakeAnswerer()
    _prepare_run(inputs, RunOptions(conditions=("full_text",), tracks=("qa",)), run_id="run")

    def run():
        return run_benchmark(run_id="run", inputs=inputs, llm=answerer, config=_fake_config(),
                             decompose_llm=None, generation_models=_generation_models(), options=RunOptions(conditions=("full_text",), tracks=("qa",)))

    def judge():
        return judge_run(inputs=inputs, judge_llm=None, judge_model="unused", max_text_chars=60000)

    run()
    if ledger == "judgments":
        judge()
    path = getattr(paths, ledger)
    original = load_jsonl(path)
    raw = path.read_bytes()
    damaged = raw.rstrip(b"\n") if tail == b"complete_without_newline" else raw.split(b"\n", 1)[0] + b"\n" + tail
    path.write_bytes(damaged)

    counts = run() if ledger == "responses" else judge()

    complete = tail == b"complete_without_newline"
    assert counts["executed" if ledger == "responses" else "judged"] == (0 if complete else 1)
    assert counts["skipped"] == (2 if complete else 1)
    assert len(load_jsonl(path)) == 2
    assert load_jsonl(path)[0] == original[0]
    assert path.read_bytes().endswith(b"\n")
    backups = list(paths.run_dir.glob(f"{path.name}.interrupted-*"))
    assert len(backups) == (0 if complete else 1)
    if backups:
        assert backups[0].read_bytes() == damaged


def test_resume_does_not_repair_interior_corruption(tmp_path):
    meta, items, papers_dir = _benchmark(tmp_path)
    paths = RunPaths(tmp_path / "run")
    paths.run_dir.mkdir()
    _prepare_run(RunInputs(meta, items, papers_dir, paths), RunOptions(tracks=("qa",)), run_id="run")
    raw = b'not json\n{"unfinished":'
    paths.responses.write_bytes(raw)
    answerer = FakeAnswerer()
    with pytest.raises(json.JSONDecodeError):
        run_benchmark(run_id="run", inputs=RunInputs(meta, items, papers_dir, paths), llm=answerer,
                      config=_fake_config(), decompose_llm=None, generation_models=_generation_models(), options=RunOptions(tracks=("qa",)))
    assert paths.responses.read_bytes() == raw
    assert answerer.prompts == []
    assert set(paths.run_dir.iterdir()) == {
        paths.responses, paths.manifest, paths.run_dir / "benchmark.jsonl",
        paths.run_dir / "benchmark.review.csv",
    }


def test_manifest_publication_failure_leaves_no_partial_manifest(monkeypatch, tmp_path):
    from zotero_summarizer.services import _common

    paths = RunPaths(tmp_path / "run")

    def interrupted(*args):
        raise OSError("manifest publication interrupted")

    monkeypatch.setattr(_common.os, "replace", interrupted)
    with pytest.raises(OSError, match="manifest publication interrupted"):
        write_or_check_manifest(paths, {"model": "test", "generation_sha256": "a" * 64,
                                       "benchmark_content_sha256": "a" * 64})
    assert not paths.manifest.exists()
    assert list(paths.run_dir.iterdir()) == []


def test_missing_manifest_never_relabels_existing_responses(tmp_path):
    paths = RunPaths(tmp_path / "run")
    paths.run_dir.mkdir()
    paths.responses.write_text('{}\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="manifest"):
        write_or_check_manifest(paths, {"model": "unverified replacement", "generation_sha256": "a" * 64,
                                       "benchmark_content_sha256": "a" * 64})
    assert not paths.manifest.exists()


def test_interrupted_repair_preserves_the_original_and_archive(monkeypatch, tmp_path):
    from zotero_summarizer.services import _common
    from zotero_summarizer.services.faithbench._runner import _repair_jsonl_tail

    path = tmp_path / "responses.jsonl"
    raw = b'{"item_id": "done"}\n{"unfinished":'
    path.write_bytes(raw)
    replace = _common.os.replace

    def interrupt_repair(source, target):
        if target == path:
            raise OSError("repair interrupted")
        replace(source, target)

    with monkeypatch.context() as scoped:
        scoped.setattr(_common.os, "replace", interrupt_repair)
        with pytest.raises(OSError, match="repair interrupted"):
            _repair_jsonl_tail(path)
    assert path.read_bytes() == raw
    assert next(tmp_path.glob("responses.jsonl.interrupted-*")).read_bytes() == raw
    _repair_jsonl_tail(path)
    assert load_jsonl(path) == [{"item_id": "done"}]
