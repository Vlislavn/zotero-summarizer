"""Part B of the redundancy gate: advisory near-duplicate function detection.

Pure (AST + string only) machinery imported by ``check_redundancy.py`` for the
``clones-sweep`` subcommand. Kept in a sibling module so each file stays under the
500-LOC hook; ``tools/precommit`` is not a package, so ``check_redundancy.py``
inserts this directory on ``sys.path`` before importing it (the test suite loads it
the same way).

Two signals must BOTH agree before a pair is surfaced — this is what keeps the
repo's many parallel-by-design adapters/handlers out of the report:

1. STRUCTURAL identity (cheap, exact, used for bucketing): same AST shape after
   LOCAL names are alpha-renamed to canonical placeholders. Free names, attribute
   names, and call targets are NOT part of the skeleton — they are the semantic
   tokens below. So two functions with the same control-flow shape bucket together
   regardless of which APIs they call.
2. SEMANTIC overlap (Jaccard over the kept free-name/attribute/call-target tokens):
   within a structural bucket, a pair is reported only if its API vocabulary
   overlaps by >= threshold. Skeleton-only twins with disjoint APIs score ~0 and
   are dropped.

The clone sweep is advisory only (``clones-sweep`` always exits 0).
"""
from __future__ import annotations

import ast
import copy
import hashlib
import itertools
from collections import defaultdict
from dataclasses import dataclass

# Functions with fewer normalized AST nodes than this are too trivial to be a
# meaningful clone (one-line getters/forwarders) and only generate noise.
MIN_FUNCTION_NODES = 12


@dataclass(frozen=True)
class FunctionFingerprint:
    """One collected function: location plus both fingerprints."""

    path: str
    qualname: str
    line: int
    skeleton: str
    free_tokens: frozenset[str]
    node_count: int

    @property
    def key(self) -> str:
        """Return the ``path:qualname`` allowlist key for this function."""
        return f"{self.path}:{self.qualname}"


@dataclass(frozen=True)
class ClonePair:
    """One advisory near-duplicate finding: two functions and their Jaccard score."""

    a: FunctionFingerprint
    b: FunctionFingerprint
    score: float

    @property
    def key(self) -> str:
        """Return the order-independent ``a.key::b.key`` allowlist key."""
        first, second = sorted((self.a.key, self.b.key))
        return f"{first}::{second}"


# --- PURE: local-name analysis & normalization ---


def _local_names(func: ast.AST) -> set[str]:
    """Return every name bound locally inside ``func`` (params, assignments, ...).

    ``self``/``cls`` are ordinary first parameters and therefore land here, so two
    methods do not register as clones merely on their ``self.*`` shape.
    """
    locals_: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.arg):
            locals_.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            locals_.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            locals_.add(node.name)
    return locals_


def _normalize(func: ast.AST, locals_: set[str]) -> ast.AST:
    """Return a copy of ``func`` with local names renamed to ``VAR0, VAR1, ...``.

    Renamed nodes are tagged ``_local`` so the structural signature can keep their
    reuse pattern while collapsing free names — which carry semantic identity — to a
    single marker. ``ast.walk`` order is deterministic, so two structurally identical
    functions get identical placeholder assignments.
    """
    norm = copy.deepcopy(func)
    mapping: dict[str, str] = {}
    for node in ast.walk(norm):
        if isinstance(node, ast.Name) and node.id in locals_:
            node.id = mapping.setdefault(node.id, f"VAR{len(mapping)}")
            node._local = True  # type: ignore[attr-defined]
        elif isinstance(node, ast.arg) and node.arg in locals_:
            node.arg = mapping.setdefault(node.arg, f"VAR{len(mapping)}")
            node._local = True  # type: ignore[attr-defined]
    return norm


def structural_tokens(normalized: ast.AST) -> list[str]:
    """Return the ordered structural token stream of an already-normalized function.

    Local placeholders keep their ``VARi`` id (their reuse pattern is structural);
    free names collapse to ``FN`` and attribute names to ``A`` (those are semantic,
    compared by Jaccard, not structure). Operator classes and constant *kinds*
    (not values) are kept so ``a + b`` and ``a - b`` differ but ``return 1`` and
    ``return 2`` do not. ``structural_signature`` hashes this stream for bucketing;
    a graded comparison (e.g. ``SequenceMatcher`` ratio) consumes it directly.
    """
    parts: list[str] = []
    for node in ast.walk(normalized):
        if isinstance(node, ast.Name):
            parts.append("LV:" + node.id if getattr(node, "_local", False) else "FN")
        elif isinstance(node, ast.arg):
            parts.append("LA:" + node.arg if getattr(node, "_local", False) else "FA")
        elif isinstance(node, ast.Constant):
            parts.append("C:" + type(node.value).__name__)
        elif isinstance(node, ast.Attribute):
            parts.append("A")
        elif isinstance(node, (ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.AugAssign)):
            parts.append(type(node).__name__ + ":" + type(node.op).__name__)
        elif isinstance(node, ast.Compare):
            parts.append("Cmp:" + ",".join(type(o).__name__ for o in node.ops))
        else:
            parts.append(type(node).__name__)
    return parts


def structural_signature(normalized: ast.AST) -> str:
    """Return a deterministic structural hash of an already-normalized function.

    The hash buckets structurally identical functions; the underlying token stream
    is exposed by ``structural_tokens`` for graded similarity.
    """
    return hashlib.sha1("|".join(structural_tokens(normalized)).encode("utf-8")).hexdigest()


def semantic_tokens(func: ast.AST, locals_: set[str]) -> frozenset[str]:
    """Return the kept free-name / attribute vocabulary — which APIs ``func`` touches.

    Computed on the ORIGINAL node: free ``Load`` names not bound locally, plus every
    attribute name (covers ``db.execute`` -> ``execute`` and call targets).
    """
    tokens: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in locals_:
            tokens.add(node.id)
        elif isinstance(node, ast.Attribute):
            tokens.add(node.attr)
    return frozenset(tokens)


def fingerprint_function(func: ast.AST, path: str, qualname: str) -> FunctionFingerprint:
    """Return the structural + semantic fingerprint of one function node."""
    locals_ = _local_names(func)
    normalized = _normalize(func, locals_)
    node_count = sum(1 for _ in ast.walk(normalized))
    return FunctionFingerprint(
        path=path,
        qualname=qualname,
        line=getattr(func, "lineno", 0),
        skeleton=structural_signature(normalized),
        free_tokens=semantic_tokens(func, locals_),
        node_count=node_count,
    )


def collect_fingerprints_from_source(source: str, path: str) -> list[FunctionFingerprint]:
    """Parse ``source`` and fingerprint every non-trivial function (incl. methods/nested).

    A ``SyntaxError`` propagates to the caller, which (for the advisory sweep) warns
    and skips rather than failing the commit.
    """
    tree = ast.parse(source)
    records: list[FunctionFingerprint] = []
    _walk_functions(tree, "", records, path)
    return [r for r in records if r.node_count >= MIN_FUNCTION_NODES]


def _is_trivial_function(func: ast.AST) -> bool:
    """Return whether ``func``'s body is a single real statement (after an optional
    docstring). Thin wrappers / named accessors / forwarders (``def x(): return f(...)``)
    are intentional API surface, not meaningful near-duplicates — flagging three parallel
    one-line accessors as "clones" is noise. They never bucket as clones.
    """
    body = list(getattr(func, "body", []))
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]  # drop the docstring
    return len(body) <= 1


def _walk_functions(node: ast.AST, prefix: str, out: list[FunctionFingerprint], path: str) -> None:
    """Recurse the tree, tracking a dotted qualname for classes and functions."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qual = f"{prefix}{child.name}"
            if not _is_trivial_function(child):
                out.append(fingerprint_function(child, path, qual))
            _walk_functions(child, qual + ".", out, path)
        elif isinstance(child, ast.ClassDef):
            _walk_functions(child, f"{prefix}{child.name}.", out, path)
        else:
            _walk_functions(child, prefix, out, path)


# --- PURE: similarity scoring & pairing ---


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Return |A&B| / |A|B| — 1.0 for two empty sets, guarding division by zero."""
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def length_bound_ok(size_a: int, size_b: int, threshold: float) -> bool:
    """Return whether two token sets of these sizes *could* reach the threshold.

    The largest Jaccard achievable from sizes alone is ``min/max`` (smaller set fully
    contained in the larger: |inter| <= min, |union| >= max). If that bound is below
    the threshold the pair cannot qualify, so it is skipped without scoring. This is
    an admissible prune — it never drops a pair whose true Jaccard >= threshold. (The
    spec's ``2*min/(a+b)`` is the looser *Dice* bound; ``min/max`` is the tight bound
    matching the Jaccard metric we actually score.)
    """
    smaller, larger = (size_a, size_b) if size_a <= size_b else (size_b, size_a)
    if larger == 0:
        return True
    return smaller / larger >= threshold


def find_clone_pairs(
    records: list[FunctionFingerprint],
    threshold: float,
    allowlist: set[str],
) -> list[ClonePair]:
    """Bucket by skeleton, length-filter, score by Jaccard, drop allowlisted, sort."""
    buckets: dict[str, list[FunctionFingerprint]] = defaultdict(list)
    for record in records:
        buckets[record.skeleton].append(record)
    pairs: list[ClonePair] = []
    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        for first, second in itertools.combinations(bucket, 2):
            if not length_bound_ok(len(first.free_tokens), len(second.free_tokens), threshold):
                continue
            score = jaccard(first.free_tokens, second.free_tokens)
            if score < threshold:
                continue
            pair = ClonePair(a=first, b=second, score=score)
            if pair.key in allowlist:
                continue
            pairs.append(pair)
    pairs.sort(key=lambda p: (-p.score, p.a.key, p.b.key))
    return pairs
