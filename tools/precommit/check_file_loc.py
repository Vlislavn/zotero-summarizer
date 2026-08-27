#!/usr/bin/env python3
"""Fail if any staged Python file exceeds the hard 500-LOC limit."""
from __future__ import annotations

import pathlib
import sys

LIMIT = 500


def main(paths: list[str]) -> int:
    failures: list[str] = []
    for raw in paths:
        path = pathlib.Path(raw)
        if path.suffix != ".py" or not path.exists():
            continue
        loc = len(path.read_text().splitlines())
        if loc > LIMIT:
            failures.append(
                f"{path}: {loc} LOC exceeds the {LIMIT}-line limit. "
                f"Split it into focused modules (one clear responsibility each)."
            )
    if failures:
        sys.stderr.write("File LOC limit (max 500):\n")
        for f in failures:
            sys.stderr.write(f"  - {f}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
