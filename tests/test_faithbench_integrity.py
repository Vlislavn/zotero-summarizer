"""Artifact identity and anchored ground truth across real benchmark entry points."""
import hashlib
import json
from unittest.mock import Mock

import pytest

from zotero_summarizer.cli import main, _faithbench
from zotero_summarizer.services import faithbench
from zotero_summarizer.services.faithbench import _dataset
from zotero_summarizer.services.faithbench._build_qa import build_traps, verify_candidates
from zotero_summarizer.services.faithbench._corpus import freeze_paper_text
from zotero_summarizer.services.faithbench._runner import RunInputs, RunOptions, RunPaths, generation_identity, load_jsonl
from zotero_summarizer.settings import Settings
from tests.test_faithbench_build import _paper, TEXT_A, TEXT_B
from tests.test_faithbench_runner import _approve_benchmark, _fake_config, _generation_models, FakeAnswerer


@pytest.fixture
def benchmark(tmp_path):
    settings = Settings.load(project_root=tmp_path)
    root = settings.faithbench_dir
    papers = [_paper("A", TEXT_A), _paper("B", TEXT_B)]
    candidates = [
        {"question": "Which dataset was used?", "answer_span": "ImageNet"},
        {"question": "Which drug was studied?", "answer_span": "pembrolizumab"},
    ]
    qas = {}
    for paper, candidate in zip(papers, candidates):
        freeze_paper_text(root / "papers", paper.item_key, paper.text)
        qas[paper.item_key] = verify_candidates([candidate], paper=paper, max_keep=1)
    items = [qa for rows in qas.values() for qa in rows] + build_traps(papers, qas, traps_per_paper=1)
    meta = _dataset.BenchmarkMeta(version=1, created_at="test", builder_model="test", papers=[
        _dataset.PaperManifestEntry(item_key=p.item_key, title=p.title, text_sha256=p.text_sha256,
                                   n_chars=len(p.text)) for p in papers
    ])
    path = root / "benchmark_v1.jsonl"
    _dataset.save_benchmark(path, meta, items)
    review_sha = _approve_benchmark(path, items)
    paths = RunPaths(root / "runs" / "integrity")
    paths.run_dir.mkdir(parents=True)
    manifest = {"run_id": "integrity", "benchmark_path": str(path),
                "benchmark_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "benchmark_content_sha256": _dataset._benchmark_sha(meta, items),
                "benchmark_review_sha256": review_sha,
                "generation_sha256": generation_identity(_fake_config(), _generation_models()),
                "research_goals": list(_fake_config().research_goals), "serial": True, "max_workers": 4,
                "conditions": ["full_text"], "tracks": ["qa"], "runs": 1, "limit": None}
    paths.manifest.write_text(json.dumps(manifest))
    return settings, path, meta, items, paths, manifest


def _bind_invalid_ground_truth(benchmark, items):
    """A valid identity around bad builder output still must fail the span gate."""
    _, _, meta, _, paths, manifest = benchmark
    path = paths.run_dir / "invalid_benchmark.jsonl"
    _dataset.save_benchmark(path, meta, items)
    review_sha = _approve_benchmark(path, items)
    manifest.update(benchmark_path=str(path), benchmark_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    benchmark_content_sha256=_dataset._benchmark_sha(meta, items),
                    benchmark_review_sha256=review_sha)
    paths.manifest.write_text(json.dumps(manifest))


@pytest.mark.parametrize("command", ["judge", "report"])
def test_cli_refuses_edited_benchmark_before_provider_or_report(benchmark, monkeypatch, command):
    from zotero_summarizer.services import run_log

    settings, path, _, _, paths, _ = benchmark
    path.write_bytes(path.read_bytes() + b"\n")  # valid JSONL, different artifact identity
    remote = Mock(side_effect=AssertionError("must not construct judge"))
    publish = Mock(side_effect=AssertionError("must not publish report"))
    monkeypatch.setattr(_faithbench, "_remote_llm", remote)
    # Report now owns artifact loading; observe publication instead of replacing the SUT.
    monkeypatch.setattr(run_log, "append_run", publish)

    with pytest.raises(ValueError, match="SHA|sha|hash"):
        main(["faithbench", command, "--run-id", "integrity", "--project-root", str(settings.project_root)])

    remote.assert_not_called()
    publish.assert_not_called()
    assert not paths.report_json.exists() and not paths.report_md.exists()


@pytest.mark.parametrize("damage", ["second_header", "late_header", "duplicate_item", "duplicate_paper", "scalar_row"])
def test_loader_refuses_ambiguous_identity(benchmark, damage):
    _, path, meta, items, _, _ = benchmark
    rows = [meta.model_dump(), *(item.model_dump() for item in items)]
    if damage == "second_header":
        rows.append(meta.model_dump())
    elif damage == "late_header":
        rows[0], rows[1] = rows[1], rows[0]
    elif damage == "duplicate_item":
        rows.append(items[0].model_dump())
    elif damage == "scalar_row":
        rows.append(None)
    else:
        rows[0]["papers"].append(rows[0]["papers"][0])
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(ValueError):
        _dataset.load_benchmark(path)


@pytest.mark.parametrize("changes", [
    {"paper_text_sha256": "0" * 64}, {"span_start": -1}, {"span_end": 100_000},
    {"span_start": 0}, {"gold_answer": "fabricated"}, {"paper_item_key": "missing"},
    {"evidence_sentence": "Made-up evidence for ImageNet."},
])
def test_runner_rejects_invalid_ground_truth_before_llm(benchmark, changes):
    settings, _, meta, items, paths, _ = benchmark
    items[0] = items[0].model_copy(update=changes)
    _bind_invalid_ground_truth(benchmark, items)
    llm = FakeAnswerer()
    with pytest.raises(ValueError):
        faithbench.run_benchmark(run_id="integrity", inputs=RunInputs(meta, items, settings.faithbench_dir / "papers", paths),
                                llm=llm, config=_fake_config(), decompose_llm=None, generation_models=_generation_models(),
                                options=RunOptions(tracks=("qa",), conditions=("full_text",)))
    assert llm.prompts == []
    assert not paths.responses.exists()


@pytest.mark.parametrize("changes", [
    {"paper_text_sha256": "0" * 64}, {"span_start": 0}, {"gold_answer": "fabricated"},
])
def test_judge_marks_invalid_ground_truth_as_harness_fault(benchmark, changes):
    settings, _, meta, items, paths, _ = benchmark
    qa = items[0].model_copy(update=changes)
    row = {"run_id": "integrity", "item_id": qa.item_id, "track": "qa", "condition": "full_text",
           "run_number": 1, "status": "ok", "parsed": {"answer": qa.gold_answer, "abstained": False}}
    paths.responses.write_text(json.dumps(row) + "\n")
    judge = Mock()
    faithbench.judge_run(inputs=RunInputs(meta, [qa], settings.faithbench_dir / "papers", paths),
                        judge_llm=judge, judge_model="unused", max_text_chars=60_000)
    assert not judge.mock_calls
    verdict, = load_jsonl(paths.judgments)
    assert verdict["success"] is None
    assert verdict["failure_reason"] == "harness_fault"


@pytest.mark.parametrize("changes", [
    {"source_paper_item_key": "missing"}, {"source_gold_answer": "fabricated"},
    {"source_paper_item_key": "A", "source_gold_answer": "ImageNet"},
])
def test_trap_provenance_must_bind_to_a_different_frozen_source(benchmark, changes):
    settings, _, meta, items, paths, _ = benchmark
    items[2] = items[2].model_copy(update=changes)
    _bind_invalid_ground_truth(benchmark, items)
    llm = FakeAnswerer()
    with pytest.raises(ValueError):
        faithbench.run_benchmark(run_id="integrity", inputs=RunInputs(meta, items, settings.faithbench_dir / "papers", paths),
                                llm=llm, config=_fake_config(), decompose_llm=None, generation_models=_generation_models(),
                                options=RunOptions(tracks=("qa",), conditions=("full_text",)))
    assert llm.prompts == []


def test_report_refuses_frozen_text_changed_after_judging(benchmark):
    from zotero_summarizer.services.faithbench._corpus import paper_text_path

    settings, _, meta, items, paths, manifest = benchmark
    inputs = RunInputs(meta, items, settings.faithbench_dir / "papers", paths)
    manifest["limit"] = 1
    paths.manifest.write_text(json.dumps(manifest))
    faithbench.run_benchmark(run_id="integrity", inputs=inputs, llm=FakeAnswerer(), config=_fake_config(),
                            decompose_llm=None, generation_models=_generation_models(), options=RunOptions(tracks=("qa",), conditions=("full_text",), limit=1))
    faithbench.judge_run(inputs=inputs, judge_llm=Mock(), judge_model="unused", max_text_chars=60_000)
    faithbench.build_report(paths=paths, faithbench_dir=settings.faithbench_dir)
    before = {path: path.read_bytes() for path in (paths.report_json, paths.report_md,
                                                  settings.faithbench_dir / "faithbench-runs.jsonl")}
    paper_text_path(inputs.papers_dir, "A", text_sha256=meta.papers[0].text_sha256).write_text("changed")

    with pytest.raises(ValueError):
        main(["faithbench", "report", "--run-id", "integrity", "--project-root", str(settings.project_root)])

    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("claims", [[], None, {}, ["not a claim object"], [{"field": "tldr", "claim": " "}]])
def test_empty_or_malformed_claims_leave_a_failed_trial_not_an_empty_denominator(benchmark, claims):
    settings, _, meta, items, paths, _ = benchmark
    row = {"run_id": "integrity", "item_id": "claims:A", "track": "claims", "condition": "digest",
           "run_number": 1, "status": "ok", "parsed": {"claims": claims}}
    paths.responses.write_text(json.dumps(row) + "\n")
    judge = Mock()
    inputs = RunInputs(meta, items, settings.faithbench_dir / "papers", paths)
    result = faithbench.judge_run(inputs=inputs, judge_llm=judge, judge_model="unused", max_text_chars=60_000)
    assert result == {"judged": 1, "skipped": 0, "escalated": 0}
    verdict, = load_jsonl(paths.judgments)
    assert verdict["success"] is False and verdict["failure_reason"] == "malformed_response"
    assert verdict["claim_idx"] is None
    assert not judge.mock_calls
    resumed = faithbench.judge_run(inputs=inputs, judge_llm=judge, judge_model="unused", max_text_chars=60_000)
    assert resumed == {"judged": 0, "skipped": 1, "escalated": 0}
    assert load_jsonl(paths.judgments) == [verdict]


@pytest.fixture
def judged_run(benchmark):
    settings, _, meta, items, paths, _ = benchmark
    inputs = RunInputs(meta, items, settings.faithbench_dir / "papers", paths)
    faithbench.run_benchmark(run_id="integrity", inputs=inputs, llm=FakeAnswerer(), config=_fake_config(),
                            decompose_llm=None, generation_models=_generation_models(), options=RunOptions(tracks=("qa",), conditions=("full_text",)))
    judge = Mock()
    judge.prompt.return_value = json.dumps({"equivalent": False, "reason": "Different answer"})
    faithbench.judge_run(inputs=inputs, judge_llm=judge, judge_model="test", max_text_chars=60_000)
    return benchmark, inputs, judge


@pytest.mark.parametrize("damage", ["missing_response", "missing_judgment", "changed_response", "wrong_run",
                                    "mixed_context", "legacy_judgment"])
def test_report_requires_complete_current_trial_coverage(judged_run, damage):
    (settings, _, _, _, paths, _), _, _ = judged_run
    path = paths.judgments if damage in {"missing_judgment", "mixed_context", "legacy_judgment"} else paths.responses
    rows = load_jsonl(path)
    if damage.startswith("missing"):
        rows.pop()
    elif damage == "changed_response":
        rows[0]["parsed"]["answer"] = "Changed after judgment"
    elif damage == "mixed_context":
        rows[0]["judge_context"]["model"] = "different-judge"
    elif damage == "legacy_judgment":
        rows[0].pop("response_sha256")
    else:
        rows[0]["run_id"] = "different-run"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(ValueError):
        faithbench.build_report(paths=paths, faithbench_dir=settings.faithbench_dir)
    assert not paths.report_json.exists() and not paths.report_md.exists()
    assert not (settings.faithbench_dir / "faithbench-runs.jsonl").exists()


def test_rejudging_updated_response_replaces_only_that_trial_in_report(judged_run):
    (settings, _, _, _, paths, _), inputs, judge = judged_run
    rows = load_jsonl(paths.responses)
    rows[0]["parsed"]["answer"] = "CIFAR-10"
    paths.responses.write_text("".join(json.dumps(row) + "\n" for row in rows))
    counts = faithbench.judge_run(inputs=inputs, judge_llm=judge, judge_model="test", max_text_chars=60_000)
    assert counts == {"judged": 1, "skipped": 3, "escalated": 1}
    report = faithbench.build_report(paths=paths, faithbench_dir=settings.faithbench_dir)
    assert report["totals"]["n_response_trials"] == report["totals"]["n_judgments"] == 4
    assert report["tracks"]["qa"]["full_text"]["accuracy"]["mean"] == 0.0


def test_changed_judge_configuration_cannot_reuse_old_verdicts(judged_run):
    (settings, _, _, _, paths, _), inputs, judge = judged_run
    counts = faithbench.judge_run(inputs=inputs, judge_llm=judge, judge_model="new-judge", max_text_chars=60_000)
    assert counts["judged"] == 4 and counts["skipped"] == 0
    report = faithbench.build_report(paths=paths, faithbench_dir=settings.faithbench_dir)
    assert report["judge"]["models_used"] == ["new-judge"]
    assert report["totals"]["n_judgments"] == 4


def test_legacy_verdicts_are_rejudged_before_reporting(judged_run):
    (settings, _, _, _, paths, _), inputs, judge = judged_run
    rows = load_jsonl(paths.judgments)
    for row in rows:
        row.pop("response_sha256")
        row.pop("judge_context")
    paths.judgments.write_text("".join(json.dumps(row) + "\n" for row in rows))
    counts = faithbench.judge_run(inputs=inputs, judge_llm=judge, judge_model="test", max_text_chars=60_000)
    assert counts["judged"] == 4 and counts["skipped"] == 0
    assert faithbench.build_report(paths=paths, faithbench_dir=settings.faithbench_dir)["totals"]["n_judgments"] == 4


def test_new_frozen_fault_invalidates_cached_success(judged_run):
    from zotero_summarizer.services.faithbench._corpus import paper_text_path

    (_, _, meta, _, paths, _), inputs, judge = judged_run
    paper_text_path(inputs.papers_dir, "A", text_sha256=meta.papers[0].text_sha256).write_text("changed")
    counts = faithbench.judge_run(inputs=inputs, judge_llm=judge, judge_model="test", max_text_chars=60_000)
    assert counts["judged"] == 4 and counts["skipped"] == 0
    assert any(row["failure_reason"] == "harness_fault" and row["success"] is None
               for row in load_jsonl(paths.judgments)[-4:])


@pytest.mark.parametrize("origin", ["empty_digest", "empty_decomposition", "empty_cache", "malformed_cache"])
def test_decomposition_cannot_return_or_cache_an_empty_claim_trial(tmp_path, origin):
    from zotero_summarizer.services.faithbench._build_claims import decompose_digest

    cache = tmp_path / "claims-v3-abc.json"
    if origin.endswith("cache"):
        cache.write_text(json.dumps([] if origin == "empty_cache" else [{"claim": "fact"}]))
    llm = Mock()
    llm.prompt.return_value = '{"claims": []}'
    with pytest.raises(ValueError):
        decompose_digest(digest_dump={} if origin == "empty_digest" else {"tldr": "A factual statement."},
                         digest_sha="abc", title="Paper", decompose_llm=llm, cache_dir=tmp_path)
    if origin == "empty_decomposition":
        llm.prompt.assert_called_once()
        assert not cache.exists()
    else:
        llm.prompt.assert_not_called()


def test_report_requires_every_claim_not_just_every_response(benchmark):
    settings, _, meta, items, paths, manifest = benchmark
    manifest["tracks"] = ["claims"]
    paths.manifest.write_text(json.dumps(manifest))
    rows = [{"run_id": "integrity", "item_id": f"claims:{paper.item_key}", "track": "claims",
             "condition": "digest", "run_number": 1, "status": "ok", "parsed": {"claims": [
                 {"field": "tldr", "claim": "First fact."}, {"field": "tldr", "claim": "Second fact."}]}}
            for paper in meta.papers]
    paths.responses.write_text("".join(json.dumps(row) + "\n" for row in rows))
    judge = Mock()
    judge.prompt.return_value = '{"supported": true, "quote": "", "reason": "Test response"}'
    faithbench.judge_run(inputs=RunInputs(meta, items, settings.faithbench_dir / "papers", paths),
                        judge_llm=judge, judge_model="test", max_text_chars=60_000)
    report = faithbench.build_report(paths=paths, faithbench_dir=settings.faithbench_dir)
    assert report["totals"]["n_response_trials"] == 2 and report["totals"]["n_judgments"] == 4
    before = paths.report_json.read_bytes()
    verdicts = load_jsonl(paths.judgments)[:-1]
    paths.judgments.write_text("".join(json.dumps(row) + "\n" for row in verdicts))
    with pytest.raises(ValueError, match="Incomplete"):
        faithbench.build_report(paths=paths, faithbench_dir=settings.faithbench_dir)
    assert paths.report_json.read_bytes() == before
