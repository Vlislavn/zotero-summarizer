"""Interpret user-written Zotero notes as labels for the golden set.

The user maintains free-text notes on library items: research diaries,
TL;DR summaries, "boring paper" verdicts, idea capture, etc. Many of these
contain explicit verdicts about a paper's quality — "tedious, too basic",
"great framing, methodology weak", "this nailed the agentic safety angle".
That signal is far richer than the emoji tags alone.

This module:

1. Pulls user-authored notes from Zotero, skipping LLM-generated content
   (our own ``zs:note_type=`` markers, "Annotations (date)" PDF extracts,
   Gemini/Claude/ChatGPT-style headers, quote-heavy compilations, very
   short or very long blobs that aren't single-paper opinions).
2. Sends each surviving note to the configured LLM with a strict
   classification prompt — the LLM either picks one of must/should/could/dont
   or returns ``SKIP`` when the note is not a per-paper opinion.
3. Writes a review CSV in the same shape as ``feed-predictions-*.csv``
   (``your_label`` for the human verdict, ``abstract_preview`` for the
   imported abstract) so downstream review tooling accepts it.

The user can then audit each row, override the LLM's choice if needed, and
ingest.
"""

from __future__ import annotations

import csv
import logging
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field, field_validator

from zotero_summarizer.domain import normalize_reading_priority


LOGGER = logging.getLogger(__name__)


_PDF_ANNOT_RE = re.compile(r"^Annotations\s*\(\d{1,2}/\d{1,2}/\d{4}", re.I)
_EXTERNAL_LLM_RE = re.compile(
    r"(gemini|chatgpt|claude|perplexity|deep research|executive summary|"
    r"this paper argues|the trajectory of|1\.\s+(introduction|executive|background))",
    re.I,
)
_QUOTE_HEAVY_RE = re.compile(r'["“][^"”\n]{40,}["”]')
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_VALID_PRIORITIES = {"must_read", "should_read", "could_read", "dont_read"}
_SKIP = "SKIP"


@dataclass
class UserNote:
    """One user-written note ready for LLM classification."""

    note_id: int
    parent_item_key: str
    note_title: str
    note_body: str
    parent_title: str
    parent_abstract: str


@dataclass
class NoteAnalysis:
    """One classified note ready for CSV emission."""

    item_key: str           # `note:<parent_item_key>:<note_id>` so the key is stable & disambiguated
    title: str              # parent paper title
    authors: str
    venue: str
    doi: str
    abstract_preview: str   # first 200 chars of parent abstract (for human reviewer)
    note_title: str
    note_preview: str       # first 300 chars of the note itself
    llm_priority: str       # must/should/could/dont OR empty when skipped
    llm_confidence: float
    llm_rationale: str
    skipped_reason: str = ""
    your_label: str = ""


class _NoteVerdict(BaseModel):
    """Strict response schema for the LLM classifier."""

    priority: str = Field(...)
    confidence: float = Field(..., ge=0.0, le=1.0)
    rationale: str = Field(default="")

    @field_validator("priority")
    @classmethod
    def _normalise(cls, value: str) -> str:
        v = str(value or "").strip().lower()
        if v.upper() == "SKIP":
            return "SKIP"
        v = normalize_reading_priority(v)
        if v not in _VALID_PRIORITIES:
            raise ValueError(
                f"priority must be one of {_VALID_PRIORITIES} or SKIP, got {value!r}"
            )
        return v


_CLASSIFY_PROMPT = """\
You are reading a researcher's own free-text note about a single academic paper.
Your task is ONLY to infer the researcher's overall assessment of the paper
based on the tone and content of the note.

If the note is NOT a per-paper opinion — for example, a personal research
diary, an imported AI-generated essay, a compilation of unrelated topics, or
just a quote dump — respond with priority="SKIP" and explain why in
rationale.

Paper:
- Title: {parent_title}
- Abstract: {parent_abstract}

User note (verbatim, may contain typos, mixed Russian/English, abbreviations):
\"\"\"
{note_body}
\"\"\"

Classify the note's verdict on this paper. Pick one:

- must_read: explicit excitement / "I will apply this" / "this nailed it"
  / "key insight" / strong endorsement of methodology
- should_read: positive framing, useful but not central / "good paper" /
  "interesting perspective" without superlative
- could_read: lukewarm or mixed / "neat idea but X is weak" / one-line
  acknowledgement without deeper engagement
- dont_read: dismissive / "boring" / "basic" / "tedious" / "doesn't deliver"
  / explicit critique without redeeming notes
- SKIP: not a per-paper opinion (research diary, AI-generated content,
  unrelated topic dump, ambiguous note that could be about anything)

Return strict JSON with three fields:
- priority: one of must_read | should_read | could_read | dont_read | SKIP
- confidence: 0.0 to 1.0 (your certainty)
- rationale: 1-2 sentences (English or Russian, match the note's language)
"""


# ---------------------------------------------------------------------------
# Pulling candidate notes from Zotero
# ---------------------------------------------------------------------------


def pull_candidate_notes(
    zotero_data_dir: Path,
    *,
    min_chars: int = 100,
    max_chars: int = 4000,
    limit: int | None = None,
) -> list[UserNote]:
    """Apply the heuristic filter, return the survivors ready for the LLM."""
    db_path = Path(zotero_data_dir) / "zotero.sqlite"
    if not db_path.exists():
        raise FileNotFoundError(f"zotero.sqlite not found at {db_path}")

    sql = """
        SELECT
            n.itemID AS note_id,
            n.note AS note_html,
            n.title AS note_title,
            n.parentItemID AS parent_id,
            parent.key AS parent_key,
            (
                SELECT v.value FROM itemData id
                JOIN fields f ON f.fieldID = id.fieldID
                JOIN itemDataValues v ON v.valueID = id.valueID
                WHERE id.itemID = n.parentItemID AND f.fieldName = 'title' LIMIT 1
            ) AS parent_title,
            (
                SELECT v.value FROM itemData id
                JOIN fields f ON f.fieldID = id.fieldID
                JOIN itemDataValues v ON v.valueID = id.valueID
                WHERE id.itemID = n.parentItemID AND f.fieldName = 'abstractNote' LIMIT 1
            ) AS parent_abstract
        FROM itemNotes n
        JOIN items parent ON parent.itemID = n.parentItemID
        WHERE n.note NOT LIKE '%zs:note_type=%'
          AND n.parentItemID IS NOT NULL
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql).fetchall()
    finally:
        conn.close()

    out: list[UserNote] = []
    for row in rows:
        body = _strip_html(row["note_html"])
        title = (row["note_title"] or "").strip()
        if _PDF_ANNOT_RE.match(body):
            continue
        if _EXTERNAL_LLM_RE.search(title) or _EXTERNAL_LLM_RE.search(body[:500]):
            continue
        if len(_QUOTE_HEAVY_RE.findall(body)) >= 2:
            continue
        if not (min_chars <= len(body) <= max_chars):
            continue
        out.append(UserNote(
            note_id=int(row["note_id"]),
            parent_item_key=str(row["parent_key"] or ""),
            note_title=title,
            note_body=body,
            parent_title=(row["parent_title"] or "").strip(),
            parent_abstract=(row["parent_abstract"] or "").strip(),
        ))
        if limit is not None and len(out) >= limit:
            break
    return out


def _strip_html(s: str | None) -> str:
    if not s:
        return ""
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", s)).strip()


# ---------------------------------------------------------------------------
# LLM classification
# ---------------------------------------------------------------------------


def classify_notes(
    notes: list[UserNote],
    llm_client: Any,
    *,
    abstract_max_chars: int = 800,
    body_max_chars: int = 2500,
    progress_cb: Callable[[int, int], None] | None = None,
) -> list[NoteAnalysis]:
    """Run each note through the LLM. Returns NoteAnalysis records.

    Errors on a single note (parse failure, LLM timeout, etc.) are
    swallowed — the note gets ``priority=""`` with ``skipped_reason``
    describing the failure so the human can decide later.
    """
    out: list[NoteAnalysis] = []
    for i, note in enumerate(notes, start=1):
        try:
            prompt = _CLASSIFY_PROMPT.format(
                parent_title=note.parent_title or "(missing title)",
                parent_abstract=(note.parent_abstract or "")[:abstract_max_chars],
                note_body=note.note_body[:body_max_chars],
            )
            verdict = llm_client.pydantic_prompt(
                prompt=prompt, pydantic_model=_NoteVerdict,
            )
            priority = verdict.priority
            confidence = float(verdict.confidence)
            rationale = (verdict.rationale or "")[:400]
            skipped_reason = "" if priority != "SKIP" else (rationale or "LLM returned SKIP")
            llm_priority = "" if priority == "SKIP" else priority
        except Exception as exc:
            LOGGER.warning("note %d/%d classify failed: %s", i, len(notes), exc)
            llm_priority = ""
            confidence = 0.0
            rationale = ""
            skipped_reason = f"classify error: {exc}"

        out.append(NoteAnalysis(
            item_key=f"note:{note.parent_item_key}:{note.note_id}",
            title=note.parent_title,
            authors="",
            venue="",
            doi="",
            abstract_preview=(note.parent_abstract or "")[:200],
            note_title=note.note_title,
            note_preview=note.note_body[:300],
            llm_priority=llm_priority,
            llm_confidence=confidence,
            llm_rationale=rationale,
            skipped_reason=skipped_reason,
        ))
        if progress_cb is not None and (i % 10 == 0 or i == len(notes)):
            progress_cb(i, len(notes))
    return out


# ---------------------------------------------------------------------------
# CSV emission — same shape as feed-predictions
# ---------------------------------------------------------------------------


def write_analyses_csv(analyses: list[NoteAnalysis], path: Path) -> None:
    if not analyses:
        path.write_text("")
        return
    # We add aliases so downstream review tooling picks the same fields
    # (it reads `your_label` for the human verdict and `abstract_preview` for
    # the imported abstract).
    fieldnames = list(asdict(analyses[0]).keys())
    if "your_label" not in fieldnames:
        fieldnames.append("your_label")
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for a in analyses:
            row = asdict(a)
            # Pre-fill `your_label` with the LLM's choice so the user can
            # accept defaults and only override disagreements.
            row.setdefault("your_label", a.llm_priority)
            if not row["your_label"]:
                row["your_label"] = a.llm_priority
            writer.writerow(row)


def distribution(analyses: list[NoteAnalysis]) -> dict[str, int]:
    out: dict[str, int] = {}
    for a in analyses:
        key = a.llm_priority or "(skipped)"
        out[key] = out.get(key, 0) + 1
    return out
