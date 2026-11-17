"""Checking a review-feedback package against its paper text, and the verdicts kept in ``check.json``.

``inspect`` is a plain string search (no model, no network): for each given part it looks for the part's
shown phrases in ``source.txt`` exactly, then after normalising case, spacing, quotes and line-break
hyphens. It reports a character offset and a share of the text, not page numbers (the extractor keeps
none). ``check.json`` = ``{part: {status, note, checked_at}}``, written by the CLI or by hand; a file that
fails the schema is ignored whole, so a bad edit never shows a wrong verdict.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from zotero_summarizer.services._common import now_iso_z
from zotero_summarizer.services.library.review_feedback_bundle import FIELDS, dump_json, load_json

STATUSES = ("confirmed", "rejected", "unclear")
NOTE_MAX = 500
_MIN_PHRASE = 25
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_LABEL = re.compile(r"^\s*(?:-\s+)?(?:[A-Z][\w /]{0,30}:\s+)?")
_QUOTE = re.compile(r"['\"“‘]([^'\"”’]{25,}?)['\"”’](?=[\s.,;:)\]]|$)")


def read_checks(pkg: Path) -> dict[str, dict[str, str]]:
    """The validated ``check.json`` of a package, or ``{}`` when absent or invalid."""
    data = load_json(pkg / "check.json")
    if not isinstance(data, dict):
        return {}
    for field, entry in data.items():
        if (field not in FIELDS or not isinstance(entry, dict) or entry.get("status") not in STATUSES
                or not isinstance(entry.get("note", ""), str) or len(entry.get("note", "")) > NOTE_MAX
                or not isinstance(entry.get("checked_at"), str)):
            return {}
    return data


def write_check(pkg: Path, field: str, status: str, note: str) -> dict[str, str]:
    """Record the verdict on one part (replaces its earlier one); ``ValueError`` on a bad field, status or note."""
    if field not in FIELDS or status not in STATUSES or len(note) > NOTE_MAX:
        raise ValueError(f"field must be one of {', '.join(FIELDS)}; status one of {', '.join(STATUSES)}; "
                         f"note at most {NOTE_MAX} characters")
    entry = {"status": status, "note": note, "checked_at": now_iso_z()}
    dump_json(pkg / "check.json", {**read_checks(pkg), field: entry})
    return entry


def _normalise(text: str) -> str:
    text = re.sub(r"-\s*\n\s*", "", text)
    text = text.translate(str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-"}))
    return re.sub(r"\s+", " ", text).strip().casefold()


def phrases(shown: str) -> list[str]:
    """Pieces of a part's shown text worth searching for: its sentences and the quotes inside them (labels and
    short values skipped). Model wording rarely repeats the paper, but a quote should."""
    sentences = [piece.strip() for line in shown.splitlines() for piece in _SENTENCE.split(_LABEL.sub("", line, count=1))]
    found = [piece for sentence in sentences for piece in (sentence, *_QUOTE.findall(sentence)) if len(piece) >= _MIN_PHRASE]
    return list(dict.fromkeys(found))


def find_phrase(text: str, normalised: str, phrase: str) -> dict[str, Any]:
    """``{match: exact|normalised|none, offset, share}`` — offset in ``source.txt`` (exact) or in its normalised form."""
    at = text.find(phrase)
    if at >= 0:
        return {"match": "exact", "offset": at, "share": round(at / max(len(text), 1), 2)}
    at = normalised.find(_normalise(phrase))
    if at >= 0:
        return {"match": "normalised", "offset": at, "share": round(at / max(len(normalised), 1), 2)}
    return {"match": "none"}


def inspect(pkg: Path, fields: list[str] | None = None) -> dict[str, Any]:
    """Per part: reasons, note, and where each shown phrase sits in ``source.txt`` (``source_text`` says if there is one)."""
    feedback = load_json(pkg / "feedback.json") or {}
    try:
        text = (pkg / "source.txt").read_text(encoding="utf-8")
    except OSError:
        text = None
    normalised = _normalise(text) if text is not None else ""
    checks, out = read_checks(pkg), {}
    for field, part in (feedback.get("parts") or {}).items():
        if fields and field not in fields:
            continue
        shown = str(part.get("shown") or "")
        found = [{"phrase": phrase, **(find_phrase(text, normalised, phrase) if text is not None else {"match": "no_source"})}
                 for phrase in phrases(shown)]
        out[field] = {"withdrawn": bool(part.get("withdrawn")), "chips": part.get("chips") or [], "note": part.get("note") or "",
                      "shown": shown, "phrases": found, "checked": checks.get(field)}
    return {"item_key": feedback.get("item_key"), "article": feedback.get("article"), "reviewed_at": feedback.get("reviewed_at"),
            "source_text": text is not None, "parts": out}
