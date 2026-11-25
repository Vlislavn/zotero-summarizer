"""Library papers for one Obsidian note: the note → an English query → meaning search → filters.

Pure text logic; the caller supplies the search and the local model (``cli/_candidates.py``).
Filters, in order: papers the note already links or was already offered (the section's hidden
``seen`` comment), papers whose deep review graded them ``D``, then the local model's yes/no
"useful for this note". The yes/no wording is the one that scored best against the owner-note
gold set (2026-11-25: 87 % of kept papers relevant vs 72.6 % for the unfiltered top 10).
"""
from __future__ import annotations

import re
from typing import Any, Callable, Iterable

SECTION = "## Кандидаты из Zotero"
SEEN_PREFIX = "<!-- zotero-candidates seen:"
_NOTE_CHARS = 1500
_ABSTRACT_CHARS = 1200
_KEY = r"[A-Z0-9]{8}"
_DROP_GRADES = {"D"}

TRANSLATE_PROMPT = """Write one English search query (at most 40 words) that finds research papers \
for this research note. Keep the note's specific claim and question; ignore section names \
(Open questions, Relevance to my work, Supports, Challenges, See also), template hints and personal remarks.
Output only the query.

NOTE TITLE: {title}
NOTE TEXT: {text}"""

USEFUL_PROMPT = """You judge whether a research paper is relevant to a researcher's concept note.
The note's title is its claim.
Would this paper be useful for the note, either directly about its claim or as related background?
Say no only if it is off-topic or merely shares a buzzword.

NOTE TITLE: {title}
NOTE TEXT: {text}

PAPER: {paper}
ABSTRACT: {abstract}

Answer yes or no."""


def _without_section(text: str) -> str:
    start = text.find(SECTION)
    if start < 0:
        return text
    nxt = re.search(r"^## ", text[start + len(SECTION):], re.M)
    return text[:start] + (text[start + len(SECTION) + nxt.start():] if nxt else "")


def note_text(markdown: str) -> str:
    """The owner's prose: no frontmatter, comments, code, fields, URLs or offered papers. Headings stay:
    dropping them lowered the yes/no judge on the gold set (89.2 % vs 91.0 %)."""
    text = re.sub(r"\A---\n.*?\n---\n", "", markdown, flags=re.S)
    text = _without_section(text)
    text = re.sub(r"<!--.*?-->|```.*?```", " ", text, flags=re.S)
    text = re.sub(r"^\s*[\w -]+::.*$", " ", text, flags=re.M)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"https?://\S+|zotero://\S+", " ", text)
    text = re.sub(r"[#>*_`|]+", " ", text)
    return " ".join(text.split())[:_NOTE_CHARS]


def known_keys(markdown: str) -> set[str]:
    """Zotero keys the note links anywhere plus the keys its section already offered."""
    keys = set(re.findall(rf"items/({_KEY})", markdown))
    for line in markdown.splitlines():
        if line.startswith(SEEN_PREFIX):
            keys.update(re.findall(rf"\b{_KEY}\b", line[len(SEEN_PREFIX):]))
    return keys


def _clean(answer: str) -> str:
    return re.sub(r"<think>.*?</think>", "", answer, flags=re.S).strip()


def query_from_answer(answer: str) -> str:
    return " ".join(_clean(answer).strip('"').split())


def is_yes(answer: str) -> bool:
    match = re.search(r"\b(yes|no)\b", _clean(answer).lower())
    return bool(match) and match.group(1) == "yes"


def useful_prompt(title: str, text: str, item: dict[str, Any]) -> str:
    abstract = item.get("abstract") or item.get("abstract_preview") or ""
    return USEFUL_PROMPT.format(title=title, text=text, paper=item.get("title") or "",
                                abstract=" ".join(str(abstract).split())[:_ABSTRACT_CHARS])


def select(items: Iterable[dict[str, Any]], known: set[str], useful: Callable[[dict[str, Any]], bool],
           limit: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Up to ``limit`` papers in search order that pass every filter, and how many each filter dropped."""
    chosen: list[dict[str, Any]] = []
    dropped = {"known": 0, "grade_d": 0, "not_useful": 0}
    for item in items:
        if len(chosen) == limit:
            break
        if item["item_key"] in known:
            dropped["known"] += 1
        elif str(item.get("quality_grade") or "") in _DROP_GRADES:
            dropped["grade_d"] += 1
        elif not useful(item):
            dropped["not_useful"] += 1
        else:
            chosen.append(item)
    return chosen, dropped


def _escape(value: Any) -> str:
    return re.sub(r"([\[\]])", r"\\\1", " ".join(str(value or "").split()))


def link_line(item: dict[str, Any]) -> str:
    authors = item.get("authors")
    first = (authors[0] if isinstance(authors, list) and authors else str(authors or "")).split(";")[0].strip()
    meta = ", ".join(x for x in (str(item.get("year") or ""), first) if x)
    grade = str(item.get("quality_grade") or "")
    return (f"- [{_escape(item.get('title'))}{f' ({_escape(meta)})' if meta else ''}]"
            f"(zotero://select/library/items/{item['item_key']})" + (f" · разбор: {grade}" if grade else ""))
