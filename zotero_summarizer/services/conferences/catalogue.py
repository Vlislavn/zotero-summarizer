"""Ready-made conference recipes (the only conference knowledge in code).

A recipe says where the list comes from and how a stored record's session level is
derived. Level rules run at READ time over stored facts, so changing a rule needs no
new download. A record no rule matches is ``unknown`` — never guessed as "poster".
"""
from __future__ import annotations

import re
from dataclasses import dataclass

LATE_BREAKING = "late-breaking"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Recipe:
    label: str
    issn: str
    doi_keep: re.Pattern[str]  # DOI of a meeting abstract; the group is its abstract number
    levels: tuple[tuple[re.Pattern[str], str], ...]  # (regex on the number, level), first match wins
    records_per_day: int  # preflight estimate only: records Crossref holds per day around the meeting


RECIPES: dict[str, Recipe] = {
    "asco": Recipe(
        label="ASCO Annual Meeting",
        issn="1527-7755",  # Journal of Clinical Oncology; meeting abstracts are its ``_suppl`` DOIs
        doi_keep=re.compile(r"_suppl\.(.+)$", re.IGNORECASE),
        levels=((re.compile(r"^LBA", re.IGNORECASE), LATE_BREAKING),),
        records_per_day=210,  # 7 332 records over a 5-week window (design spec, probe)
    ),
}


def classify(recipe: Recipe, number: str) -> tuple[str, str]:
    """``(level, why)`` for an abstract number; ``why`` names the rule or says none matched."""
    for pattern, level in recipe.levels:
        if pattern.search(number):
            return level, f"from number {number} (rule {pattern.pattern})"
    return UNKNOWN, "no rule matched" if number else "no abstract number"
