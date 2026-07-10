"""Chunking A/B pure metrics: distinctive tokens, late-content recall, A/B summary."""
from __future__ import annotations

import pytest

from tools.eval_chunking import distinctive_tokens, late_recall, summarize_ab


def test_distinctive_tokens_numbers_and_named():
    toks = distinctive_tokens("DxChain reached 84.98% on MIMIC-IV with GPT and 4761 cases.")
    assert "84.98%" in toks and "4761" in toks
    assert "DxChain" in toks and "MIMIC" in toks
    assert "on" not in toks and "with" not in toks  # stop-ish lowercase words excluded


def test_late_recall_high_when_digest_covers_late_content():
    # A distinctive token that appears ONLY in the last third of the paper.
    early = "Intro and methods. " * 200
    late = "The novel ZephyrMetric scored 99.7 percent. "
    full = early + late
    assert late_recall("The model's ZephyrMetric hit 99.7 in the end.", full) > 0.4
    assert late_recall("Only the intro is summarized here.", full) == 0.0


def test_late_recall_validates():
    with pytest.raises(ValueError):
        late_recall("d", "f", tail_frac=1.0)


def test_summarize_ab_picks_winner_by_late_recall():
    results = [
        {"strategy": "prefix", "late_recall": 0.1, "completeness": 1.0, "secs": 12.0},
        {"strategy": "prefix", "late_recall": 0.2, "completeness": 1.0, "secs": 13.0},
        {"strategy": "map_reduce", "late_recall": 0.8, "completeness": 1.0, "secs": 40.0},
        {"strategy": "map_reduce", "late_recall": 0.7, "completeness": 1.0, "secs": 38.0},
    ]
    s = summarize_ab(results)
    assert s["winner"] == "map_reduce"
    prefix_row = next(r for r in s["rows"] if r["strategy"] == "prefix")
    assert prefix_row["n"] == 2 and prefix_row["late_recall"] == 0.15


def test_summarize_ab_latency_tiebreak_when_coverage_equivalent():
    # The real A/B case: near-zero, noise-level late_recall for all → pick the FASTEST.
    results = [
        {"strategy": "prefix", "late_recall": 0.001, "completeness": 1.0, "secs": 18.0},
        {"strategy": "rank", "late_recall": 0.002, "completeness": 1.0, "secs": 16.0},
        {"strategy": "map_reduce", "late_recall": 0.002, "completeness": 1.0, "secs": 136.0},
    ]
    s = summarize_ab(results)
    assert s["inconclusive_coverage"] is True
    assert s["decided_by"] == "latency"
    assert s["winner"] == "rank"  # cheapest among coverage-equivalent — not the 8× slower map_reduce


def test_summarize_ab_empty_raises():
    with pytest.raises(ValueError):
        summarize_ab([])
