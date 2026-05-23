#!/usr/bin/env python3
"""Fail if any staged Python file exceeds the 500-LOC limit.

A small set of legacy files predates the limit; they are grandfathered in
``loc_allowlist.txt`` with a frozen ceiling. Grandfathered files may shrink
but must NOT grow — the only way to clear them is to split them up. New files
get the hard 500-line cap with no exceptions.
"""
from __future__ import annotations

import pathlib
import sys

LIMIT = 500
ALLOWLIST = pathlib.Path(__file__).with_name("loc_allowlist.txt")


def _load_allowlist() -> dict[str, int]:
    ceilings: dict[str, int] = {}
    if not ALLOWLIST.exists():
        return ceilings
    for line in ALLOWLIST.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        path, ceiling = line.rsplit(None, 1)
        ceilings[path] = int(ceiling)
    return ceilings


def main(paths: list[str]) -> int:
    ceilings = _load_allowlist()
    failures: list[str] = []
    for raw in paths:
        path = pathlib.Path(raw)
        if path.suffix != ".py" or not path.exists():
            continue
        loc = len(path.read_text().splitlines())
        ceiling = ceilings.get(path.as_posix())
        if ceiling is not None:
            if loc > ceiling:
                failures.append(
                    f"{path}: {loc} LOC exceeds its grandfathered ceiling {ceiling}. "
                    f"Legacy files may not grow — split it into smaller modules."
                )
        elif loc > LIMIT:
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
