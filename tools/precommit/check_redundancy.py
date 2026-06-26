#!/usr/bin/env python3
"""Identify redundant transforms and near-duplicate functions (Tier 6 of the gate).

Sibling of ``check_dead_code.py`` in the guardrail family. Two subcommands:

  transforms-check [PATHS...]  file-based hook; BLOCK new provably-redundant transforms
  clones-sweep                 whole-tree hook; ADVISE near-duplicate functions
  dump                         bootstrap: print every blocking ``path:line:kind`` key

Part A (this file) is the BLOCK tier; Part B (the clone machinery) lives in the
sibling ``_redundancy_clones.py``, imported by path because ``tools/precommit`` is not
a package.

SOUNDNESS is the prime directive: a flag must be a behaviour-PRESERVING redundancy, so
a false positive can never pressure a behaviour-changing edit. Where redundancy cannot
be proven from the AST alone — ``dict(list(x))`` (mapping-vs-pairs dispatch),
``set(sorted(x))`` (orderability precondition), ``abs(abs(x))`` (user ``__abs__``) — the
finding is ADVISORY (printed, exit-neutral) rather than blocking, mirroring the
vulture-sweep block/advisory split. The simple-call guard and the binding-scope guard
are the two false-positive firewalls.
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _redundancy_clones as clones  # noqa: E402  (sibling module; dir is not a package)

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = "zotero_summarizer"
ALLOWLIST = Path(__file__).with_name("redundancy_allowlist.txt")
CLONE_THRESHOLD = 0.75

BLOCK = "block"
ADVISE = "advise"

# Builtins whose RETURN TYPE is a guaranteed fixpoint of the call, so f(f(x)) == f(x)
# for every input (a sorted list is already sorted, a set of a set is the same set).
IDEMPOTENT_BLOCK = frozenset(
    {"sorted", "set", "frozenset", "list", "tuple", "dict", "str", "int", "float", "bool"}
)
# abs/round dispatch to user-overridable __abs__/__round__, which are not guaranteed
# idempotent for custom types, so their self-application is advisory, not blocking.
IDEMPOTENT_ADVISE = frozenset({"abs", "round"})

# list/tuple are faithful iterable materializers (preserve order AND multiplicity).
MATERIALIZER_INNERS = frozenset({"list", "tuple"})
# Outers that consume the whole iterable uniformly and add no precondition, so
# outer(materializer(x)) == outer(x) is provable from the AST.
SAFE_ROUNDTRIP_OUTERS = frozenset({"list", "tuple", "sorted"})
# dict dispatches mapping-vs-pairs; set/frozenset can raise mid-iteration. Their
# collapse over a materializer inner is only conditionally valid -> advisory.
ADVISE_ROUNDTRIP_OUTERS = frozenset({"dict", "set", "frozenset"})
# set(sorted(x))/frozenset(sorted(x)): sorted imposes a total-order precondition the
# set outer doesn't (raises on unorderable-but-hashable elements) -> advisory.
ADVISE_SORTED_OUTERS = frozenset({"set", "frozenset"})


@dataclass(frozen=True)
class Finding:
    """One redundant-transform finding; ``severity`` decides whether it blocks."""

    path: str
    line: int
    kind: str
    severity: str

    @property
    def key(self) -> str:
        """Return the ``path:line:kind`` allowlist key."""
        return f"{self.path}:{self.line}:{self.kind}"


# --- PURE: call-shape helpers (the false-positive firewall) ---


def is_simple_call(node: ast.Call) -> bool:
    """Return whether ``node`` is a single bare positional-arg call.

    Exactly one positional arg, no keywords (covers ``**kw``), no ``*args``. This is
    what keeps ``sorted(sorted(rows, key=a), key=b)`` and ``round(round(x, 2), 4)``
    out of the idempotent/round-trip rules — those carry behaviour-altering args.
    """
    return (
        len(node.args) == 1
        and not node.keywords
        and not any(isinstance(a, ast.Starred) for a in node.args)
    )


def called_name(node: ast.Call) -> str | None:
    """Return the callee name only for a bare-``Name`` call, else ``None``.

    ``obj.method(...)`` and aliased calls cannot be proven to be the builtin, so they
    resolve to ``None`` and are never flagged.
    """
    return node.func.id if isinstance(node.func, ast.Name) else None


def bound_names(tree: ast.AST) -> set[str]:
    """Return every name bound anywhere in the module (the binding-scope guard).

    A builtin-rule name that is also defined, assigned, imported, or used as a
    parameter/target here may not be the builtin (``def sorted``,
    ``import OrderedDict as dict``, a param named ``set``), so its findings are
    suppressed. A sound over-approximation that errs toward under-flagging.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return names


def roundtrip_severity(outer: str, inner: str) -> str | None:
    """Return the severity of a collapsible round-trip ``outer(inner(x))``, or None.

    See the constant tables for the soundness rationale behind each branch.
    """
    if outer == inner:
        return None
    if inner in MATERIALIZER_INNERS:
        if outer in SAFE_ROUNDTRIP_OUTERS:
            return BLOCK
        if outer in ADVISE_ROUNDTRIP_OUTERS:
            return ADVISE
    if inner == "sorted" and outer in ADVISE_SORTED_OUTERS:
        return ADVISE
    return None


def is_identity_map(node: ast.Call) -> bool:
    """Return whether ``node`` is ``map(lambda x: x, it)`` (the identity map)."""
    if len(node.args) != 2 or node.keywords or any(isinstance(a, ast.Starred) for a in node.args):
        return False
    lam = node.args[0]
    if not isinstance(lam, ast.Lambda):
        return False
    spec = lam.args
    if spec.posonlyargs or spec.kwonlyargs or spec.vararg or spec.kwarg or spec.defaults:
        return False
    if len(spec.args) != 1:
        return False
    return isinstance(lam.body, ast.Name) and lam.body.id == spec.args[0].arg


def _sole_plain_generator(node: ast.AST) -> ast.comprehension | None:
    """Return the lone non-filtering, non-async comprehension generator, or None."""
    gens = node.generators  # type: ignore[attr-defined]
    if len(gens) != 1:
        return None
    gen = gens[0]
    if gen.ifs or gen.is_async:
        return None
    return gen


def _is_identity_dict_comp(node: ast.DictComp, gen: ast.comprehension) -> bool:
    """Return whether ``node`` is ``{k: v for k, v in d.items()}`` (identity dict)."""
    if not (isinstance(gen.target, ast.Tuple) and len(gen.target.elts) == 2):
        return False
    key_target, val_target = gen.target.elts
    if not (isinstance(key_target, ast.Name) and isinstance(val_target, ast.Name)):
        return False
    if not (isinstance(node.key, ast.Name) and isinstance(node.value, ast.Name)):
        return False
    if node.key.id != key_target.id or node.value.id != val_target.id:
        return False
    return (
        isinstance(gen.iter, ast.Call)
        and isinstance(gen.iter.func, ast.Attribute)
        and gen.iter.func.attr == "items"
    )


# --- PURE: the transform visitor ---


class _TransformVisitor(ast.NodeVisitor):
    """Walk a module, collecting redundant-transform findings."""

    def __init__(self, path: str, bound: set[str]) -> None:
        self.path = path
        self._bound = bound
        self.findings: list[Finding] = []

    def _active(self, name: str | None) -> bool:
        """Return whether ``name`` is a usable builtin reference (not shadowed)."""
        return name is not None and name not in self._bound

    def _add(self, node: ast.AST, kind: str, severity: str) -> None:
        self.findings.append(Finding(self.path, node.lineno, kind, severity))

    def visit_Call(self, node: ast.Call) -> None:
        name = called_name(node)
        if name == "map" and self._active("map"):
            if is_identity_map(node):
                self._add(node, "identity-map", ADVISE)
        elif is_simple_call(node) and self._active(name):
            self._check_nested(node, name)  # type: ignore[arg-type]
        self.generic_visit(node)

    def _check_nested(self, node: ast.Call, outer: str) -> None:
        arg = node.args[0]
        if not isinstance(arg, ast.Call) or not is_simple_call(arg):
            return
        inner = called_name(arg)
        if not self._active(inner):
            return
        if outer == inner:
            if outer in IDEMPOTENT_BLOCK:
                self._add(node, "idempotent", BLOCK)
            elif outer in IDEMPOTENT_ADVISE:
                self._add(node, "idempotent", ADVISE)
            return
        severity = roundtrip_severity(outer, inner)  # type: ignore[arg-type]
        if severity is not None:
            self._add(node, "round-trip", severity)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._check_sequence_comp(node, node.elt, BLOCK)
        self.generic_visit(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._check_sequence_comp(node, node.elt, BLOCK)
        self.generic_visit(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        # A genexp is a lazy one-shot iterator with no eager equivalent -> advisory.
        self._check_sequence_comp(node, node.elt, ADVISE)
        self.generic_visit(node)

    def _check_sequence_comp(self, node: ast.AST, elt: ast.expr, severity: str) -> None:
        gen = _sole_plain_generator(node)
        if gen is None or not isinstance(gen.target, ast.Name):
            return
        if isinstance(elt, ast.Name) and elt.id == gen.target.id:
            self._add(node, "identity-comp", severity)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        gen = _sole_plain_generator(node)
        if gen is not None and _is_identity_dict_comp(node, gen):
            self._add(node, "identity-comp", BLOCK)
        self.generic_visit(node)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> None:
        # Involutions over the numeric domain: -(-x) and ~~x. Not/UAdd are excluded
        # (truthiness coercion / overloadable __pos__ are not no-ops).
        if (
            isinstance(node.op, (ast.USub, ast.Invert))
            and isinstance(node.operand, ast.UnaryOp)
            and type(node.operand.op) is type(node.op)
        ):
            self._add(node, "involution", BLOCK)
        self.generic_visit(node)


def find_transforms(source: str, path: str) -> list[Finding]:
    """Return every redundant-transform finding in ``source``, sorted by location.

    ``SyntaxError`` propagates: an unparseable runtime file is a real defect, not
    something to silently skip (matches ``public_symbol_definitions``).
    """
    tree = ast.parse(source)
    visitor = _TransformVisitor(path, bound_names(tree))
    visitor.visit(tree)
    return sorted(visitor.findings, key=lambda finding: (finding.line, finding.kind))


# --- allowlist + git helpers (I/O) ---


def load_allowlist(text: str) -> set[str]:
    """Parse grandfather keys, stripping comments/blanks (same idiom as dead-code)."""
    keys: set[str] = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            keys.add(line)
    return keys


def _load_allowlist() -> set[str]:
    """Load the grandfather set, or empty when the file is absent."""
    return load_allowlist(ALLOWLIST.read_text(encoding="utf-8")) if ALLOWLIST.exists() else set()


def _runtime_python_files() -> list[str]:
    """Return every tracked runtime ``.py`` file (for whole-tree subcommands)."""
    result = subprocess.run(
        ["git", "ls-files", f"{RUNTIME_ROOT}/**/*.py", f"{RUNTIME_ROOT}/*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


# --- subcommands ---


def _print_findings(findings: list[Finding]) -> None:
    for finding in findings:
        sys.stderr.write(f"  - {finding.path}:{finding.line} {finding.kind} ({finding.severity})\n")


def _transforms_check(paths: list[str]) -> int:
    """Block new provably-redundant transforms in staged runtime files."""
    runtime_files = [
        path
        for path in paths
        if path.endswith(".py")
        and Path(path).as_posix().startswith(f"{RUNTIME_ROOT}/")
        and (REPO_ROOT / path).exists()
    ]
    if not runtime_files:
        return 0
    allow = _load_allowlist()
    blocking: list[Finding] = []
    advisory: list[Finding] = []
    for path in runtime_files:
        source = (REPO_ROOT / path).read_text(encoding="utf-8")
        for finding in find_transforms(source, Path(path).as_posix()):
            if finding.severity == ADVISE:
                advisory.append(finding)
            elif finding.key not in allow:
                blocking.append(finding)
    if advisory:
        sys.stderr.write(
            f"[advisory] {len(advisory)} conditionally-redundant transform(s) "
            "(simplify only if the input types make it safe):\n"
        )
        _print_findings(advisory)
    if blocking:
        sys.stderr.write(
            f"Redundant transforms — {len(blocking)} provably-redundant (not grandfathered):\n"
        )
        _print_findings(blocking)
        sys.stderr.write(
            "  -> rewrite to the single equivalent form, or add '<path>:<line>:<kind>' "
            "to redundancy_allowlist.txt with a reason.\n"
        )
        return 1
    return 0


def _clones_sweep() -> int:
    """Advisory whole-tree near-duplicate function sweep (always returns 0)."""
    allow = _load_allowlist()
    records: list[clones.FunctionFingerprint] = []
    for path in _runtime_python_files():
        source = (REPO_ROOT / path).read_text(encoding="utf-8")
        records.extend(clones.collect_fingerprints_from_source(source, path))
    pairs = clones.find_clone_pairs(records, CLONE_THRESHOLD, allow)
    if pairs:
        sys.stderr.write(
            f"[advisory] {len(pairs)} near-duplicate function pair(s) "
            f"(Jaccard >= {CLONE_THRESHOLD}):\n"
        )
        for pair in pairs:
            sys.stderr.write(
                f"  - {pair.a.path}:{pair.a.line} {pair.a.qualname} <~> "
                f"{pair.b.path}:{pair.b.line} {pair.b.qualname} (J={pair.score:.2f})\n"
            )
        sys.stderr.write("  -> consider extracting a shared helper. Advisory only.\n")
    return 0


def _dump() -> int:
    """Print every grandfatherable key — blocking transforms + current clone pairs.

    Seeds ``redundancy_allowlist.txt`` so only *new* findings surface (the repo's
    grandfathering idiom; goal: shrink to empty). Blocking transform keys are
    ``path:line:kind``; clone-pair keys are ``path:qualname::path:qualname``.
    """
    keys: set[str] = set()
    records: list[clones.FunctionFingerprint] = []
    for path in _runtime_python_files():
        source = (REPO_ROOT / path).read_text(encoding="utf-8")
        for finding in find_transforms(source, path):
            if finding.severity == BLOCK:
                keys.add(finding.key)
        records.extend(clones.collect_fingerprints_from_source(source, path))
    for pair in clones.find_clone_pairs(records, CLONE_THRESHOLD, set()):
        keys.add(pair.key)
    for key in sorted(keys):
        print(key)
    return 0


def main(argv: list[str]) -> int:
    """Run the redundancy gate CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("transforms-check", help="Block new redundant transforms")
    check.add_argument("paths", nargs="*", help="Files passed by pre-commit")
    subparsers.add_parser("clones-sweep", help="Advisory near-duplicate function sweep")
    subparsers.add_parser("dump", help="Print every blocking path:line:kind key (bootstrap)")

    args = parser.parse_args(argv)
    if args.command == "transforms-check":
        return _transforms_check(args.paths)
    if args.command == "clones-sweep":
        return _clones_sweep()
    if args.command == "dump":
        return _dump()
    raise RuntimeError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
