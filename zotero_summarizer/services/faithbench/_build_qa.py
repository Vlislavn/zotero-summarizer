"""Build stage: auto-generate extractive QA, gate it deterministically, add traps.

The builder LLM (the remote judge endpoint by default — the model under test
must never write its own exam) proposes candidate ``{question, answer_span,
answer_type}`` triples from text windows. A candidate enters the benchmark
ONLY if it survives the deterministic keep-gate: the answer must be a
locatable span of the frozen paper text (exact → case-insensitive →
whitespace-tolerant re-anchoring), short, non-leaky and deduplicated. Trap
questions are verified QA from *other* papers whose answer is provably absent
from the target paper (proxy-verified; the review CSV is the escape hatch).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from zotero_summarizer.services._common import extract_json_blob, to_text
from zotero_summarizer.services.faithbench._constants import (
    DEFAULT_QA_PER_PAPER,
    DEFAULT_TRAPS_PER_PAPER,
    MAX_GOLD_SPAN_CHARS,
    QA_MAX_WINDOWS,
    QA_WINDOW_CHARS,
)
from zotero_summarizer.services.faithbench._corpus import (
    PaperRecord,
    normalize_text,
    sentence_at,
)
from zotero_summarizer.services.faithbench._dataset import QAItem, TrapItem
from zotero_summarizer.storage.corpus_bm25 import tokenize

LOGGER = logging.getLogger(__name__)

_QA_GENERATION_PROMPT = (
    "You write extractive QA pairs from an excerpt of an academic paper.\n"
    "Rules:\n"
    "- The answer MUST be a short contiguous span copied VERBATIM from the excerpt "
    "(at most 15 words). Copy it character-for-character.\n"
    "- Prefer specific facts: numbers with units, dataset/method/model names, metric "
    "values, sample sizes.\n"
    "- The question must be self-contained, answerable ONLY by reading the paper, and "
    "must not contain the answer.\n"
    "- No yes/no questions.\n"
    '- "answer_type" is "number" when the span is numeric, "entity" for a name, '
    '"span" otherwise.\n\n'
    "Paper title: {title}\n\nExcerpt:\n{window}\n\n"
    "Return ONE JSON object, nothing else:\n"
    '{{"items": [{{"question": "...", "answer_span": "...", "answer_type": "..."}}, ...]}}\n'
    "Propose up to {n} pairs."
)


# ---------------------------------------------------------------------------
# Candidate generation
# ---------------------------------------------------------------------------


def _windows(text: str) -> list[str]:
    """Up to QA_MAX_WINDOWS evenly-spaced windows covering the paper."""
    if len(text) <= QA_WINDOW_CHARS:
        return [text]
    count = min(QA_MAX_WINDOWS, max(1, len(text) // QA_WINDOW_CHARS))
    if count == 1:
        return [text[:QA_WINDOW_CHARS]]
    step = (len(text) - QA_WINDOW_CHARS) // (count - 1)
    return [text[i * step: i * step + QA_WINDOW_CHARS] for i in range(count)]


def generate_candidates(
    llm: Any, *, title: str, text: str, per_window: int
) -> list[dict[str, Any]]:
    """Ask the builder LLM for candidate QA pairs over each window.

    A window whose response cannot be parsed as JSON is logged and yields no
    candidates (the gate downstream needs *verified* pairs; an unparseable
    builder response only shrinks the candidate pool, never corrupts it).
    """
    candidates: list[dict[str, Any]] = []
    for window in _windows(text):
        prompt = _QA_GENERATION_PROMPT.format(title=title, window=window, n=per_window)
        raw = to_text(llm.prompt(prompt))
        try:
            payload = extract_json_blob(raw)
        except ValueError:
            LOGGER.warning("faithbench build: unparseable QA-builder output, window skipped")
            continue
        for entry in payload.get("items") or []:
            if isinstance(entry, dict):
                candidates.append(entry)
    return candidates


# ---------------------------------------------------------------------------
# Deterministic keep-gate (span verification)
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _locate_span(text: str, span: str) -> tuple[int, int] | None:
    """Anchor ``span`` in ``text``: exact → case-insensitive → whitespace-tolerant."""
    span = (span or "").strip()
    if not span:
        return None
    idx = text.find(span)
    if idx >= 0:
        return idx, idx + len(span)
    lower_idx = text.lower().find(span.lower())
    if lower_idx >= 0:
        return lower_idx, lower_idx + len(span)
    # Whitespace-tolerant: the builder may have collapsed a line break.
    pattern = r"\s+".join(re.escape(part) for part in span.split())
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if match:
        return match.start(), match.end()
    return None


def _count_occurrences(text: str, anchored: str) -> int:
    pattern = r"\s+".join(re.escape(part) for part in anchored.split())
    return len(re.findall(pattern, text, flags=re.IGNORECASE))


def verify_candidates(
    candidates: list[dict[str, Any]],
    *,
    paper: PaperRecord,
    max_keep: int,
) -> list[QAItem]:
    """Apply the keep-gate; returns at most ``max_keep`` verified QAItems."""
    kept: list[QAItem] = []
    seen_questions: set[str] = set()
    for cand in candidates:
        if len(kept) >= max_keep:
            break
        question = str(cand.get("question") or "").strip()
        span = str(cand.get("answer_span") or "").strip()
        answer_type = str(cand.get("answer_type") or "span").strip().lower()
        if answer_type not in ("number", "entity", "span"):
            answer_type = "span"
        if not question or not span or len(span) > MAX_GOLD_SPAN_CHARS:
            continue
        located = _locate_span(paper.text, span)
        if located is None:
            continue  # hallucinated span — the gate's whole purpose
        start, end = located
        gold = paper.text[start:end]
        norm_q, norm_a = normalize_text(question), normalize_text(gold)
        if not norm_a or norm_a in norm_q:
            continue  # leaky question contains its own answer
        if answer_type == "number" and not _NUMBER_RE.search(gold):
            continue
        if norm_q in seen_questions:
            continue
        seen_questions.add(norm_q)
        kept.append(
            QAItem(
                item_id=f"qa:{paper.item_key}:{len(kept)}",
                paper_item_key=paper.item_key,
                paper_title=paper.title,
                paper_text_sha256=paper.text_sha256,
                question=question,
                gold_answer=gold,
                span_start=start,
                span_end=end,
                answer_type=answer_type,  # type: ignore[arg-type]
                occurrences_in_paper=_count_occurrences(paper.text, gold),
                evidence_sentence=sentence_at(paper.text, start),
            )
        )
    return kept


# ---------------------------------------------------------------------------
# Trap construction
# ---------------------------------------------------------------------------


def build_traps(
    papers: list[PaperRecord],
    qa_by_paper: dict[str, list[QAItem]],
    *,
    traps_per_paper: int = DEFAULT_TRAPS_PER_PAPER,
) -> list[TrapItem]:
    """For each target paper, pick verified QA from other papers whose answer
    is absent from the target (string + content-token absence, both on
    normalized text). Deterministic round-robin over source papers."""
    traps: list[TrapItem] = []
    norm_texts = {p.item_key: normalize_text(p.text) for p in papers}
    token_sets = {p.item_key: set(tokenize(p.text)) for p in papers}

    for target in papers:
        target_norm = norm_texts[target.item_key]
        target_tokens = token_sets[target.item_key]
        picked = 0
        for source in papers:
            if picked >= traps_per_paper:
                break
            if source.item_key == target.item_key:
                continue
            for qa in qa_by_paper.get(source.item_key, []):
                if picked >= traps_per_paper:
                    break
                answer_norm = normalize_text(qa.gold_answer)
                if not answer_norm or answer_norm in target_norm:
                    continue
                content_tokens = [t for t in tokenize(qa.gold_answer) if len(t) > 3]
                if content_tokens and any(t in target_tokens for t in content_tokens):
                    continue
                traps.append(
                    TrapItem(
                        item_id=f"trap:{target.item_key}:{picked}",
                        paper_item_key=target.item_key,
                        paper_title=target.title,
                        paper_text_sha256=target.text_sha256,
                        question=qa.question,
                        source_paper_item_key=source.item_key,
                        source_gold_answer=qa.gold_answer,
                    )
                )
                picked += 1
        if picked < traps_per_paper:
            LOGGER.warning(
                "faithbench build: only %d/%d traps for %s (answers from other "
                "papers overlapped its text)", picked, traps_per_paper, target.item_key,
            )
    return traps


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def build_items(
    *,
    papers: list[PaperRecord],
    builder_llm: Any,
    qa_per_paper: int = DEFAULT_QA_PER_PAPER,
    traps_per_paper: int = DEFAULT_TRAPS_PER_PAPER,
    progress_cb: Callable[[str], None] | None = None,
) -> list[QAItem | TrapItem]:
    """Generate + gate QA for every paper, then add traps. Pure orchestration —
    paper selection/freezing lives in ``_corpus.select_papers``."""
    qa_by_paper: dict[str, list[QAItem]] = {}
    for paper in papers:
        candidates = generate_candidates(
            builder_llm, title=paper.title, text=paper.text, per_window=qa_per_paper
        )
        verified = verify_candidates(candidates, paper=paper, max_keep=qa_per_paper)
        qa_by_paper[paper.item_key] = verified
        if progress_cb:
            progress_cb(
                f"{paper.item_key}: kept {len(verified)}/{len(candidates)} candidate QA"
            )
        if not verified:
            LOGGER.warning(
                "faithbench build: 0 verified QA for %s — builder spans never "
                "anchored in the text", paper.item_key,
            )

    items: list[QAItem | TrapItem] = [qa for qas in qa_by_paper.values() for qa in qas]
    if not items:
        raise RuntimeError(
            "faithbench build: no QA pair survived span verification across all papers"
        )
    items.extend(build_traps(papers, qa_by_paper, traps_per_paper=traps_per_paper))
    return items
