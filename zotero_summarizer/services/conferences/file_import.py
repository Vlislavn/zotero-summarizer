"""Merge owner-supplied rows (``table_reader.read_rows``) into one edition's records. No network.

A row finds its record by abstract number; a row without a number finds it by title (case,
punctuation and the source's number prefix ignored). Text from the file wins when it is not empty;
a row that finds nothing is added. Touched records carry ``imported: true``, so a later Crossref
download re-applies them instead of losing them. Running the same file again changes nothing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from zotero_summarizer.services.conferences.catalogue import Recipe, shown_title


@dataclass(frozen=True)
class Merged:
    records: list[dict[str, Any]]
    matched: int
    added: int
    skipped: int


def _number_key(number: str) -> str:
    key = re.sub(r"\s+", "", number).casefold()
    return key[:-2] if re.fullmatch(r"\d+\.0", key) else key  # a spreadsheet's 8114.0


def _title_key(recipe: Recipe, title: str) -> str:
    return " ".join(re.findall(r"\w+", shown_title(recipe, title).casefold()))


def _authors(value: Any) -> list[str]:
    if isinstance(value, list):
        return value
    return [a.strip() for a in re.split(r"[;\n]", str(value)) if a.strip()]


def _doi(value: str) -> str:
    return re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", value.strip(), flags=re.IGNORECASE)


def merge_rows(recipe: Recipe, records: list[dict[str, Any]], rows: list[dict[str, Any]]) -> Merged:
    out = [dict(r) for r in records]
    by_number = {_number_key(r["number"]): r for r in out if r["number"]}
    by_title = {_title_key(recipe, r["title"]): r for r in out}
    matched = added = skipped = 0
    for row in rows:
        number, title = str(row.get("number", "")).strip(), str(row.get("title", "")).strip()
        number = number[:-2] if re.fullmatch(r"\d+\.0", number) else number
        target = by_number.get(_number_key(number)) if number else None
        if target is None and not number and title:
            target = by_title.get(_title_key(recipe, title))
        if target is None and not title:
            skipped += 1
            continue
        if target is None:
            target = {"title": title, "authors": [], "abstract": "", "doi": "", "number": number}
            out.append(target)
            by_title[_title_key(recipe, title)] = target
            if number:
                by_number[_number_key(number)] = target
            added += 1
        else:
            matched += 1
        authors, doi = _authors(row.get("authors", [])), _doi(str(row.get("doi", "")))
        target["abstract"] = str(row.get("abstract", "")).strip() or target["abstract"]
        target["authors"] = target["authors"] or authors
        target["doi"] = target["doi"] or doi
        target["session"] = str(row.get("session", "")).strip() or target.get("session", "")
        target["imported"] = True
    return Merged(out, matched, added, skipped)
