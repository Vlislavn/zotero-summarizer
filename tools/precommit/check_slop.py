#!/usr/bin/env python3
"""Catch AI-authored slop the other gates miss (Tier 7 of the guardrail family).

Adopts aislop's deterministic slop/dead-code detectors (github scanaislop/aislop)
into this repo's stdlib-only pre-commit style: rules encode a PRINCIPLE not a snippet,
every false positive is killed by a shape-level GUARD, AST is preferred over text, and
a detector never silently no-ops. Subcommands: ``slop-check [PATHS]`` (file hook, BLOCK
new unambiguous slop), ``slop-sweep`` (whole-tree, ADVISE), ``dump`` (seed the allowlist).

SOUNDNESS is the prime directive. Only ONE rule BLOCKs — a committed ``breakpoint()`` /
``pdb.set_trace()`` (runtime has zero). Everything heuristic is ADVISE and all current
findings are grandfathered in ``slop_allowlist.txt`` so only NEW slop surfaces. Per-rule
severity is config-overridable via ``slop_severity.txt`` (``rule=off|advise|block``).
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _slop_text as text  # noqa: E402  (sibling module; tools/precommit is not a package)

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = "zotero_summarizer"
ALLOWLIST = Path(__file__).with_name("slop_allowlist.txt")
SEVERITY_CONFIG = Path(__file__).with_name("slop_severity.txt")

BLOCK = "block"
ADVISE = "advise"

# Default (editorial) severity per rule. Only the unambiguous, zero-judgment
# behaviour defect blocks; everything heuristic advises (config-overridable below).
SEVERITY: dict[str, str] = {
    "slop/debug-leftover": BLOCK,
    "slop/swallowed-exception": ADVISE,
    "slop/silent-recovery": ADVISE,
    "slop/mutable-default": ADVISE,
    "slop/todo-stub": ADVISE,
    "slop/comment-slop": ADVISE,
    "slop/generic-naming": ADVISE,
    "slop/function-too-long": ADVISE,
    "slop/too-many-params": ADVISE,
    "slop/deep-nesting": ADVISE,
}

_DEBUGGERS = frozenset({"pdb", "ipdb", "pudb"})
_LOG_METHODS = frozenset({"debug", "info", "warning", "warn", "error", "exception", "critical", "log"})
_MUTABLE_CTORS = frozenset({"list", "dict", "set", "defaultdict", "OrderedDict", "Counter"})
_MUTATORS = frozenset(
    {"append", "extend", "insert", "update", "add", "discard", "pop", "popitem", "clear", "setdefault", "sort"}
)
_INTENTIONAL_IGNORE = frozenset({"_", "_e", "_err", "_ex", "_exc", "ignored", "ignore", "unused"})
_GENERIC_EXACT = frozenset(
    {"foo", "bar", "baz", "qux", "quux", "corge", "grault", "garply", "waldo", "fred", "plugh", "xyzzy", "thud", "blah", "asdf", "foobar"}
)
# Body code-lines / required-params / control-nesting thresholds (aislop defaults),
# each with a 1.1x soft buffer to tolerate off-by-one.
_MAX_BODY_LINES = 80
_MAX_PARAMS = 6
_MAX_NESTING = 5


@dataclass(frozen=True)
class Diagnostic:
    """One slop finding; ``severity`` decides BLOCK vs ADVISE."""

    path: str
    line: int
    column: int
    rule: str
    severity: str
    category: str
    message: str

    @property
    def key(self) -> str:
        """Return the ``path:line:rule`` allowlist key."""
        return f"{self.path}:{self.line}:{self.rule}"


# --- PURE helpers ---


def module_shadowed_names(tree: ast.AST) -> set[str]:
    """Return names REBOUND in the module (def/class/assign/param) — imports excluded.

    The shadow guard for builtin-name rules must NOT count ``import pdb`` or
    ``from collections import Counter`` as shadowing: those bind the real object,
    which is exactly the slop we want to catch (a committed ``pdb.set_trace()``, an
    imported ``Counter()`` mutable default). Only a non-import rebinding (a local
    ``list = ...`` or ``def breakpoint(...)``) genuinely changes the name's meaning.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
    return names


def is_generic_name(name: str) -> bool:
    """Return whether ``name`` is a metasyntactic / numbered-stem placeholder."""
    low = name.lower()
    if low in _GENERIC_EXACT:
        return True
    return re.fullmatch(r"(?:helper|util|handler|thingy?)_?\d+", low) is not None


def _is_noop_stmt(stmt: ast.stmt) -> bool:
    """Return whether a statement is a no-op (pass / ... / bare string)."""
    if isinstance(stmt, ast.Pass):
        return True
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and (stmt.value.value is Ellipsis or isinstance(stmt.value.value, str))
    )


def handler_swallows(handler: ast.ExceptHandler) -> bool:
    """Return whether an except body is purely no-op (swallows the error)."""
    if not all(_is_noop_stmt(stmt) for stmt in handler.body):
        return False
    return handler.name not in _INTENTIONAL_IGNORE  # `except E as _:` is a documented ignore


def _is_logging_call(call: ast.Call) -> bool:
    return isinstance(call.func, ast.Attribute) and call.func.attr in _LOG_METHODS


def handler_drops_context(handler: ast.ExceptHandler) -> bool:
    """Return whether a handler only logs WITHOUT carrying the exception's context."""
    effectful = [stmt for stmt in handler.body if not _is_noop_stmt(stmt)]
    if not effectful or not all(
        isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call) and _is_logging_call(stmt.value)
        for stmt in effectful
    ):
        return False
    for stmt in effectful:
        call = stmt.value  # type: ignore[attr-defined]
        if call.func.attr == "exception":  # logs the active traceback
            return False
        if any(kw.arg in {"exc_info", "stack_info"} for kw in call.keywords):
            return False
        if len(call.args) > 1:  # carries a dynamic context arg beyond the format string
            return False
    if handler.name and any(isinstance(n, ast.Name) and n.id == handler.name for n in ast.walk(handler)):
        return False
    return True


def is_debug_entry(call: ast.Call, bound: set[str]) -> str | None:
    """Return a debugger name if ``call`` is an interactive-debugger entry, else None."""
    func = call.func
    if isinstance(func, ast.Name) and func.id == "breakpoint" and "breakpoint" not in bound:
        return "breakpoint()"
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "set_trace"
        and isinstance(func.value, ast.Name)
        and func.value.id in _DEBUGGERS
    ):
        # receiver named pdb/ipdb/pudb + .set_trace() is unambiguous; an unrelated
        # object's .set_trace() has a different receiver name, so no bound-check needed.
        return f"{func.value.id}.set_trace()"
    return None


def is_mutable_default(node: ast.expr, bound: set[str]) -> bool:
    """Return whether a default-arg node is a mutable container."""
    if isinstance(node, (ast.List, ast.Dict, ast.Set)):
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _MUTABLE_CTORS
        and not node.args
        and not node.keywords
        and node.func.id not in bound
    )


def param_mutated(func: ast.AST, name: str) -> bool:
    """Return whether parameter ``name`` is mutated in the body (the actual bug)."""
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == name
            and node.func.attr in _MUTATORS
        ):
            return True
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == name and isinstance(node.ctx, (ast.Store, ast.Del)):
            return True
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return True
    return False


def default_pairs(func) -> list[tuple[str, ast.expr]]:
    """Return (param_name, default_node) for every parameter that has a default."""
    spec = func.args
    positional = spec.posonlyargs + spec.args
    pairs = list(zip([p.arg for p in positional[len(positional) - len(spec.defaults):]], spec.defaults))
    pairs += [(p.arg, d) for p, d in zip(spec.kwonlyargs, spec.kw_defaults) if d is not None]
    return pairs


def required_params(func) -> int:
    """Count required params, excluding self/cls, defaulted, and *args/**kwargs."""
    spec = func.args
    positional = spec.posonlyargs + spec.args
    pos_required = len(positional) - len(spec.defaults)
    if positional and positional[0].arg in {"self", "cls"}:
        pos_required -= 1
    return max(0, pos_required) + sum(1 for d in spec.kw_defaults if d is None)


def body_code_lines(func, lines: list[str]) -> int:
    """Count non-blank, non-comment body lines, excluding the docstring and the
    continuation lines of multi-line string literals (embedded SQL/prompts are data,
    not control-flow, so they must not inflate the function-length signal)."""
    body = func.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    if not body:
        return 0
    start = body[0].lineno
    end = max(getattr(stmt, "end_lineno", stmt.lineno) for stmt in body)
    data: set[int] = set()
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and getattr(node, "end_lineno", 0) > node.lineno:
                data.update(range(node.lineno + 1, node.end_lineno + 1))
    return sum(
        1
        for num in range(start, end + 1)
        if num not in data
        and (s := (lines[num - 1].strip() if num - 1 < len(lines) else ""))
        and not s.startswith("#")
    )


def nesting_depth(body: list[ast.stmt], current: int = 0) -> int:
    """Max control-flow nesting; an elif chain counts as one level (not N)."""
    best = current
    for stmt in body:
        if isinstance(stmt, ast.If):
            best = max(best, nesting_depth(stmt.body, current + 1))
            orelse = stmt.orelse
            while len(orelse) == 1 and isinstance(orelse[0], ast.If):  # elif: same level
                best = max(best, nesting_depth(orelse[0].body, current + 1))
                orelse = orelse[0].orelse
            if orelse:
                best = max(best, nesting_depth(orelse, current + 1))
        elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
            best = max(best, nesting_depth(stmt.body, current + 1), nesting_depth(stmt.orelse, current + 1))
        elif isinstance(stmt, ast.Try):
            best = max(best, nesting_depth(stmt.body, current + 1), nesting_depth(stmt.orelse, current + 1), nesting_depth(stmt.finalbody, current + 1))
            for handler in stmt.handlers:
                best = max(best, nesting_depth(handler.body, current + 1))
    return best


def _is_flat_data_block(func, n_params: int) -> bool:
    """Spare flat declarative bodies: a data-shuttle call, or a low-branch builder block."""
    effectful = [s for s in func.body if not _is_noop_stmt(s)]
    if effectful and isinstance(effectful[0], ast.Expr) and isinstance(effectful[0].value, ast.Constant):
        effectful = effectful[1:]
    # data shuttle: one statement that is a call passing >= n_params arguments through.
    if len(effectful) <= 2:
        for stmt in effectful:
            call = stmt.value if isinstance(stmt, (ast.Return, ast.Expr)) else None
            if isinstance(call, ast.Call) and len(call.args) + len(call.keywords) >= max(1, n_params):
                return True
    # flat builder: shallow nesting and mostly bare calls (e.g. argparse registration).
    calls = sum(1 for s in effectful if isinstance(s, (ast.Expr, ast.Assign)) and isinstance(getattr(s, "value", None), ast.Call))
    return nesting_depth(func.body) <= 1 and effectful and calls / len(effectful) >= 0.6


# --- the AST visitor ---


class _SlopVisitor(ast.NodeVisitor):
    """Walk a module collecting AST-based slop diagnostics."""

    def __init__(self, path: str, bound: set[str], lines: list[str]) -> None:
        self.path = path
        self._bound = bound
        self._lines = lines
        self.diags: list[Diagnostic] = []

    def _add(self, node: ast.AST, rule: str, message: str, category: str = "ai-slop") -> None:
        self.diags.append(
            Diagnostic(self.path, node.lineno, getattr(node, "col_offset", 0), rule, SEVERITY[rule], category, message)
        )

    def visit_Try(self, node: ast.Try) -> None:
        for handler in node.handlers:
            if handler_swallows(handler):
                self._add(handler, "slop/swallowed-exception", "except handler silently swallows the error")
            elif handler_drops_context(handler):
                self._add(handler, "slop/silent-recovery", "except logs without the exception's context")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        entry = is_debug_entry(node, self._bound)
        if entry is not None:
            self._add(node, "slop/debug-leftover", f"interactive debugger left in source: {entry}")
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if is_generic_name(node.name):
            self._add(node, "slop/generic-naming", f"placeholder name: {node.name}")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node)
        self.generic_visit(node)

    def _function(self, node) -> None:
        if is_generic_name(node.name):
            self._add(node, "slop/generic-naming", f"placeholder name: {node.name}")
        for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
            if is_generic_name(arg.arg):
                self._add(arg, "slop/generic-naming", f"placeholder parameter: {arg.arg}")
        for name, default in default_pairs(node):
            if is_mutable_default(default, self._bound) and param_mutated(node, name):
                self._add(default, "slop/mutable-default", f"mutable default for '{name}' is shared across calls")
        self._complexity(node)

    def _complexity(self, node) -> None:
        n_params = required_params(node)
        if n_params > _MAX_PARAMS and not _is_flat_data_block(node, n_params):
            self._add(node, "slop/too-many-params", f"{n_params} required parameters (> {_MAX_PARAMS})", "code-quality")
        if body_code_lines(node, self._lines) > _MAX_BODY_LINES * 1.1 and not _is_flat_data_block(node, n_params):
            self._add(node, "slop/function-too-long", f"function body > {_MAX_BODY_LINES} code lines", "code-quality")
        if nesting_depth(node.body) > _MAX_NESTING:
            self._add(node, "slop/deep-nesting", f"control flow nests > {_MAX_NESTING} deep", "code-quality")


def find_slop(source: str, path: str) -> list[Diagnostic]:
    """Return every slop diagnostic in ``source``. ``SyntaxError`` propagates."""
    tree = ast.parse(source)
    lines = source.splitlines()
    visitor = _SlopVisitor(path, module_shadowed_names(tree), lines)
    visitor.visit(tree)
    diags = list(visitor.diags)
    for line, col, rule, message in text.scan_todos(source) + text.scan_comments(source):
        diags.append(Diagnostic(path, line, col, rule, SEVERITY[rule], "comments", message))
    return sorted(diags, key=lambda d: (d.line, d.rule, d.column))


# --- config + git helpers (I/O) ---


def load_allowlist(content: str) -> set[str]:
    """Parse grandfather keys, stripping comments/blanks (shared gate idiom)."""
    keys: set[str] = set()
    for line in content.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            keys.add(line)
    return keys


def load_severity_overrides(content: str) -> dict[str, str]:
    """Parse ``rule=off|advise|block`` overrides (aislop rule-severity model)."""
    overrides: dict[str, str] = {}
    for line in content.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        rule, _, value = line.partition("=")
        value = value.strip().lower()
        if value not in {"off", ADVISE, BLOCK}:
            raise ValueError(f"slop_severity.txt: bad severity '{value}' for '{rule.strip()}'")
        overrides[rule.strip()] = value
    return overrides


def _load_config() -> tuple[set[str], dict[str, str]]:
    allow = load_allowlist(ALLOWLIST.read_text(encoding="utf-8")) if ALLOWLIST.exists() else set()
    overrides = load_severity_overrides(SEVERITY_CONFIG.read_text(encoding="utf-8")) if SEVERITY_CONFIG.exists() else {}
    return allow, overrides


def _effective(diag: Diagnostic, overrides: dict[str, str]) -> str:
    """Return the config-resolved severity ('off' to drop) for a diagnostic."""
    return overrides.get(diag.rule, diag.severity)


def _runtime_python_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", f"{RUNTIME_ROOT}/**/*.py", f"{RUNTIME_ROOT}/*.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _scan(paths: list[str]) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    for path in paths:
        source = (REPO_ROOT / path).read_text(encoding="utf-8")
        diags.extend(find_slop(source, Path(path).as_posix()))
    return diags


def _print(diags: list[Diagnostic]) -> None:
    for diag in diags:
        sys.stderr.write(f"  - {diag.path}:{diag.line} {diag.rule} — {diag.message}\n")


# --- subcommands ---


def _slop_check(paths: list[str]) -> int:
    """Block new unambiguous slop (debug-leftover) in staged runtime files."""
    runtime = [
        p for p in paths
        if p.endswith(".py") and Path(p).as_posix().startswith(f"{RUNTIME_ROOT}/") and (REPO_ROOT / p).exists()
    ]
    if not runtime:
        return 0
    allow, overrides = _load_config()
    blocking, advisory = [], []
    for diag in _scan(runtime):
        if diag.key in allow:
            continue
        severity = _effective(diag, overrides)
        if severity == "off":
            continue
        (blocking if severity == BLOCK else advisory).append(diag)
    if advisory:
        sys.stderr.write(f"[advisory] {len(advisory)} slop finding(s) (not blocking):\n")
        _print(advisory)
    if blocking:
        sys.stderr.write(f"Slop — {len(blocking)} blocking finding(s) (not grandfathered):\n")
        _print(blocking)
        sys.stderr.write("  -> remove it, or add '<path>:<line>:<rule>' to slop_allowlist.txt with a reason.\n")
        return 1
    return 0


def _slop_sweep() -> int:
    """Advisory whole-tree slop sweep (always exits 0)."""
    allow, overrides = _load_config()
    findings = [
        d for d in _scan(_runtime_python_files())
        if d.key not in allow and _effective(d, overrides) != "off"
    ]
    if findings:
        sys.stderr.write(f"[advisory] {len(findings)} slop finding(s) across the tree:\n")
        _print(findings)
        sys.stderr.write("  -> clean up, or grandfather in slop_allowlist.txt. Advisory only.\n")
    return 0


def _dump() -> int:
    """Print every current finding's key (bootstrap the allowlist)."""
    for key in sorted({d.key for d in _scan(_runtime_python_files())}):
        print(key)
    return 0


def main(argv: list[str]) -> int:
    """Run the slop gate CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("slop-check", help="Block new unambiguous slop")
    check.add_argument("paths", nargs="*", help="Files passed by pre-commit")
    subparsers.add_parser("slop-sweep", help="Advisory whole-tree slop sweep")
    subparsers.add_parser("dump", help="Print every finding key (bootstrap)")

    args = parser.parse_args(argv)
    if args.command == "slop-check":
        return _slop_check(args.paths)
    if args.command == "slop-sweep":
        return _slop_sweep()
    if args.command == "dump":
        return _dump()
    raise RuntimeError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
