#!/usr/bin/env python3
"""Identify dead code in the runtime package via two complementary tiers.

Ported from IVAI's ``scripts/dead_code_guard.py`` and reshaped to this repo's
``loc_allowlist.txt`` grandfathering idiom (existing findings are frozen; only
*new* dead code blocks).

Tier 1 — consumer-check (stdlib AST + ``git grep``): a public, top-level,
*undecorated* function/class with no reference inside ``zotero_summarizer/``
beyond its own definition is an orphan. Robust to this repo's dynamic
registration — a FastAPI handler passed to ``router.add_api_route("/p", h)``, a
CLI handler in ``set_defaults(func=_h)`` and an ``__all__ = ["Name"]`` entry all
appear in the grep output, so they count as consumers. ``@mcp.tool()``-decorated
symbols are skipped (the decorator is their real, grep-invisible consumer).
Orphans are grandfathered in ``dead_code_allowlist.txt``.

Tier 2 — vulture-sweep: Vulture over the whole package catches what name-grep
cannot (unused imports/locals, unreachable code). Current findings are
grandfathered in ``vulture_allowlist.txt`` by a *path-anchored* ``<path>:<name>``
key — so a name acknowledged in one module never masks a genuinely-dead symbol
of the same name elsewhere — and a structural guard auto-suppresses pydantic /
``@dataclass`` field declarations (framework-serialised, never read by name) so
they need no per-symbol entry. New high-confidence dead code blocks. The whole
ignore policy lives in ``vulture_argv`` (one source of truth) — ``make scan`` and
the README regeneration route through the subcommands rather than re-typing it.

Subcommands:
  consumer-check [PATHS...]  file-based hook; check staged runtime files
  vulture-sweep              whole-tree hook; Vulture + path-anchored allowlist
  vulture-scan               raw backlog view for ``make scan`` (no allowlist)
  make-allowlist             regenerate vulture_allowlist.txt from current findings
  dump-orphans               bootstrap: print every "path:symbol" orphan to stdout
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from _model_fields import (  # noqa: E402 — same-dir import, script context
    REPO_ROOT,
    RUNTIME_ROOT,
    _all_runtime_python_files,
    model_field_keys,
)
CONSUMER_ALLOWLIST = Path(__file__).with_name("dead_code_allowlist.txt")
VULTURE_ALLOWLIST = Path(__file__).with_name("vulture_allowlist.txt")
VULTURE_BLOCK_CONFIDENCE = 80
VULTURE_ADVISORY_CONFIDENCE = 60
# Framework decorators register their target with a runtime the name-grep (and
# Vulture) cannot see, so a decorated symbol is never "unused": MCP tools +
# resources, pydantic field/model validators, and FastAPI route handlers
# (``@app.*`` / ``@router.*``). Ignoring them kills that whole false-positive
# class at the source instead of grandfathering each one in the whitelist.
VULTURE_IGNORE_DECORATORS = (
    "@mcp.tool,@mcp.resource,@field_validator,@model_validator,@app.*,@router.*"
)
# Names Vulture flags as "unused attribute" but that a library reads for us:
# ``conn.row_factory = sqlite3.Row`` is consumed by sqlite3's C layer, never by
# our code, so it looks dead at every assignment site.
VULTURE_IGNORE_NAMES = "row_factory"
_VULTURE_LINE_RE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+): (?P<message>.+) \((?P<confidence>\d+)% confidence\)$"
)
# A finding's allowlist key is ``<path>:<flagged-name>`` — path-anchored so a
# name acknowledged in one module never masks a genuinely-dead symbol of the
# SAME name elsewhere (the native bare-name whitelist's collision hole), and
# name-based (not line-based) so it survives line drift. Only
# ``unused <kind> '<name>'`` findings are keyable; messages without a symbol
# (e.g. "unreachable code after 'return'") get a non-name key and are never
# grandfathered — unreachable/structural dead code must be removed, not frozen.
_VULTURE_NAME_RE = re.compile(r"^unused \w+ '(?P<name>[^']+)'")


@dataclass(frozen=True)
class PublicSymbol:
    """A public, top-level, undecorated function/class definition."""

    path: str
    line: int
    name: str

    @property
    def key(self) -> str:
        """Return the ``path:symbol`` allowlist key for this definition."""
        return f"{self.path}:{self.name}"


@dataclass(frozen=True)
class VultureFinding:
    """One Vulture dead-code report on a source line."""

    path: str
    line: int
    message: str
    confidence: int


# --- Tier 1: consumer-check (pure helpers) ---


def load_consumer_allowlist(text: str) -> set[str]:
    """Parse ``<path>:<symbol>`` grandfather keys, stripping comments/blanks."""
    keys: set[str] = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        keys.add(line)
    return keys


def public_symbol_definitions(source: str, path: str) -> list[PublicSymbol]:
    """Return top-level public, undecorated function/class definitions.

    A decorated symbol is registered with a framework by its decorator
    (``@mcp.tool()``, event handlers, ...). That registration is a real runtime
    consumer the name-grep cannot see, so do not treat it as a candidate.

    A ``SyntaxError`` propagates: an unparseable runtime file is a real defect,
    not something to silently skip.
    """
    tree = ast.parse(source)
    definitions: list[PublicSymbol] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name.startswith("_"):
                continue
            if node.decorator_list:
                continue
            definitions.append(PublicSymbol(path=path, line=node.lineno, name=node.name))
    return definitions


def symbols_without_consumers(
    definitions: list[PublicSymbol],
    reference_locations_by_symbol: dict[str, list[tuple[str, int]]],
) -> list[PublicSymbol]:
    """Return symbols whose only runtime reference is their own definition."""
    missing: list[PublicSymbol] = []
    for definition in definitions:
        locations = reference_locations_by_symbol.get(definition.name, [])
        has_consumer = any(
            location_path != definition.path or location_line != definition.line
            for location_path, location_line in locations
        )
        if not has_consumer:
            missing.append(definition)
    return missing


# --- Tier 1: git-backed helpers ---


def _reference_locations(symbol: str) -> list[tuple[str, int]]:
    """Return ``(path, line)`` runtime references to ``symbol`` via git grep."""
    result = subprocess.run(
        ["git", "grep", "-n", "-w", "-e", symbol, "--", RUNTIME_ROOT],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    # git grep exits 1 when there are no matches (an orphan) and 0 on a hit;
    # anything >= 2 is a real tool error. Tolerating 1 is load-bearing.
    if result.returncode >= 2:
        raise RuntimeError(f"git grep failed (exit {result.returncode}): {result.stderr.strip()}")
    locations: list[tuple[str, int]] = []
    for entry in result.stdout.splitlines():
        path_text, line_text, _ = entry.split(":", 2)
        locations.append((path_text, int(line_text)))
    return locations


def _runtime_python_files() -> list[str]:
    """Return every tracked runtime ``.py`` file (for the bootstrap sweep)."""
    result = subprocess.run(
        ["git", "ls-files", f"{RUNTIME_ROOT}/**/*.py", f"{RUNTIME_ROOT}/*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _definitions_for(paths: list[str]) -> list[PublicSymbol]:
    """Collect public symbol definitions across the given runtime files."""
    definitions: list[PublicSymbol] = []
    for path in paths:
        source = (REPO_ROOT / path).read_text(encoding="utf-8")
        definitions.extend(public_symbol_definitions(source, Path(path).as_posix()))
    return definitions


def _orphans_for(paths: list[str]) -> list[PublicSymbol]:
    """Return public symbols in ``paths`` with no runtime consumer."""
    definitions = _definitions_for(paths)
    if not definitions:
        return []
    symbols = sorted({definition.name for definition in definitions})
    references = {symbol: _reference_locations(symbol) for symbol in symbols}
    return symbols_without_consumers(definitions, references)


def _consumer_check(paths: list[str]) -> int:
    """Fail when a changed public symbol has no consumer and is not grandfathered."""
    runtime_files = [
        path
        for path in paths
        if path.endswith(".py")
        and Path(path).as_posix().startswith(f"{RUNTIME_ROOT}/")
        and (REPO_ROOT / path).exists()
    ]
    if not runtime_files:
        return 0
    allowlist = (
        load_consumer_allowlist(CONSUMER_ALLOWLIST.read_text())
        if CONSUMER_ALLOWLIST.exists()
        else set()
    )
    orphans = [o for o in _orphans_for(runtime_files) if o.key not in allowlist]
    if not orphans:
        return 0
    sys.stderr.write("Dead code — public symbols with no runtime consumer:\n")
    for orphan in orphans:
        sys.stderr.write(
            f"  - {orphan.path}:{orphan.line} {orphan.name} — no reference inside "
            f"{RUNTIME_ROOT}/. Delete it, or add '{orphan.key}' to "
            f"dead_code_allowlist.txt with a reason.\n"
        )
    return 1


def _dump_orphans() -> int:
    """Print every ``path:symbol`` orphan across the package (bootstrap helper)."""
    for orphan in sorted(_orphans_for(_runtime_python_files()), key=lambda o: o.key):
        print(orphan.key)
    return 0


# --- Tier 2: vulture-sweep ---


def parse_vulture_findings(output: str) -> list[VultureFinding]:
    """Parse Vulture stdout into structured findings, ignoring stray lines."""
    findings: list[VultureFinding] = []
    for raw_line in output.splitlines():
        match = _VULTURE_LINE_RE.match(raw_line.strip())
        if match is None:
            continue
        findings.append(
            VultureFinding(
                path=match.group("path"),
                line=int(match.group("line")),
                message=match.group("message"),
                confidence=int(match.group("confidence")),
            )
        )
    return findings


def split_blocking_advisory(
    findings: list[VultureFinding], block_confidence: int
) -> tuple[list[VultureFinding], list[VultureFinding]]:
    """Partition findings into blocking (>= threshold) and advisory (below)."""
    blocking = [f for f in findings if f.confidence >= block_confidence]
    advisory = [f for f in findings if f.confidence < block_confidence]
    return blocking, advisory


def vulture_argv(min_confidence: int) -> list[str]:
    """Build the Vulture command line from the single-source-of-truth policy.

    EVERY Vulture invocation — the gate sweep, ``make scan``'s raw view and the
    allowlist regeneration — routes through here, so the ignore-decorators /
    ignore-names policy is defined in exactly ONE place and cannot drift between
    the Makefile, this script and the README. Tested at this seam.
    """
    return [
        sys.executable,
        "-m",
        "vulture",
        RUNTIME_ROOT,
        "--min-confidence",
        str(min_confidence),
        "--ignore-decorators",
        VULTURE_IGNORE_DECORATORS,
        "--ignore-names",
        VULTURE_IGNORE_NAMES,
    ]


def _run_vulture(min_confidence: int) -> str:
    """Run Vulture over the runtime package and return its stdout.

    No allowlist file is handed to Vulture: grandfathering is applied in Python
    via path-anchored ``<path>:<name>`` keys, so it can never mask a same-named
    symbol in another module.
    """
    completed = subprocess.run(
        vulture_argv(min_confidence), cwd=REPO_ROOT, capture_output=True, text=True
    )
    # Vulture exits 0 (clean) or 3 (dead code found); anything else is a tool error.
    if completed.returncode not in (0, 3):
        raise RuntimeError(
            f"vulture failed (exit {completed.returncode}): {completed.stderr.strip()}"
        )
    if completed.stderr.strip():
        sys.stderr.write(
            f"[warning] Vulture could not analyze some files (results may be incomplete):\n"
            f"{completed.stderr.strip()}\n"
        )
    return completed.stdout


def vulture_finding_key(finding: VultureFinding) -> str:
    """Return the path-anchored ``<path>:<name>`` allowlist key for a finding.

    Findings whose message carries no symbol (e.g. "unreachable code after
    'return'") get a path+line key that is intentionally awkward to grandfather.
    """
    match = _VULTURE_NAME_RE.match(finding.message)
    token = match.group("name") if match else f"@{finding.line}"
    return f"{finding.path}:{token}"


def filter_allowlisted(
    findings: list[VultureFinding], allowlist: set[str]
) -> list[VultureFinding]:
    """Drop findings whose path-anchored key is grandfathered in the allowlist."""
    return [f for f in findings if vulture_finding_key(f) not in allowlist]


def suppress_model_fields(
    advisory: list[VultureFinding], model_keys: set[str]
) -> list[VultureFinding]:
    """Drop ADVISORY findings that name a model field. The >= block-confidence
    bucket is never passed in here, so this shrinks noise but can never hide
    blocking dead code (real unused imports / unreachable code)."""
    return [f for f in advisory if vulture_finding_key(f) not in model_keys]


def _classified_findings() -> tuple[list[VultureFinding], list[VultureFinding]]:
    """Run Vulture; return (blocking, advisory) after the model-field guard.

    Shared by the gate sweep, the ``make scan`` raw view and the regenerator so
    all three agree on what a "finding" is.
    """
    findings = parse_vulture_findings(_run_vulture(VULTURE_ADVISORY_CONFIDENCE))
    blocking, advisory = split_blocking_advisory(findings, VULTURE_BLOCK_CONFIDENCE)
    advisory = suppress_model_fields(advisory, model_field_keys(_all_runtime_python_files()))
    return blocking, advisory


def _print_findings(findings: list[VultureFinding]) -> None:
    """Print Vulture findings in their original report format."""
    for finding in findings:
        sys.stderr.write(
            f"  - {finding.path}:{finding.line}: {finding.message} ({finding.confidence}% confidence)\n"
        )


def _vulture_sweep() -> int:
    """Block on allowlist-uncovered dead code at or above the block confidence."""
    allowlist = (
        load_consumer_allowlist(VULTURE_ALLOWLIST.read_text())
        if VULTURE_ALLOWLIST.exists()
        else set()
    )
    blocking, advisory = _classified_findings()
    advisory = filter_allowlisted(advisory, allowlist)
    blocking = filter_allowlisted(blocking, allowlist)
    if advisory:
        sys.stderr.write(
            f"[advisory] {len(advisory)} dead-code candidate(s) "
            f"({VULTURE_ADVISORY_CONFIDENCE}-{VULTURE_BLOCK_CONFIDENCE - 1}% confidence):\n"
        )
        _print_findings(advisory)
        sys.stderr.write("  -> review and fix, or grandfather via vulture_allowlist.txt.\n")
    if blocking:
        sys.stderr.write(
            f"Dead code — {len(blocking)} Vulture finding(s) >= "
            f"{VULTURE_BLOCK_CONFIDENCE}% confidence (not grandfathered):\n"
        )
        _print_findings(blocking)
        sys.stderr.write(
            "  -> remove the dead code, or grandfather it in vulture_allowlist.txt "
            "(regenerate: check_dead_code.py make-allowlist) with justification.\n"
        )
        return 1
    return 0


def _vulture_scan() -> int:
    """Print the raw Vulture backlog for ``make scan`` (no allowlist applied).

    Shows every finding the gate would weigh (after the model-field guard),
    grandfathered or not — the backlog to shrink. Always returns 0.
    """
    blocking, advisory = _classified_findings()
    _print_findings(sorted(blocking + advisory, key=lambda f: (f.path, f.line)))
    return 0


def _make_allowlist() -> int:
    """Regenerate vulture_allowlist.txt: print one ``path:name  # why`` per finding.

    Redirect into vulture_allowlist.txt. Model-field declarations are already
    suppressed by the structural guard, so this holds only the genuinely-ambiguous
    residue (e.g. test-only public helpers).
    """
    blocking, advisory = _classified_findings()
    seen: set[str] = set()
    for finding in sorted(blocking + advisory, key=lambda f: (f.path, f.line)):
        key = vulture_finding_key(finding)
        if key in seen:
            continue
        seen.add(key)
        print(f"{key}  # {finding.message} ({finding.path}:{finding.line})")
    return 0


# --- CLI ---


def main(argv: list[str]) -> int:
    """Run the dead-code guard CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    consumer = subparsers.add_parser("consumer-check", help="Check public symbols have consumers")
    consumer.add_argument("paths", nargs="*", help="Files passed by pre-commit")

    subparsers.add_parser("vulture-sweep", help="Gate: Vulture sweep + path-anchored allowlist")
    subparsers.add_parser("vulture-scan", help="Raw Vulture backlog for `make scan` (no allowlist)")
    subparsers.add_parser("make-allowlist", help="Regenerate vulture_allowlist.txt from findings")
    subparsers.add_parser("dump-orphans", help="Print every path:symbol orphan (bootstrap)")

    args = parser.parse_args(argv)
    if args.command == "consumer-check":
        return _consumer_check(args.paths)
    if args.command == "vulture-sweep":
        return _vulture_sweep()
    if args.command == "vulture-scan":
        return _vulture_scan()
    if args.command == "make-allowlist":
        return _make_allowlist()
    if args.command == "dump-orphans":
        return _dump_orphans()
    raise RuntimeError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
