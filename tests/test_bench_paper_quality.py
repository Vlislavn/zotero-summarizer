"""Unit tests for the deterministic graders in tools/bench_paper_quality.py (offline —
no LLM). The harness lives in tools/ (not a package), so we load it by path."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "tools" / "bench_paper_quality.py"
_spec = importlib.util.spec_from_file_location("bench_paper_quality", _PATH)
bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench)


def test_grade_type_correct_family_and_fallback():
    assert bench.grade_type("policy", "policy", "arg", "arg") == {
        "type_correct": True, "family_correct": True, "fallback": False}
    # right family, wrong leaf, via the generic fallback
    out = bench.grade_type("generic_review", "narrative_review", "arg,synth", "arg,synth")
    assert out["type_correct"] is False and out["family_correct"] is True and out["fallback"] is True


def test_grade_checklist_aligns_and_defaults_missing_to_na():
    gold = {"a": "yes", "b": "no", "c": "yes"}
    pred = {"a": "yes", "b": "yes"}  # c missing → 'na'
    ck = bench.grade_checklist(pred, gold)
    assert ck["pred"] == ["yes", "yes", "na"] and ck["gold"] == ["yes", "no", "yes"]
    assert ck["n_items"] == 3 and ck["agreement"] == round(1 / 3, 4)


def test_grade_band_exact_within1_and_uncertain():
    assert bench.grade_band("highlight", "highlight") == {"exact": True, "within1": True}
    assert bench.grade_band("flag", "neutral") == {"exact": False, "within1": True}      # adjacent
    assert bench.grade_band("flag", "highlight") == {"exact": False, "within1": False}   # 2 apart
    assert bench.grade_band("uncertain", "neutral") == {"exact": False, "within1": False}  # off-axis


def test_grade_selfverify_buckets():
    assert bench.grade_selfverify(True, True) == "TP"    # caught an over-claim
    assert bench.grade_selfverify(False, True) == "FN"   # missed one
    assert bench.grade_selfverify(True, False) == "FP"   # demoted a LEGIT grounding (the bad error)
    assert bench.grade_selfverify(False, False) == "TN"


def test_selfverify_metrics_math():
    from collections import Counter
    m = bench.selfverify_metrics(Counter({"TP": 8, "FP": 1, "FN": 2, "TN": 9}))
    assert m["precision"] == round(8 / 9, 4) and m["recall"] == round(8 / 10, 4)
    assert m["false_positive_rate"] == round(1 / 10, 4)  # 1 legit demoted out of 10 legit


def test_grade_docling_recall_and_undefined():
    assert bench.grade_docling(2, 2) == 1.0
    assert bench.grade_docling(0, 3) == 0.0
    assert bench.grade_docling(5, 3) == 1.0           # capped at 1.0
    assert bench.grade_docling(1, 0) is None          # truth 0 → undefined (leaves denom)


def test_std_sem_across_runs_ddof1():
    out = bench._std_sem_across_runs({0: [1.0, 0.0], 1: [1.0, 1.0]})  # run means 0.5, 1.0
    assert out["mean"] == 0.75 and out["n_runs"] == 2 and out["std"] > 0 and out["sem"] > 0
    assert bench._std_sem_across_runs({0: [1.0, 0.0]})["sem"] == 0.0  # 1 run → no fake error bar


def test_kappa_delegates_to_repo_impl():
    assert bench._kappa(["a", "b", "a"], ["a", "b", "a"]) == 1.0


def test_firewall_candidate_view_hides_graded_labels():
    paper = {"item_key": "K", "text_path": "/t.txt", "pdf_path": "/t.pdf", "gold_type": "policy",
             "gold_rubric": {"thesis": "yes"}, "gold_band": "highlight", "gold_grade": "A",
             "tables_truth": 2}
    view = bench.candidate_view(paper)
    # The pipeline sees ONLY text + the controlled gold TYPE — never the graded labels.
    assert set(view) == {"item_key", "text_path", "pdf_path", "gold_type"}
    for leak in bench._LABEL_FIELDS:
        assert leak not in view
