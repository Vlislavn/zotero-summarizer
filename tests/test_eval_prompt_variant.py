"""Unit tests for the pure comparison in tools/eval_prompt_variant.py (offline —
no faithbench data, no LLM). The harness lives in tools/ (not a package), so we
load it by path (mirrors tests/test_bench_paper_quality.py)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "tools" / "eval_prompt_variant.py"
_spec = importlib.util.spec_from_file_location("eval_prompt_variant", _PATH)
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)


def _report(support_mean: float, halluc_rate: float, *, median: float | None = None) -> dict:
    """A minimal faithbench-shaped report.json: one claims digest support_rate
    block + one QA condition with a trap.hallucination_rate (the only two fields
    the comparison reads)."""
    return {
        "judge": {"models_used": ["Qwen3.5-397B-A17B-FP8"]},
        "tracks": {
            "qa": {
                "retrieval": {"trap": {"hallucination_rate": halluc_rate}},
            },
            "claims": {
                "digest": {
                    "support_rate": {
                        "mean": support_mean,
                        "median": support_mean if median is None else median,
                        "std_across_runs": 0.0,
                        "sem_across_runs": 0.0,
                    }
                }
            },
        },
    }


def test_candidate_better_when_support_up_and_hallucination_equal():
    baseline = _report(0.90, 0.05)
    candidate = _report(0.95, 0.05)  # +0.05 support, same hallucination
    out = ev.compare_variants(baseline, candidate)
    assert out["support_rate_delta"] == 0.05
    assert out["hallucination_rate_delta"] == 0.0
    assert out["verdict"] == "candidate_better"


def test_not_candidate_better_when_support_up_but_hallucination_also_up():
    baseline = _report(0.90, 0.05)
    candidate = _report(0.95, 0.10)  # +0.05 support BUT +0.05 hallucination (safety regression)
    out = ev.compare_variants(baseline, candidate)
    assert out["support_rate_delta"] == 0.05
    assert out["hallucination_rate_delta"] == 0.05
    assert out["verdict"] != "candidate_better"
    assert out["verdict"] == "baseline_wins"  # a strict safety regression hands it to the baseline


def test_inconclusive_when_near_equal():
    baseline = _report(0.900, 0.05)
    candidate = _report(0.905, 0.05)  # +0.005 support < 0.01 margin, no safety change
    out = ev.compare_variants(baseline, candidate)
    assert out["support_rate_delta"] == 0.005
    assert out["hallucination_rate_delta"] == 0.0
    assert out["verdict"] == "inconclusive"


def test_baseline_wins_when_support_drops():
    baseline = _report(0.95, 0.05)
    candidate = _report(0.85, 0.05)  # support strictly worse, no safety win to redeem it
    out = ev.compare_variants(baseline, candidate)
    assert out["support_rate_delta"] == -0.10
    assert out["verdict"] == "baseline_wins"


def test_hallucination_aggregates_worst_condition():
    """Two QA conditions → the comparison uses the WORST (max) hallucination_rate,
    so a regression on ANY condition is caught."""
    baseline = _report(0.90, 0.05)
    candidate = _report(0.95, 0.05)
    # add a second, worse condition to the candidate only
    candidate["tracks"]["qa"]["full_text"] = {"trap": {"hallucination_rate": 0.20}}
    out = ev.compare_variants(baseline, candidate)
    assert out["candidate"]["hallucination_rate"] == 0.20  # worst, not the 0.05 retrieval one
    assert out["verdict"] == "baseline_wins"  # the worst-condition regression disqualifies it


def test_support_extractor_fails_loud_on_missing_track():
    bad = {"tracks": {"qa": {"retrieval": {"trap": {"hallucination_rate": 0.0}}}}}  # no claims track
    with pytest.raises(KeyError, match="support_rate"):
        ev.support_rate(bad)


def test_hallucination_extractor_fails_loud_on_missing_trap():
    bad = {"tracks": {"qa": {"retrieval": {}}}, "claims": {}}  # no trap block anywhere
    with pytest.raises(KeyError, match="trap"):
        ev.hallucination_rate(bad)


def test_margin_and_tolerance_are_configurable():
    baseline = _report(0.90, 0.05)
    candidate = _report(0.905, 0.05)  # +0.005 support
    # default margin 0.01 → inconclusive; a looser margin makes it a win
    assert ev.compare_variants(baseline, candidate)["verdict"] == "inconclusive"
    assert ev.compare_variants(baseline, candidate, support_margin=0.001)["verdict"] == "candidate_better"
