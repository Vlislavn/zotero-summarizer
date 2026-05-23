#!/usr/bin/env python3
"""Every Python package under zotero_summarizer/ must carry a README, and that
README must be re-committed whenever the package's code changes.

Two checks:
  1. PRESENCE  — every directory with __init__.py has a README.md.
  2. FRESHNESS — if a *.py file in package P is staged, P/README.md must be
     staged too. (Touch the code, refresh the doc.)

The freshness check inspects the git index directly, so it ignores the file
list pre-commit passes in.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

PKG_ROOT = pathlib.Path("zotero_summarizer")


def _staged() -> set[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True, text=True, check=True,
    ).stdout
    return {line.strip() for line in out.splitlines() if line.strip()}


def main() -> int:
    failures: list[str] = []

    # 1. PRESENCE
    for init in PKG_ROOT.rglob("__init__.py"):
        if "__pycache__" in init.parts:
            continue
        readme = init.parent / "README.md"
        if not readme.exists():
            failures.append(f"{init.parent.as_posix()}/: missing README.md")

    # 2. FRESHNESS
    staged = _staged()
    touched_pkgs: set[pathlib.Path] = set()
    for s in staged:
        p = pathlib.Path(s)
        if p.suffix == ".py" and p.as_posix().startswith("zotero_summarizer/") and (p.parent / "__init__.py").exists():
            touched_pkgs.add(p.parent)
    for pkg in sorted(touched_pkgs):
        readme = (pkg / "README.md").as_posix()
        if readme not in staged:
            failures.append(
                f"{pkg.as_posix()}/: code changed but {readme} is not staged "
                f"— update the module README to match."
            )

    if failures:
        sys.stderr.write("Module README policy:\n")
        for f in failures:
            sys.stderr.write(f"  - {f}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
