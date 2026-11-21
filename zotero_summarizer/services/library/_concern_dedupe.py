"""Deterministic, model-free clean-up of a review's concern list (distinct and true).

Kept OUT of ``quality_eval``/``propose`` on purpose: ``_review_identity`` hashes the
prompt/contract text of those modules, so a regex added there would mark every stored
review outdated. This is projection/assembly logic, not prompt text, so it lives here.
"""
from __future__ import annotations

import re

_STOP = frozenset(
    "the and for with are was were been this that these those its their from than but only also into via such "
    "may can does did has have had which while when where whose per over under between across within "
    "limiting limits limit reproducibility verification independent".split())
# A concern that LEADS with "no code / no data release" (anchored: "…no prompts, code, or config given"
# inside a reproducibility remark is not this claim).
_NO_CODE = re.compile(
    r"^\s*(?:no|without|lacks?)\s+(?:(?:mention of|any|explicit|concrete|public|open[- ]source|source|released?)\s+)*"
    r"(?:code|data release)\b|^\s*(?:source )?code\b[^.;]{0,40}\b(?:not|never)\b[^.;]{0,15}"
    r"\b(?:shared|released|available|provided|public)\b", re.I)


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us")) else w for w in words if len(w) > 2 and w not in _STOP}


def _same_concern(a: set[str], b: set[str]) -> bool:
    """Token overlap |A∩B| / min(|A|,|B|) >= 0.6, or >= 0.5 with >= 12 shared tokens (long paraphrases)."""
    if not a or not b:
        return False
    shared = len(a & b)
    cover = shared / min(len(a), len(b))
    return cover >= 0.6 or (cover >= 0.5 and shared >= 12)


def _merge_once(items: list[str]) -> list[str]:
    kept: list[str] = []
    for item in items:
        toks = _tokens(item)
        i = next((i for i, k in enumerate(kept) if _same_concern(toks, _tokens(k))), None)
        if i is None:
            kept.append(item)
        elif len(item) > len(kept[i]):
            kept[i] = item
    return kept


def dedupe_concerns(items: list[str], *, code_found: bool = False) -> list[str]:
    """Make a concern list distinct and true — deterministic, no model.

    Samples/depth passes phrase one concern several ways, so: (1) a concern that overlaps another
    (``_same_concern``) is a duplicate and the longest wording (the most specific) is kept in the
    first one's position — repeated until stable, since keeping a longer wording can newly match a
    third; (2) with ``code_found`` a concern leading with "no code / no data release" is dropped
    (before merging, so a longer wording cannot absorb it): the review found the code."""
    kept = [c for c in dict.fromkeys(c.strip() for c in items if c and c.strip())
            if not (code_found and _NO_CODE.search(c))]
    while len(merged := _merge_once(kept)) < len(kept):
        kept = merged
    return kept

