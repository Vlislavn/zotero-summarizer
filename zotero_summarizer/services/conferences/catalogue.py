"""Ready-made conference recipes (the only conference knowledge in code).

A recipe says where the list comes from and how a stored record's session level is
derived. Level rules run at READ time over stored facts, so changing a rule needs no
new download. A record no rule matches is ``unknown`` — never guessed as "poster".
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from zotero_summarizer.integrations.crossref import CrossrefHit

LATE_BREAKING = "late-breaking"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Recipe:
    label: str
    issn: str
    levels: tuple[tuple[re.Pattern[str], str], ...]  # (regex on the number, level), first match wins
    records_per_day: int  # preflight estimate only: records Crossref holds per day around the meeting
    abstract_available: bool = True  # False: the source lists titles only (ESMO), the UI says "title only"
    doi_keep: re.Pattern[str] | None = None  # DOI of a meeting abstract; group 1 is its abstract number
    title_number: re.Pattern[str] | None = None  # no DOI rule: the number opens the title (group 1)
    page_keep: re.Pattern[str] | None = None  # title rule only: the Crossref page must match too
    withdrawn: re.Pattern[str] | None = None  # title of an abstract withdrawn before the meeting
    title_strip: re.Pattern[str] | None = None  # prefix removed from the title when shown


_ESMO_NUMBER = re.compile(r"^(LBA\d+|\d+(?:e?TiP|e?P|RO|MO|O))\s")  # 5P, 1295P, 593eP, 12MO, 2613O, LBA1
_ESMO_WITHDRAWN = re.compile(r"has been removed because it was not presented", re.IGNORECASE)
_LBA = ((re.compile(r"^LBA", re.IGNORECASE), LATE_BREAKING),)

RECIPES: dict[str, Recipe] = {
    "asco": Recipe(
        label="ASCO Annual Meeting",
        issn="1527-7755",  # Journal of Clinical Oncology; meeting abstracts are its ``_suppl`` DOIs
        doi_keep=re.compile(r"_suppl\.(.+)$", re.IGNORECASE),
        levels=_LBA,
        records_per_day=210,  # 7 332 records over a 5-week window (design spec, probe)
    ),
    "aacr": Recipe(
        label="AACR Annual Meeting",
        issn="1538-7445",  # Cancer Research; ``am2026-lb333``, ``am2026-5512`` (special conferences use other suffixes)
        doi_keep=re.compile(r"1538-7445\.am\d{4}-(.+)$", re.IGNORECASE),
        levels=((re.compile(r"^lb", re.IGNORECASE), LATE_BREAKING),),
        title_strip=re.compile(r"^Abstract\s+\S+?:\s*"),
        records_per_day=125,  # 7 549 records over 61 days
    ),
    "esmo": Recipe(
        label="ESMO Congress",
        issn="0923-7534",  # Annals of Oncology; dated by print month (2025: 2025-09), titles only
        title_number=_ESMO_NUMBER, page_keep=re.compile(r"^S\d"), withdrawn=_ESMO_WITHDRAWN, title_strip=_ESMO_NUMBER,
        levels=_LBA, abstract_available=False,
        records_per_day=180,  # 5 466 records in one month (2026)
    ),
    "esmo_ai": Recipe(
        label="ESMO AI & Digital Oncology",
        issn="2949-8201",  # ESMO Real World Data and Digital Oncology; the congress is one volume, one month
        title_number=_ESMO_NUMBER, withdrawn=_ESMO_WITHDRAWN, title_strip=_ESMO_NUMBER,
        levels=_LBA, abstract_available=False,
        records_per_day=15,  # 379 records in 2026-11
    ),
}


def abstract_number(recipe: Recipe, hit: CrossrefHit) -> str | None:
    """The abstract number of a meeting abstract, or ``None`` for any other row of the journal
    (an ordinary article, a withdrawn abstract)."""
    if recipe.withdrawn and recipe.withdrawn.search(hit.title):
        return None
    if recipe.doi_keep:
        match = recipe.doi_keep.search(hit.doi)
    else:
        match = recipe.title_number.match(hit.title) if recipe.title_number else None
        if match and recipe.page_keep and not recipe.page_keep.match(hit.page):
            return None
    return match.group(1) if match else None


def shown_title(recipe: Recipe, title: str) -> str:
    """The title without the number the source prefixes to it (the number is shown on its own)."""
    return recipe.title_strip.sub("", title, count=1).strip() if recipe.title_strip else title


def classify(recipe: Recipe, number: str) -> tuple[str, str]:
    """``(level, why)`` for an abstract number; ``why`` names the rule or says none matched."""
    for pattern, level in recipe.levels:
        if pattern.search(number):
            return level, f"from number {number} (rule {pattern.pattern})"
    return UNKNOWN, "no rule matched" if number else "no abstract number"
