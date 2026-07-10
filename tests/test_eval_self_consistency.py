"""Unit tests for the self-consistency knob-sweep eval's PURE aggregation.

No LLM, no data, no I/O — only ``summarize_consistency_sweep`` (per-N verdict
stability vs the max-N gold) and ``recommend_smallest_stable_n`` (smallest N within
tolerance) on SYNTHETIC verdict dicts where the stability is known by construction.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# Load tools/eval_self_consistency.py by path (tools/ is a scripts dir, not a package).
_MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "eval_self_consistency.py"
_spec = importlib.util.spec_from_file_location("eval_self_consistency", _MODULE_PATH)
assert _spec and _spec.loader
esc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(esc)


def test_known_stability_half_and_full():
    """N=1 disagrees with N=7 (gold) on half the papers → 0.5; N=3 matches fully → 1.0;
    N=5 matches fully → 1.0; N=7 is the gold → 1.0 by construction."""
    per_n = {
        1: ["highlight/A", "highlight/A", "flag/D", "flag/D"],   # last two differ from gold
        3: ["highlight/A", "highlight/A", "neutral/B", "neutral/C"],  # == gold
        5: ["highlight/A", "highlight/A", "neutral/B", "neutral/C"],  # == gold
        7: ["highlight/A", "highlight/A", "neutral/B", "neutral/C"],  # gold
    }
    rows = esc.summarize_consistency_sweep(per_n)
    by_n = {r["n"]: r for r in rows}

    assert [r["n"] for r in rows] == [1, 3, 5, 7]  # ascending
    assert by_n[1]["stability_vs_max"] == 0.5
    assert by_n[3]["stability_vs_max"] == 1.0
    assert by_n[5]["stability_vs_max"] == 1.0
    assert by_n[7]["stability_vs_max"] == 1.0  # gold matches itself
    assert all(r["n_papers"] == 4 for r in rows)


def test_recommend_smallest_within_tolerance():
    """With N=1 at 0.5 and N>=3 at 1.0: tolerance 0.1 must NOT accept N=1 (gap 0.5 > 0.1)
    → smallest stable is N=3. A loose tolerance of 0.6 DOES accept N=1."""
    per_n = {
        1: ["a", "a", "x", "x"],          # 0.5 vs gold
        3: ["a", "a", "b", "c"],          # 1.0
        5: ["a", "a", "b", "c"],          # 1.0
        7: ["a", "a", "b", "c"],          # gold
    }
    rows = esc.summarize_consistency_sweep(per_n)
    assert esc.recommend_smallest_stable_n(rows, tolerance=0.1) == 3
    assert esc.recommend_smallest_stable_n(rows, tolerance=0.6) == 1  # loose → 1 fine
    assert esc.recommend_smallest_stable_n(rows, tolerance=0.0) == 3  # exact-gold demand


def test_recommend_picks_one_when_even_n1_is_stable():
    """When N=1 already equals the gold on every paper, the smallest stable N is 1 —
    the 'is 1 enough?' answer the sweep exists to give."""
    per_n = {
        1: ["a", "b", "c"],
        3: ["a", "b", "c"],
        7: ["a", "b", "c"],  # gold
    }
    rows = esc.summarize_consistency_sweep(per_n)
    assert all(r["stability_vs_max"] == 1.0 for r in rows)
    assert esc.recommend_smallest_stable_n(rows, tolerance=0.1) == 1


def test_recommend_needs_max_when_only_gold_stable():
    """When NO sub-max N is within tolerance (only the gold itself qualifies), the
    recommendation is max(N) — 'you actually need the full N'."""
    per_n = {
        1: ["x", "x", "x", "x"],          # 0.0 vs gold
        3: ["y", "y", "y", "y"],          # 0.0 vs gold
        7: ["a", "b", "c", "d"],          # gold
    }
    rows = esc.summarize_consistency_sweep(per_n)
    by_n = {r["n"]: r for r in rows}
    assert by_n[1]["stability_vs_max"] == 0.0
    assert by_n[3]["stability_vs_max"] == 0.0
    assert esc.recommend_smallest_stable_n(rows, tolerance=0.1) == 7


def test_intermediate_stability_fraction():
    """A non-trivial fraction: N=3 matches gold on 2 of 3 papers → 0.6667 (rounded 4dp)."""
    per_n = {
        3: ["a", "b", "ZZ"],   # third differs
        7: ["a", "b", "c"],    # gold
    }
    rows = esc.summarize_consistency_sweep(per_n)
    by_n = {r["n"]: r for r in rows}
    assert by_n[3]["stability_vs_max"] == round(2 / 3, 4)
    assert by_n[7]["stability_vs_max"] == 1.0


def test_ragged_sweep_fails_fast():
    """A mismatched per-N paper count must RAISE, not silently fake the stability with
    a shorter list (fail-fast on a malformed sweep)."""
    per_n = {
        3: ["a", "b"],          # only 2 papers
        7: ["a", "b", "c"],     # gold has 3
    }
    with pytest.raises(ValueError, match="ragged sweep"):
        esc.summarize_consistency_sweep(per_n)


def test_empty_input_fails_fast():
    with pytest.raises(ValueError, match="empty"):
        esc.summarize_consistency_sweep({})
    with pytest.raises(ValueError, match="no rows"):
        esc.recommend_smallest_stable_n([], tolerance=0.1)
