#!/usr/bin/env python3
"""On-demand, all-pairs, semantic function-overlap audit (advisory — not a gate).

Sibling of ``check_redundancy.py``. Where Tier 6's ``clones-sweep`` buckets functions
by an EXACT structural hash and only compares same-skeleton pairs, this audit compares
**every function against every function** across the whole runtime tree and ranks them
by a hybrid similarity, so it surfaces consolidation candidates whose *intent* overlaps
even when the control-flow shape or the API differs.

One subcommand:

  audit   whole-tree, all-pairs, ranked overlap report (always exits 0)

Per pair it blends three signals: an embedding cosine over the function source (the
semantic signal, from a local code-embedding model), a graded structural-token
similarity, and an API-vocabulary Jaccard. The embedding backend is dependency-injected
(``_overlap_embed.get_embedder``) so this module imports torch-free and tests pass a
fake embedder; when the model is unavailable the audit degrades to the two deterministic
signals (announced on stderr). It is NEVER wired into pre-commit/CI/``make deadcode`` and
ALWAYS returns 0 — run it by hand via ``make overlaps``.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _redundancy_clones as clones  # noqa: E402  (sibling module; dir is not a package)

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = "zotero_summarizer"

DEFAULT_THRESHOLD = 0.55  # below the 0.75 clone bar — this audit favours recall
DEFAULT_TOP = 50          # 0 = all
DEFAULT_MIN_NODES = 20    # skip trivial getters/forwarders

# Combine weights (fixed — the system owns this complexity, not the user; Tesler):
# embedding is weighted highest because it is the signal that catches the *semantic*
# overlap the deterministic signals miss.
HYBRID_EMBED, HYBRID_STRUCT, HYBRID_API = 0.5, 0.3, 0.2
DET_STRUCT, DET_API = 0.6, 0.4


@dataclass(frozen=True)
class FunctionUnit:
    """One function: location, structural tokens, API vocabulary, and its source text."""

    path: str
    qualname: str
    line: int
    struct_tokens: tuple[str, ...]
    free_tokens: frozenset[str]
    source: str

    @property
    def node_count(self) -> int:
        """Return the normalized-AST node count (one structural token per node)."""
        return len(self.struct_tokens)


@dataclass(frozen=True)
class OverlapPair:
    """One ranked overlap finding: two functions and their per-signal + combined scores."""

    a: FunctionUnit
    b: FunctionUnit
    struct: float
    api: float
    embed: float | None
    combined: float


# --- PURE: corpus build (functions with source) ---


def _unit(func: ast.AST, source: str, path: str, qualname: str) -> FunctionUnit:
    """Fingerprint one function node, reusing the Tier 6 clone primitives."""
    locals_ = clones._local_names(func)
    normalized = clones._normalize(func, locals_)
    segment = ast.get_source_segment(source, func)  # type: ignore[arg-type]
    return FunctionUnit(
        path=path,
        qualname=qualname,
        line=getattr(func, "lineno", 0),
        struct_tokens=tuple(clones.structural_tokens(normalized)),
        free_tokens=clones.semantic_tokens(func, locals_),
        source=segment or "",  # None -> "" : empty source = deterministic-only scoring
    )


def _walk_units(node: ast.AST, prefix: str, out: list[FunctionUnit], source: str, path: str) -> None:
    """Recurse the tree, tracking a dotted qualname for classes and functions."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qual = f"{prefix}{child.name}"
            out.append(_unit(child, source, path, qual))
            _walk_units(child, qual + ".", out, source, path)
        elif isinstance(child, ast.ClassDef):
            _walk_units(child, f"{prefix}{child.name}.", out, source, path)
        else:
            _walk_units(child, prefix, out, source, path)


def collect_units(source: str, path: str) -> list[FunctionUnit]:
    """Parse ``source`` and return a unit for every function (incl. methods/nested).

    ``SyntaxError`` propagates: an unparseable runtime file is a real defect.
    """
    tree = ast.parse(source)
    units: list[FunctionUnit] = []
    _walk_units(tree, "", units, source, path)
    return units


# --- PURE: scoring ---


def combine(struct: float, api: float, embed: float | None) -> float:
    """Return the combined similarity for one pair under the active weighting."""
    if embed is None:
        return DET_STRUCT * struct + DET_API * api
    return HYBRID_EMBED * embed + HYBRID_STRUCT * struct + HYBRID_API * api


def _embed_units(units: list[FunctionUnit], embed_fn) -> tuple[dict[int, int], object] | None:
    """Embed every non-empty-source unit once; return (index->row map, cosine matrix).

    Rows are L2-normalized, so the cosine matrix is ``V @ V.T``. ``None`` when there is
    no embedder or no embeddable source. numpy is a guaranteed transitive dependency
    of the embedding stack, so the matmul path is the only path (no speculative fallback).
    """
    if embed_fn is None:
        return None
    indices = [i for i, unit in enumerate(units) if unit.source]
    if not indices:
        return None
    import numpy as np

    matrix = np.asarray(embed_fn([units[i].source for i in indices]), dtype=float)
    similarity = matrix @ matrix.T
    return {full: row for row, full in enumerate(indices)}, similarity


def _cosine(vectors: tuple[dict[int, int], object] | None, i: int, j: int) -> float | None:
    """Return the precomputed cosine for units ``i``/``j``, or None if either was masked."""
    if vectors is None:
        return None
    positions, similarity = vectors
    if i not in positions or j not in positions:
        return None
    return float(similarity[positions[i]][positions[j]])  # type: ignore[index]


def find_overlaps(
    units: list[FunctionUnit],
    *,
    threshold: float,
    min_nodes: int,
    embed_fn,
    changed: set[str] | None = None,
) -> list[OverlapPair]:
    """Return every function pair with combined similarity >= ``threshold``, ranked desc.

    All-pairs over the upper triangle (symmetric dedup). The exact ``api`` and ``embed``
    are cheap, so they are taken as-is and only ``struct`` is bounded: difflib's
    ``real_quick_ratio`` >= ``quick_ratio`` >= ``ratio`` give successively tighter sound
    upper bounds, and the expensive ``ratio`` is computed only for pairs that could still
    reach the threshold. The prune never drops a pair whose true score >= threshold.

    ``changed`` (diff mode): when given, only pairs where at least one function lives in a
    changed file are scored — i.e. "where do *my* changed functions overlap anything in the
    corpus" — while the other side still ranges over the whole tree.
    """
    units = [unit for unit in units if unit.node_count >= min_nodes]
    vectors = _embed_units(units, embed_fn)
    pairs: list[OverlapPair] = []
    for i in range(len(units)):
        for j in range(i + 1, len(units)):
            a, b = units[i], units[j]
            if changed is not None and a.path not in changed and b.path not in changed:
                continue
            embed = _cosine(vectors, i, j)
            api = clones.jaccard(a.free_tokens, b.free_tokens)
            matcher = difflib.SequenceMatcher(None, a.struct_tokens, b.struct_tokens, autojunk=False)
            if combine(matcher.real_quick_ratio(), api, embed) < threshold:
                continue  # cheap O(1) size bound on struct
            if combine(matcher.quick_ratio(), api, embed) < threshold:
                continue  # cheap O(n) multiset bound on struct
            struct = matcher.ratio()
            combined = combine(struct, api, embed)
            if combined >= threshold:
                pairs.append(OverlapPair(a=a, b=b, struct=struct, api=api, embed=embed, combined=combined))
    pairs.sort(key=lambda p: (-p.combined, p.a.path, p.a.line, p.b.path, p.b.line))
    return pairs


# --- report ---


def format_report(pairs: list[OverlapPair], embed_enabled: bool, reason: str) -> str:
    """Render the ranked, aligned human report (one line per pair)."""
    header = f"Function-overlap audit — {len(pairs)} pair(s) at or above threshold"
    if not embed_enabled:
        header += f"  (deterministic-only — {reason})"
    lines = [header]
    for pair in pairs:
        embed_text = f"{pair.embed:.2f}" if pair.embed is not None else "n/a"
        lines.append(
            f"  {pair.combined:.3f}  {pair.a.path}:{pair.a.line} '{pair.a.qualname}'  <->  "
            f"{pair.b.path}:{pair.b.line} '{pair.b.qualname}'   "
            f"[struct {pair.struct:.2f} · api {pair.api:.2f} · embed {embed_text}]"
        )
    if not pairs:
        lines.append("  (none — try a lower --threshold or a larger --min-nodes window)")
    return "\n".join(lines)


def pairs_to_json(pairs: list[OverlapPair]) -> list[dict]:
    """Render the same findings as JSON-serializable records."""
    return [
        {
            "combined": round(pair.combined, 4),
            "struct": round(pair.struct, 4),
            "api": round(pair.api, 4),
            "embed": round(pair.embed, 4) if pair.embed is not None else None,
            "a": {"path": pair.a.path, "line": pair.a.line, "qualname": pair.a.qualname},
            "b": {"path": pair.b.path, "line": pair.b.line, "qualname": pair.b.qualname},
        }
        for pair in pairs
    ]


# --- I/O + CLI ---


def _runtime_python_files() -> list[str]:
    """Return every tracked runtime ``.py`` file."""
    result = subprocess.run(
        ["git", "ls-files", f"{RUNTIME_ROOT}/**/*.py", f"{RUNTIME_ROOT}/*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def build_corpus() -> list[FunctionUnit]:
    """Collect every runtime function (with source) for the all-pairs audit."""
    units: list[FunctionUnit] = []
    for path in _runtime_python_files():
        source = (REPO_ROOT / path).read_text(encoding="utf-8")
        units.extend(collect_units(source, path))
    return units


def _audit(args: argparse.Namespace) -> int:
    """Run the all-pairs overlap audit and print the ranked report. Always returns 0."""
    embed_fn = None
    reason = "embeddings disabled (--no-embed)"
    if not args.no_embed:
        import _overlap_embed  # lazy: keeps this module torch-free at import

        model = args.model or _overlap_embed.DEFAULT_MODEL
        embed_fn = _overlap_embed.get_embedder(model)
        if embed_fn is None:
            reason = "embedding model unavailable (see message above)"
    units = build_corpus()
    changed = {Path(p).as_posix() for p in args.changed} if args.changed else None
    pairs = find_overlaps(
        units, threshold=args.threshold, min_nodes=args.min_nodes, embed_fn=embed_fn, changed=changed
    )
    if args.top and args.top > 0:
        pairs = pairs[: args.top]
    if args.json:
        print(json.dumps(pairs_to_json(pairs), indent=2))
    else:
        print(format_report(pairs, embed_fn is not None, reason))
    return 0


def main(argv: list[str]) -> int:
    """Run the overlap-audit CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit = subparsers.add_parser("audit", help="All-pairs semantic overlap audit (advisory)")
    audit.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="min combined score")
    audit.add_argument("--top", type=int, default=DEFAULT_TOP, help="cap rows (0 = all)")
    audit.add_argument("--min-nodes", type=int, default=DEFAULT_MIN_NODES, dest="min_nodes", help="skip tiny functions")
    audit.add_argument("--no-embed", action="store_true", help="deterministic-only (struct+api)")
    audit.add_argument("--model", default=None, help="sentence-transformers model id (default: code model)")
    audit.add_argument("--json", action="store_true", help="emit JSON instead of the human report")
    audit.add_argument(
        "--changed",
        nargs="*",
        default=None,
        help="diff mode: only pairs touching these changed files (other side = whole tree)",
    )

    args = parser.parse_args(argv)
    if args.command == "audit":
        return _audit(args)
    raise RuntimeError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
