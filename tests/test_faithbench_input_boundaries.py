"""Benchmark budgets and safety cohorts fail before provider work or publication."""
import json
from unittest.mock import Mock

import pytest

from zotero_summarizer.cli import main
from zotero_summarizer.cli import _faithbench
from zotero_summarizer.services.faithbench._build_qa import build_items, _windows
from zotero_summarizer.services.faithbench._constants import QA_MAX_WINDOWS, QA_WINDOW_CHARS
from zotero_summarizer.services.faithbench._corpus import select_papers
from zotero_summarizer.services.faithbench._runner import RunInputs, RunOptions, RunPaths, run_benchmark
from zotero_summarizer.settings import Settings
from tests.test_faithbench_build import _paper, TEXT_A, TEXT_B
from tests.test_faithbench_runner import (
    _approve_benchmark, _benchmark, _fake_config, _generation_models, _prepare_run, FakeAnswerer,
)


@pytest.mark.parametrize("command,option,value", [
    ("build", "n-papers", "0"), ("build", "n-papers", "1"),
    ("build", "qa-per-paper", "0"), ("build", "qa-per-paper", "-2"),
    ("build", "traps-per-paper", "0"), ("build", "traps-per-paper", "-2"),
    ("run", "runs", "0"), ("run", "runs", "-1"),
    ("run", "limit", "0"), ("run", "limit", "-1"),
    ("run", "conditions", ""), ("run", "conditions", "typo"),
    ("run", "conditions", "full_text,"), ("run", "conditions", "retrieval,retrieval"),
    ("run", "tracks", ""), ("run", "tracks", "typo"),
    ("run", "tracks", ",qa"), ("run", "tracks", "claims,claims"),
])
def test_invalid_cli_input_never_loads_settings_or_dispatches(monkeypatch, capsys, command, option, value):
    load, handler = Mock(), Mock(return_value=0)
    monkeypatch.setattr(Settings, "load", load)
    monkeypatch.setattr(_faithbench, f"_faithbench_{command}", handler)

    with pytest.raises(SystemExit) as error:
        main(["faithbench", command, f"--{option}={value}"])

    assert error.value.code == 2
    assert option in capsys.readouterr().err
    load.assert_not_called()
    handler.assert_not_called()


@pytest.mark.parametrize("kwargs", [
    {"runs": 0}, {"runs": -1}, {"runs": True}, {"runs": 1.5},
    {"limit": 0}, {"limit": -1}, {"limit": False},
    {"max_workers": 0}, {"max_workers": -1},
    {"conditions": ()}, {"conditions": ("bad",)},
    {"conditions": ("full_text", "full_text")}, {"conditions": ["full_text"]},
    {"tracks": ()}, {"tracks": ("bad",)}, {"tracks": ("qa", "qa")},
    {"tracks": ("claims",), "limit": 1},
])
def test_invalid_run_options_cannot_be_constructed(kwargs):
    with pytest.raises(ValueError):
        RunOptions(**kwargs)


@pytest.mark.parametrize("n_papers", [0, -1, 1, True])
def test_selection_rejects_invalid_budget_without_reader_or_files(tmp_path, n_papers):
    reader, extractor = Mock(), Mock()
    with pytest.raises(ValueError):
        select_papers(reader=reader, extractor=extractor, papers_dir=tmp_path / "papers",
                      pdf_root=tmp_path, n_papers=n_papers)
    assert not reader.mock_calls and not extractor.mock_calls
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kwargs", [
    {"qa_per_paper": 0}, {"qa_per_paper": -1}, {"qa_per_paper": True},
    {"traps_per_paper": 0}, {"traps_per_paper": -1},
    {"papers": []}, {"papers": [_paper("A", TEXT_A)]},
    {"papers": [_paper("A", TEXT_A), _paper("A", TEXT_A)]},
])
def test_build_rejects_invalid_budget_or_single_paper_before_llm(kwargs):
    llm = Mock()
    with pytest.raises(ValueError):
        build_items(builder_llm=llm, **{"papers": [_paper("A", TEXT_A), _paper("B", TEXT_B)], **kwargs})
    llm.assert_not_called()
    assert not llm.mock_calls


def test_build_refuses_empty_trap_cohort_after_real_candidate_gate():
    llm = Mock()
    llm.prompt.return_value = json.dumps({"items": [
        {"question": "Which dataset was used?", "answer_span": "ImageNet"},
    ]})
    with pytest.raises(ValueError):
        build_items(papers=[_paper("A", TEXT_A), _paper("B", TEXT_A)],
                    builder_llm=llm, qa_per_paper=1, traps_per_paper=1)
    assert llm.prompt.call_count == 2


@pytest.mark.parametrize("kind", ["qa", "trap"])
def test_run_refuses_single_cohort_legacy_benchmark_before_llm(tmp_path, kind):
    meta, items, papers_dir = _benchmark(tmp_path)
    paths = RunPaths(tmp_path / "run")
    llm = FakeAnswerer()
    with pytest.raises(ValueError):
        run_benchmark(run_id="test", inputs=RunInputs(meta, [i for i in items if i.kind == kind],
                                                     papers_dir, paths),
                      llm=llm, config=_fake_config(), decompose_llm=None, generation_models=_generation_models(), options=RunOptions(tracks=("qa",)))
    assert llm.prompts == []
    assert not paths.run_dir.exists()


@pytest.mark.parametrize("size", [QA_WINDOW_CHARS + 1, 11_999, 17_999, 18_000, 50_003])
def test_qa_windows_cover_short_papers_and_include_both_ends_when_sampled(size):
    # Unique characters make coverage/position independent of the implementation's formula.
    text = "".join(chr(0x1000 + index) for index in range(size))
    windows = _windows(text)
    assert len(windows) <= QA_MAX_WINDOWS
    assert all(0 < len(window) <= QA_WINDOW_CHARS for window in windows)
    assert windows[0] == text[:QA_WINDOW_CHARS]
    assert windows[-1] == text[-QA_WINDOW_CHARS:]
    if size <= QA_MAX_WINDOWS * QA_WINDOW_CHARS:
        assert set("".join(windows)) == set(text)


def test_smoke_run_without_selected_traps_reports_unmeasured_not_zero(tmp_path):
    from zotero_summarizer.services.faithbench._judge import judge_run
    from zotero_summarizer.services.faithbench._report import build_report

    meta, items, papers_dir = _benchmark(tmp_path)
    paths = RunPaths(tmp_path / "run")
    paths.run_dir.mkdir()
    inputs = RunInputs(meta, items, papers_dir, paths)
    _prepare_run(inputs, RunOptions(tracks=("qa",), conditions=("full_text",), limit=1), run_id="smoke")
    counts = run_benchmark(run_id="smoke", inputs=inputs, llm=FakeAnswerer(), config=_fake_config(),
                           decompose_llm=None, generation_models=_generation_models(), options=RunOptions(tracks=("qa",), conditions=("full_text",), limit=1))
    judge_run(inputs=inputs, judge_llm=Mock(), judge_model="test", max_text_chars=60_000)
    report = build_report(paths=paths, faithbench_dir=tmp_path)

    assert counts == {"executed": 1, "skipped": 0, "failed": 0}
    block = report["tracks"]["qa"]["full_text"]
    assert block["accuracy"]["mean"] == 1.0
    assert block["trap"]["n_trap_trials"] == 0
    assert block["trap"]["hallucination_rate"] is None
    assert block["trap"]["abstention_recall"] is None
    assert block["trap"]["abstention_precision"] is None
    assert "Trap hallucination rate: N/A" in paths.report_md.read_text().replace("**", "")
    headline = json.loads((tmp_path / "faithbench-runs.jsonl").read_text())
    assert headline["qa_full_text_trap_hallucination_rate"] is None


def test_valid_cli_build_and_run_use_the_requested_budget(tmp_path, monkeypatch):
    from zotero_summarizer.integrations import zotero_read
    from zotero_summarizer.services import _adapters
    from zotero_summarizer.services.faithbench._dataset import load_benchmark
    from zotero_summarizer.services.faithbench._runner import load_jsonl
    from zotero_summarizer.services.llm import factory
    from zotero_summarizer.services.setup import bootstrap

    monkeypatch.setenv("PDF_ROOT", str(tmp_path / "pdfs"))
    settings = Settings.load(project_root=tmp_path)
    settings.env_path.write_text(f"PDF_ROOT={tmp_path / 'pdfs'}\nZOTERO_DATA_DIR={tmp_path / 'zotero'}\n")
    bootstrap.bootstrap_phase0(settings)
    reader, extractor, builder = Mock(), Mock(), Mock()
    reader.get_all_items.return_value = {"items": [{"item_key": key} for key in ("A", "B", "C")]}
    reader.get_item_detail.side_effect = lambda key: {
        "title": f"Paper {key}", "pdf_path": str(settings.pdf_root / f"{key}.pdf"),
    }
    extractor.extract_text.side_effect = lambda path: (TEXT_A if path.endswith("A.pdf") else TEXT_B) * 100
    builder.prompt.side_effect = lambda prompt: json.dumps({"items": [{
        "question": "Which dataset was used?" if "ImageNet" in prompt else "Which drug was studied?",
        "answer_span": "ImageNet" if "ImageNet" in prompt else "pembrolizumab",
        "answer_type": "entity",
    }]})
    monkeypatch.setattr(zotero_read, "ZoteroReader", Mock(return_value=reader))
    monkeypatch.setattr(_adapters, "build_pdf_extractor", Mock(return_value=extractor))
    monkeypatch.setattr(_faithbench, "_remote_llm", Mock(return_value=builder))

    assert main(["faithbench", "build", "--project-root", str(tmp_path),
                 "--n-papers", "2", "--qa-per-paper", "1", "--traps-per-paper", "1"]) == 0
    meta, items = load_benchmark(settings.faithbench_dir / "benchmark_v1.jsonl")
    assert [paper.item_key for paper in meta.papers] == ["A", "B"]
    assert len(items) == 4 and {item.kind for item in items} == {"qa", "trap"}
    assert reader.get_item_detail.call_count == extractor.extract_text.call_count == 2
    assert (settings.faithbench_dir / "benchmark_v1.review.csv").exists()
    with pytest.raises(ValueError, match="not human-approved"):
        main(["faithbench", "run", "--project-root", str(tmp_path), "--run-id", "unapproved",
              "--tracks", "qa", "--conditions", "full_text"])
    _approve_benchmark(settings.faithbench_dir / "benchmark_v1.jsonl", items)
    answerer = FakeAnswerer()
    monkeypatch.setattr(factory, "build_client_for_stage", Mock(return_value=answerer))
    assert main(["faithbench", "run", "--project-root", str(tmp_path), "--run-id", "budget",
                 "--tracks", " qa ", "--conditions", " retrieval , full_text ",
                 "--runs", "2", "--limit", "3"]) == 0
    rows = load_jsonl(settings.faithbench_dir / "runs" / "budget" / "responses.jsonl")
    assert len(rows) == len(answerer.prompts) == 12
    assert {(r["item_id"], r["condition"], r["run_number"]) for r in rows} == {
        (item.item_id, condition, run) for item in items[:3]
        for condition in ("retrieval", "full_text") for run in (1, 2)
    }


def test_claims_run_refuses_empty_paper_manifest_before_llm(tmp_path):
    meta, items, papers_dir = _benchmark(tmp_path)
    meta = meta.model_copy(update={"papers": []})
    answerer, decomposer = Mock(), Mock()
    paths = RunPaths(tmp_path / "run")
    with pytest.raises(ValueError):
        run_benchmark(run_id="empty", inputs=RunInputs(meta, items, papers_dir, paths),
                      llm=answerer, config=_fake_config(), decompose_llm=decomposer, generation_models=_generation_models(True),
                      options=RunOptions(tracks=("claims",)))
    assert not answerer.mock_calls and not decomposer.mock_calls
    assert not paths.run_dir.exists()


def test_unjudgeable_traps_are_unmeasured_not_successful():
    from zotero_summarizer.services.faithbench._stats import calculate_statistics

    judgments = [{"item_id": "t", "condition": "full_text", "track": "qa", "run_number": 1,
                  "success": None, "method": "none", "failure_reason": "harness_fault"}]
    block = calculate_statistics([], judgments, {"t": {"kind": "trap"}})["tracks"]["qa"]["full_text"]
    assert block["n_unjudgeable"] == 1
    assert block["answerable_accuracy"] is None
    assert block["wrong_abstain_rate"] is None
    assert block["trap"] == {"n_trap_trials": 0, "hallucination_rate": None,
                              "abstention_recall": None, "abstention_precision": None}
