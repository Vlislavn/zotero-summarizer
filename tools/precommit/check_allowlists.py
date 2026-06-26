#!/usr/bin/env python3
"""Reconcile the grandfather allowlists against live detector output.

A grandfather entry is STALE when its target no longer appears in the detector's
current findings — the symbol was deleted/renamed, the file moved, or the code
was fixed — so the entry now grandfathers nothing and silently rots (worse, a
``path:line`` entry can drift onto unrelated code). Nothing else notices: the
gate only ever asks "is this NEW finding grandfathered?", never "does every
grandfather still correspond to a finding?". This reconciler closes that loop.

For each key-based allowlist it computes the LIVE key set from that detector's
own ``dump`` and reports every committed key with no live finding. Wired into
``make scan`` (advisory) and asserted by the test suite, so the allowlists can
only shrink toward empty, never accrete dead grandfathers.

  reconcile   print stale entries per allowlist; exit 1 if any are stale
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]

_CHECK_DEAD = "tools/precommit/check_dead_code.py"
_CHECK_REDUNDANCY = "tools/precommit/check_redundancy.py"
_CHECK_SLOP = "tools/precommit/check_slop.py"

# (label, committed allowlist file, argv that dumps the LIVE keys). The
# loc_allowlist is excluded by design: its keys are ``<path> <ceiling>`` with
# grow-not-shrink semantics, not a finding dump.
RECONCILERS = [
    ("dead_code_allowlist.txt", HERE / "dead_code_allowlist.txt", [_CHECK_DEAD, "dump-orphans"]),
    ("vulture_allowlist.txt", HERE / "vulture_allowlist.txt", [_CHECK_DEAD, "make-allowlist"]),
    ("redundancy_allowlist.txt", HERE / "redundancy_allowlist.txt", [_CHECK_REDUNDANCY, "dump"]),
    ("slop_allowlist.txt", HERE / "slop_allowlist.txt", [_CHECK_SLOP, "dump"]),
]


def parse_keys(text: str) -> set[str]:
    """Parse one grandfather key per non-comment line (key = text before ``#``)."""
    keys: set[str] = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            keys.add(line)
    return keys


def live_keys(dump_output: str) -> set[str]:
    """Live keys = the first whitespace-delimited token of each dumped line.

    Every detector's dump leads with its key (``path:symbol`` / ``path:name`` /
    ``path:line:rule`` / ``path:qual::path:qual``), optionally followed by a
    ``# why`` comment, so the first token is the key regardless of detector.
    """
    return {line.split()[0] for line in dump_output.splitlines() if line.strip()}


def find_stale(committed: set[str], live: set[str]) -> set[str]:
    """Return committed keys with no live finding — the stale grandfathers."""
    return committed - live


def _dump(argv: list[str]) -> str:
    """Run a detector dump subcommand and return its stdout (dumps exit 0)."""
    completed = subprocess.run(
        [sys.executable, *argv], cwd=REPO_ROOT, capture_output=True, text=True
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{argv} failed (exit {completed.returncode}): {completed.stderr.strip()}"
        )
    return completed.stdout


def _reconcile() -> int:
    """Report stale grandfathers across every key-based allowlist."""
    stale_total = 0
    for label, allowlist_path, dump_argv in RECONCILERS:
        committed = parse_keys(allowlist_path.read_text()) if allowlist_path.exists() else set()
        if not committed:
            continue
        stale = sorted(find_stale(committed, live_keys(_dump(dump_argv))))
        if not stale:
            continue
        stale_total += len(stale)
        sys.stderr.write(f"Stale grandfathers in {label} ({len(stale)}):\n")
        for key in stale:
            sys.stderr.write(f"  - {key} — no live finding; delete this dead entry.\n")
    if stale_total:
        sys.stderr.write(
            f"\n{stale_total} stale allowlist entr(y/ies) grandfather nothing — remove them "
            "(regenerate the allowlist from its dump). Goal: shrink to empty.\n"
        )
        return 1
    return 0


def main(argv: list[str]) -> int:
    """Run the allowlist reconciler CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("reconcile", help="Report stale allowlist entries (committed but not live)")
    args = parser.parse_args(argv)
    if args.command == "reconcile":
        return _reconcile()
    raise RuntimeError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
