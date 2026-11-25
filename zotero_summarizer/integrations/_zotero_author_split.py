"""Split a *crammed* author entry (a whole author list in one string) into single authors.

Feeds and search hits sometimes deliver every author in one string. Treated as one name it
became one creator with the list in ``firstName``; Zotero sync rejects creator names over
255 characters. Stdlib only, no app imports: ``fix_creators.py`` (the one-off DB repair)
loads this file by path, so the app and the repair share one rule.

Entries shorter than ``LIST_MIN_CHARS`` pass through untouched (no real name is that long).
"""

from __future__ import annotations

import re
from typing import NamedTuple

MAX_NAME_CHARS = 255  # Zotero sync limit for one creator name
LIST_MIN_CHARS = 100

_PARTICLES = frozenset({"de", "van", "von", "der", "den", "da", "di", "le", "la", "du", "del", "dos", "das", "ter", "ten", "bin", "zur", "zu"})
_TITLE_PARTICLES = frozenset({"Van", "Von", "De", "Der", "Den", "Da", "Di", "Del", "Du"})  # only before a surname
_SUFFIXES = frozenset({"Jr", "Jr.", "Sr", "Sr.", "II", "III", "IV", "MD", "PhD"})
_INITIAL = re.compile(r"^(?:[A-Z]\.?-?)+$")
_AFFILIATION = re.compile(
    r"https?|\bror\b|\d|universit|institut|department|\bdept\b|school|hospital|college|cent(?:er|re|ro)\b"
    r"|laborator|faculty|division|academy|medical|medicine|research|clinic|foundation|program"
    r"|\b(?:Korea|China|Japan|India|Taiwan|Germany|France|Italy|Spain|Canada|Australia|Brazil|USA|UK)\b",
    re.IGNORECASE,
)
_NAME_PREFIX = re.compile(r"(?:^|[\s-])(?:Mc|Mac|Da|De|Di|Del|Du|La|Le|Van|Von)$")  # "McNally" is one word


class Split(NamedTuple):
    names: list[str]
    kind: str  # single | comma_list | initials_pairs | space_run | glued | clipped
    lossy: bool  # text was dropped (affiliations, URLs) or clipped to the limit


def clip(text: str, limit: int = MAX_NAME_CHARS) -> str:
    """Cut at a word boundary so the result is at most ``limit`` characters."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    head, space, _ = cut.rpartition(" ")
    return (head if space and head else cut).strip()


def split_name(name: str) -> tuple[str, str, int]:
    """Split one author string into (firstName, lastName, fieldMode); each part ≤255 chars.

    Zotero's fieldMode=1 means single-name entry (lastName-only, used for
    institutions, single-name authors). Otherwise fieldMode=0 with split.
    """
    text = name.strip()
    if not text:
        return ("", "", 1)
    if "," in text:  # "Last, First" form
        last, _, first = text.partition(",")
        return (clip(first), clip(last), 0)
    parts = text.rsplit(None, 1)  # "First Last" form — split on last whitespace
    if len(parts) == 2:
        return (clip(parts[0]), clip(parts[1]), 0)
    return ("", clip(text), 1)  # single token — institution/single-name


def split_author_entry(entry: str) -> Split:
    """Return the individual authors in ``entry`` (one string for an ordinary single author)."""
    text = entry.strip()
    if len(text) < LIST_MIN_CHARS:
        return Split([text] if text else [], "single", False)
    if "," in text:
        names, kind = _comma_names(text)
        return Split([clip(n) for n in names], kind, False)
    segments = _glued_segments(text)
    if len(segments) > 2:
        return _glued_names(text, segments)
    return Split([clip(n) for n in _space_run(text.split())], "space_run", False)


def _is_initials(part: str) -> bool:
    tokens = part.split()
    return bool(tokens) and all(len(t) <= 4 and _INITIAL.match(t) for t in tokens)


def _comma_names(text: str) -> tuple[list[str], str]:
    """``A B, C D`` → full names; ``Last, F., Last, F.`` → ``Last, F.`` pairs."""
    parts = [p.strip() for p in text.split(",") if p.strip()]
    names: list[str] = []
    paired = False
    i = 0
    while i < len(parts):
        part = parts[i]
        if part in _SUFFIXES and names:
            names[-1] += f" {part}"
        elif i + 1 < len(parts) and not _is_initials(part) and _is_initials(parts[i + 1]):
            names.append(f"{part}, {parts[i + 1]}")
            paired = True
            i += 1
        else:
            names.append(part)
        i += 1
    return names, "initials_pairs" if paired else "comma_list"


def _space_run(tokens: list[str]) -> list[str]:
    """``First [M.] Last First Last`` with no separators: one name per given(+initials)/surname run."""
    names: list[str] = []
    i = 0
    while i < len(tokens):
        cur = [tokens[i]]
        i += 1
        while i < len(tokens) and _is_initials(tokens[i]):
            cur.append(tokens[i])
            i += 1
        while i < len(tokens) and (tokens[i] in _PARTICLES or tokens[i] in _TITLE_PARTICLES):
            cur.append(tokens[i])
            i += 1
        if i < len(tokens):
            cur.append(tokens[i])
            i += 1
        while i + 1 < len(tokens) and tokens[i] in _PARTICLES:
            cur += [tokens[i], tokens[i + 1]]
            i += 2
        while i < len(tokens) and tokens[i] in _SUFFIXES:
            cur.append(tokens[i])
            i += 1
        names.append(" ".join(cur))
    return names


def _glued_segments(text: str) -> list[str]:
    """Cut ``ColwellLaura`` style joins at lower→Upper boundaries (not inside ``McNally``)."""
    segments: list[str] = []
    start = 0
    for pos in range(1, len(text)):
        if text[pos - 1].islower() and text[pos].isupper() and not _NAME_PREFIX.search(text[start:pos]):
            segments.append(text[start:pos])
            start = pos
    segments.append(text[start:])
    return [s.strip() for s in segments if s.strip()]


def _well_formed(segment: str) -> bool:
    return 2 <= len(segment.split()) <= 6 and len(segment) <= 60 and not _AFFILIATION.search(segment)


def _glued_names(text: str, segments: list[str]) -> Split:
    """Keep the first run of person-like segments; affiliations/URLs after it are dropped."""
    flags = [_well_formed(s) for s in segments]
    if not any(flags):
        return Split([clip(text)], "clipped", True)
    start = flags.index(True)
    end = start
    while end < len(segments) and flags[end]:
        end += 1
    return Split([clip(s) for s in segments[start:end]], "glued", True)
