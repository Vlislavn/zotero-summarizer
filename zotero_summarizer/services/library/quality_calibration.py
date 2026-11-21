"""Cohen's kappa for two nominal label sequences (used by ``tools/bench_paper_quality.py``)."""
from __future__ import annotations

from collections import Counter


def cohen_kappa(a: list[str], b: list[str]) -> float | None:
    """Cohen's kappa for two equal-length nominal label sequences. ``None`` when
    undefined: no data, mismatched lengths, or a single constant category (chance
    agreement == 1, so kappa is degenerate)."""
    n = len(a)
    if n == 0 or n != len(b):
        return None
    labels = sorted(set(a) | set(b))
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum((ca[lab] / n) * (cb[lab] / n) for lab in labels)
    if pe >= 1.0:
        return None
    return round((po - pe) / (1.0 - pe), 3)


__all__ = ["cohen_kappa"]
