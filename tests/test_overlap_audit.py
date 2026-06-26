"""Tests for the on-demand all-pairs semantic overlap audit (tools/precommit).

Deterministic and model-free: a FAKE ``embed_fn`` returns known unit vectors so the
real ``sentence-transformers`` model is never loaded. Mirrors the importlib-by-path
loader idiom of ``tests/test_redundancy_lint.py`` (``tools/precommit`` is not a package).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PRECOMMIT = Path(__file__).resolve().parent.parent / "tools" / "precommit"
sys.path.insert(0, str(_PRECOMMIT))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _PRECOMMIT / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # register before exec (frozen-dataclass resolution)
    spec.loader.exec_module(module)
    return module


_load("_redundancy_clones")  # check_overlaps imports it by name
co = _load("check_overlaps")


def _unit(name: str, struct, free, source: str):
    return co.FunctionUnit(
        path=f"{name}.py",
        qualname=name,
        line=1,
        struct_tokens=tuple(struct),
        free_tokens=frozenset(free),
        source=source,
    )


def _fake_embedder(vectors_by_source: dict):
    def embed(texts):
        return [vectors_by_source[text] for text in texts]

    return embed


# --- combine math ---


def test_combine_hybrid_weighting():
    assert co.combine(0.4, 0.6, 0.8) == pytest.approx(0.5 * 0.8 + 0.3 * 0.4 + 0.2 * 0.6)


def test_combine_deterministic_weighting():
    assert co.combine(0.5, 0.5, None) == pytest.approx(0.6 * 0.5 + 0.4 * 0.5)


def test_combine_all_ones_is_one():
    assert co.combine(1.0, 1.0, 1.0) == pytest.approx(1.0)


# --- find_overlaps: ranking, multi-overlap, symmetric dedup ---


def _three_units():
    # struct + api identical across all three (tokens/free equal); only embed varies.
    struct = ["Module", "FunctionDef", "Return", "FN"]
    free = {"alpha", "beta"}
    f = _unit("f", struct, free, "src-f")
    g = _unit("g", struct, free, "src-g")
    h = _unit("h", struct, free, "src-h")
    vectors = {"src-f": [1.0, 0.0], "src-g": [0.96, 0.28], "src-h": [0.6, 0.8]}
    return [f, g, h], _fake_embedder(vectors)


def test_pairs_ranked_descending():
    units, embed = _three_units()
    pairs = co.find_overlaps(units, threshold=0.0, min_nodes=0, embed_fn=embed)
    combined = [p.combined for p in pairs]
    assert combined == sorted(combined, reverse=True)
    # fg=0.98 > gh=0.90 > fh=0.80
    assert pairs[0].combined == pytest.approx(0.98)
    assert pairs[-1].combined == pytest.approx(0.80)


def test_multi_overlap_function_in_multiple_pairs():
    units, embed = _three_units()
    pairs = co.find_overlaps(units, threshold=0.0, min_nodes=0, embed_fn=embed)
    appearances = [p.a.qualname for p in pairs] + [p.b.qualname for p in pairs]
    assert appearances.count("f") == 2  # f overlaps with both g and h — not best-match-only


def test_symmetric_dedup_each_pair_once():
    units, embed = _three_units()
    pairs = co.find_overlaps(units, threshold=0.0, min_nodes=0, embed_fn=embed)
    keys = {frozenset((p.a.qualname, p.b.qualname)) for p in pairs}
    assert len(keys) == len(pairs) == 3  # exactly the 3 unordered pairs, no (b,a) twins


def test_changed_filter_restricts_to_diff_touching_pairs():
    # f, g, h all mutually overlap; only `g` is "changed" -> only pairs touching g remain
    # (the other side still ranges over the whole corpus).
    units, embed = _three_units()
    pairs = co.find_overlaps(units, threshold=0.0, min_nodes=0, embed_fn=embed, changed={"g.py"})
    touched = {frozenset((p.a.qualname, p.b.qualname)) for p in pairs}
    assert touched == {frozenset(("f", "g")), frozenset(("g", "h"))}  # f<->h dropped (neither changed)


def test_threshold_boundary_inclusive():
    units, embed = _three_units()
    at = co.find_overlaps(units, threshold=0.80, min_nodes=0, embed_fn=embed)
    above = co.find_overlaps(units, threshold=0.8001, min_nodes=0, embed_fn=embed)
    assert any(p.combined == pytest.approx(0.80) for p in at)  # >= is inclusive
    assert all(p.combined > 0.80 for p in above)  # the 0.80 pair dropped


# --- deterministic (no-embed) path ---


def test_no_embed_uses_deterministic_weighting():
    struct = ["Module", "FunctionDef", "Return", "FN"]
    a = _unit("a", struct, {"x"}, "src-a")
    b = _unit("b", struct, {"x"}, "src-b")
    pairs = co.find_overlaps([a, b], threshold=0.0, min_nodes=0, embed_fn=None)
    assert len(pairs) == 1
    assert pairs[0].embed is None
    assert pairs[0].combined == pytest.approx(0.6 * 1.0 + 0.4 * 1.0)


def test_report_renders_embed_na_when_deterministic():
    a = _unit("a", ["X", "Y"], {"x"}, "")
    b = _unit("b", ["X", "Y"], {"x"}, "")
    pairs = co.find_overlaps([a, b], threshold=0.0, min_nodes=0, embed_fn=None)
    report = co.format_report(pairs, embed_enabled=False, reason="embeddings disabled")
    assert "deterministic-only" in report
    assert "embed n/a" in report


def test_json_embed_null_when_deterministic():
    a = _unit("a", ["X", "Y"], {"x"}, "")
    b = _unit("b", ["X", "Y"], {"x"}, "")
    pairs = co.find_overlaps([a, b], threshold=0.0, min_nodes=0, embed_fn=None)
    record = co.pairs_to_json(pairs)[0]
    assert record["embed"] is None
    assert set(record["a"]) == {"path", "line", "qualname"}


# --- prefilters & graded struct ---


def test_min_nodes_prefilter_drops_tiny_functions():
    small = _unit("small", ["A", "B", "C"], {"x"}, "")
    big = _unit("big", ["A"] * 30, {"x"}, "")
    pairs = co.find_overlaps([small, big], threshold=0.0, min_nodes=10, embed_fn=None)
    assert pairs == []  # `small` (3 nodes) filtered out -> no pair


def test_graded_struct_between_zero_and_one():
    # Same free vocabulary (api=1) but partially-overlapping structural token streams.
    a = _unit("a", ["Module", "If", "Return", "Assign", "Call"], {"x"}, "")
    b = _unit("b", ["Module", "For", "Return", "Assign", "Call"], {"x"}, "")
    pairs = co.find_overlaps([a, b], threshold=0.0, min_nodes=0, embed_fn=None)
    assert len(pairs) == 1
    assert 0.0 < pairs[0].struct < 1.0  # SequenceMatcher ratio is graded, not binary


# --- source extraction & empty-source masking ---


def test_collect_units_captures_source():
    source = "def foo(value):\n    total = value + 1\n    return total\n"
    units = co.collect_units(source, "sample.py")
    assert len(units) == 1
    assert units[0].qualname == "foo"
    assert "def foo" in units[0].source


def test_empty_source_falls_back_to_deterministic():
    # An embedder is provided, but both functions have empty source -> no row to embed,
    # so the pair is scored deterministic-only (embed is None).
    a = _unit("a", ["Module", "Return", "FN", "Call"], {"x"}, "")
    b = _unit("b", ["Module", "Return", "FN", "Call"], {"x"}, "")
    pairs = co.find_overlaps([a, b], threshold=0.0, min_nodes=0, embed_fn=_fake_embedder({}))
    assert len(pairs) == 1
    assert pairs[0].embed is None


# --- CLI smoke (real corpus, no model) ---


def test_cli_audit_json_smoke(capsys):
    # High threshold + min-nodes keep the all-pairs sweep cheap; exercises the full
    # argparse -> build_corpus -> find_overlaps -> JSON path with exit 0.
    rc = co.main(["audit", "--no-embed", "--json", "--threshold", "0.99", "--min-nodes", "200"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list)


# --- embedding backend guard ---


def test_backend_returns_none_without_sentence_transformers(monkeypatch, capsys):
    emb = _load("_overlap_embed")
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)  # force ImportError
    result = emb.get_embedder("test/guard-model-unique-xyz")
    assert result is None
    assert "sentence-transformers not importable" in capsys.readouterr().err
