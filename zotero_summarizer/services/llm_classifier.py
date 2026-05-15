"""LLM-as-classifier: ask the model to classify title+abstract directly.

This is the **fourth** classifier in the lineup (alongside LogReg, LightGBM,
and TabPFN). Unlike those — which learn from the golden labels — this one
makes a fresh judgement per paper using only the LLM's prior knowledge and
the user's stated research goals from ``goals.yaml``.

Why this matters:
    The user's "first-glance hook" decision is hard to capture in tabular
    features. The LLM can read tone and topical fit holistically, the way the
    user does. It might catch nuances (e.g. "tangential application of agents
    to agricultural yield, not core to the goals") that the embedding misses.

Pipeline:
    1. Load goals from ``goals.yaml.research_goals`` (passed by caller).
    2. For each row in the golden CSV: build a focused prompt and invoke the
       LLM via ``pydantic_prompt`` for structured output.
    3. ``ThreadPoolExecutor`` with ``workers=4`` (default) parallelises the
       per-paper LLM calls — the OnPrem ``LLM`` wrapper is thread-safe
       (each call is a stateless HTTP request).
    4. Per-paper failures are swallowed; the row just gets an empty priority.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, Field, field_validator

from zotero_summarizer.domain import normalize_reading_priority


LOGGER = logging.getLogger(__name__)


_VALID_PRIORITIES = {"must_read", "should_read", "could_read", "dont_read"}


@dataclass
class LLMClassification:
    item_key: str
    priority: str           # one of must/should/could/dont, or "" on failure
    confidence: float
    rationale: str
    error: str = ""


class _LLMVerdict(BaseModel):
    priority: str = Field(...)
    confidence: float = Field(..., ge=0.0, le=1.0)
    rationale: str = Field(default="")

    @field_validator("priority")
    @classmethod
    def _normalise(cls, value: str) -> str:
        v = normalize_reading_priority(str(value or "").strip())
        if v not in _VALID_PRIORITIES:
            raise ValueError(
                f"priority must be one of {_VALID_PRIORITIES}, got {value!r}"
            )
        return v


_PROMPT_TEMPLATE = """\
You are pre-screening a single academic paper for a researcher who has
stated these primary goals:

{goals}

You are NOT reading the paper — only its title and abstract. Mimic the
researcher's first-glance triage: would they invest 30+ minutes reading
this paper carefully, or skip it?

Paper:
Title: {title}
Abstract: {abstract}
Authors: {authors}
Venue: {venue}

Be CRITICAL. Papers that:
- apply agents/ML to **unrelated domains** (agriculture, retail, gaming,
  geography, generic NLP) → dont_read, even if they sound interesting
- promise a lot in the abstract but show no concrete methodology → dont_read
- repeat well-known ideas without novelty → could_read at best
- directly tackle one of the researcher's stated goals AND have a credible
  method → should_read or must_read

Return JSON with three fields:
- priority: one of must_read | should_read | could_read | dont_read
- confidence: 0.0 to 1.0
- rationale: 1-2 sentences explaining why
"""


def classify_papers_with_llm(
    rows: list[dict[str, str]],
    llm_client: Any,
    *,
    research_goals: list[str],
    workers: int = 4,
    progress_cb: Callable[[int, int], None] | None = None,
) -> list[LLMClassification]:
    """Classify every row in ``rows`` with the LLM, in parallel.

    Rows missing title or abstract are returned with an empty priority and
    an explanatory ``error`` field.
    """
    goals_text = "\n".join(f"- {g}" for g in research_goals) if research_goals else "- (no goals configured)"
    total = len(rows)
    results: list[LLMClassification | None] = [None] * total

    def _classify_one(idx: int, row: dict[str, str]) -> LLMClassification:
        item_key = (row.get("item_key") or "").strip()
        title = (row.get("title") or "").strip()
        abstract = (row.get("abstract") or "").strip()
        if not title or not abstract:
            return LLMClassification(
                item_key=item_key,
                priority="",
                confidence=0.0,
                rationale="",
                error="missing title or abstract",
            )
        prompt = _PROMPT_TEMPLATE.format(
            goals=goals_text,
            title=title,
            abstract=abstract[:2000],
            authors=(row.get("authors") or "").strip() or "(unknown)",
            venue=(row.get("venue") or "").strip() or "(unknown)",
        )
        try:
            verdict = llm_client.pydantic_prompt(prompt=prompt, pydantic_model=_LLMVerdict)
            return LLMClassification(
                item_key=item_key,
                priority=verdict.priority,
                confidence=float(verdict.confidence),
                rationale=(verdict.rationale or "")[:400],
            )
        except Exception as exc:
            LOGGER.warning("LLM classify failed for %r: %s", title[:60], exc)
            return LLMClassification(
                item_key=item_key,
                priority="",
                confidence=0.0,
                rationale="",
                error=str(exc)[:300],
            )

    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_classify_one, i, r): i for i, r in enumerate(rows)}
        for fut in as_completed(futures):
            idx = futures[fut]
            results[idx] = fut.result()
            completed += 1
            if progress_cb is not None and (completed % 10 == 0 or completed == total):
                progress_cb(completed, total)

    return [r for r in results if r is not None]


def write_predictions_to_csv(
    input_csv,
    classifications: list[LLMClassification],
    *,
    classifier_name: str,
) -> int:
    """Add per-classifier columns to the golden CSV.

    Columns ``cls_{name}_priority``, ``cls_{name}_score``, ``cls_{name}_rationale``
    are created and used. Different classifier_name values never collide, so
    runs of different LLM endpoints are preserved alongside each other (FAIR
    ``Reusable``).
    """
    import csv as _csv
    from pathlib import Path

    if not classifier_name or "/" in classifier_name or " " in classifier_name:
        raise ValueError(
            f"invalid classifier_name {classifier_name!r}; must be a short slug "
            "(letters / digits / underscore / dash only)."
        )

    path = Path(input_csv)
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = _csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    priority_col = f"cls_{classifier_name}_priority"
    score_col = f"cls_{classifier_name}_score"
    rationale_col = f"cls_{classifier_name}_rationale"
    for col in (priority_col, score_col, rationale_col):
        if col not in fieldnames:
            fieldnames.append(col)

    by_key = {c.item_key: c for c in classifications}
    updated = 0
    for row in rows:
        key = row.get("item_key", "")
        c = by_key.get(key)
        if c is None:
            row.setdefault(priority_col, "")
            row.setdefault(score_col, "")
            row.setdefault(rationale_col, "")
            continue
        row[priority_col] = c.priority
        row[score_col] = f"{c.confidence:.4f}" if c.priority else ""
        row[rationale_col] = c.rationale
        if c.priority:
            updated += 1

    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = _csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    tmp.replace(path)
    return updated
