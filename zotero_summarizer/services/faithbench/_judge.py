"""Judge stage: deterministic hard ladder first, LLM judge only for the residual.

The ladder (ARE ``MildToolJudge`` ordering): a hard FAIL rejects with no model
call; a cheap deterministic PASS accepts with no model call; only the narrow
"close but not exact" band reaches the (pinned) LLM judge. Traps and
abstentions are decided purely deterministically — the judge never sees them.

Tri-state outcomes are honest: a judge call that fails after one retry yields
``success=None / JUDGE_ERROR`` (excluded from accuracy denominators, never
smuggled into ``False``), and frozen-text drift yields ``HARNESS_FAULT`` —
both are the documented benchmark contract, with the error preserved in the
row. Set-equality has no rung here: v1 gold answers are single spans, list
golds don't exist.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

from zotero_summarizer.services._common import extract_json_blob, now_iso_z, to_text
from zotero_summarizer.services.faithbench._constants import (
    CLAIM_JUDGE_TOP_K,
    MAX_CONTAINMENT_ANSWER_CHARS,
    NUMERIC_REL_TOL,
)
from zotero_summarizer.services.faithbench._corpus import (
    PaperChunkIndex,
    load_frozen_text,
    normalize_text,
)
from zotero_summarizer.services.faithbench._dataset import (
    BenchmarkItem,
    BenchmarkMeta,
    QAItem,
    TrapItem,
    items_by_id,
)
from zotero_summarizer.services.faithbench._judgment import (
    FailureReason,
    JudgeMethod,
    Judgment,
)
from zotero_summarizer.services.faithbench._runner import (
    RunPaths,
    latest_by_key,
    load_jsonl,
)

LOGGER = logging.getLogger(__name__)

_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

_EQUIV_PROMPT = (
    "You judge whether a candidate answer is factually equivalent to the gold "
    "answer for a question about an academic paper.\n"
    "- The gold answer is a VERBATIM span from the paper; the evidence sentence "
    "is its context.\n"
    "- Numbers must match up to rounding.\n"
    "- A candidate containing extra material that CONTRADICTS the gold is NOT "
    "equivalent; extra consistent detail is fine.\n\n"
    "Question: {question}\nGold answer: {gold}\nEvidence sentence: {evidence}\n"
    "Candidate answer: {candidate}\n\n"
    'Return ONE JSON object, nothing else: {{"equivalent": true|false, "reason": "..."}}'
)

_CLAIM_PROMPT = (
    "You judge whether a claim about an academic paper is supported by the "
    "paper's text below.\n"
    '- "supported": the text states or directly entails the claim.\n'
    '- "unsupported": the text contradicts the claim, or asserts something the '
    "text does not say.\n"
    '- "not_enough_info": these excerpts neither support nor contradict it.\n\n'
    "Claim: {claim}\n\nPaper text:\n{context}\n\n"
    'Return ONE JSON object, nothing else: '
    '{{"verdict": "supported"|"unsupported"|"not_enough_info", "evidence": "<quote or empty>"}}'
)

# read_why claims come from a field whose generator input is paper text PLUS
# the reader's research goals, so they legitimately describe the paper in
# goal vocabulary ("addresses agent autonomy") — judging them paper-only
# rejects fair characterizations. The goals license the VOCABULARY, never the
# facts: a paper that never engages with a goal topic stays unsupported (that
# is goal-projection hallucination, the failure this track must keep catching).
_RELEVANCE_CLAIM_PROMPT = (
    "You judge a claim taken from a recommendation that explains why an "
    "academic paper matters to a reader with these research goals:\n{goals}\n\n"
    "Such claims may rephrase the paper's content in the reader's goal "
    "vocabulary; that is acceptable. Judge against the paper's text below:\n"
    '- "supported": the paper genuinely engages with the claim\'s subject '
    "matter, even if the claim names it in goal terms rather than the "
    "paper's own words.\n"
    '- "unsupported": the paper does not deal with the claim\'s subject '
    "matter at all — a goal topic was projected onto the paper. The reader's "
    "goals alone NEVER support a claim.\n"
    '- "not_enough_info": these excerpts are insufficient to decide.\n\n'
    "Claim: {claim}\n\nPaper text:\n{context}\n\n"
    'Return ONE JSON object, nothing else: '
    '{{"verdict": "supported"|"unsupported"|"not_enough_info", "evidence": "<quote or empty>"}}'
)


# ---------------------------------------------------------------------------
# Hard ladder (deterministic — no LLM)
# ---------------------------------------------------------------------------


def parse_number(text: str) -> float | None:
    match = _NUMBER_RE.search(text or "")
    if not match:
        return None
    return float(match.group(0).replace(",", ""))


def hard_qa_judgment(
    item: BenchmarkItem, response_row: dict[str, Any]
) -> Judgment | None:
    """Rungs 1-7 of the ladder. ``None`` means undecided → escalate to the LLM."""
    if response_row.get("status") != "ok":
        return Judgment(
            success=False, failure_reason=FailureReason.MODEL_ERROR,
            details=str(response_row.get("error") or "trial raised"),
        )
    parsed = response_row.get("parsed")
    if not isinstance(parsed, dict):
        return Judgment(
            success=False, failure_reason=FailureReason.MALFORMED_RESPONSE,
            details="no parseable answer object even after retry",
        )
    abstained = bool(parsed.get("abstained"))

    if isinstance(item, TrapItem):  # rung: trap rule — judge never sees traps
        if abstained:
            return Judgment(success=True, method=JudgeMethod.TRAP_RULE)
        return Judgment(
            success=False, method=JudgeMethod.TRAP_RULE,
            failure_reason=FailureReason.HALLUCINATED_ON_TRAP,
            details=f"answered an unanswerable question: {str(parsed.get('answer'))[:200]}",
        )

    assert isinstance(item, QAItem)
    if abstained:  # rung: wrong abstain
        return Judgment(
            success=False, method=JudgeMethod.ABSTAIN_RULE,
            failure_reason=FailureReason.WRONG_ABSTAIN,
            details="abstained on an answerable question",
        )

    answer = str(parsed.get("answer") or "")
    norm_gold, norm_answer = normalize_text(item.gold_answer), normalize_text(answer)

    if norm_gold and norm_gold == norm_answer:  # rung: normalized exact
        return Judgment(success=True, method=JudgeMethod.EXACT)

    if item.answer_type == "number":  # rung: numeric tolerance
        gold_num, ans_num = parse_number(item.gold_answer), parse_number(answer)
        if gold_num is not None and ans_num is not None:
            if gold_num == ans_num:
                return Judgment(success=True, method=JudgeMethod.NUMERIC)
            if gold_num.is_integer() and ans_num.is_integer():
                return Judgment(
                    success=False, method=JudgeMethod.NUMERIC,
                    failure_reason=FailureReason.WRONG_ANSWER,
                    details=f"integer mismatch: gold {gold_num:g} vs answer {ans_num:g}",
                )
            denom = max(abs(gold_num), 1e-12)
            if abs(gold_num - ans_num) / denom <= NUMERIC_REL_TOL:
                return Judgment(success=True, method=JudgeMethod.NUMERIC)
            return Judgment(
                success=False, method=JudgeMethod.NUMERIC,
                failure_reason=FailureReason.WRONG_ANSWER,
                details=f"numeric mismatch: gold {gold_num:g} vs answer {ans_num:g}",
            )

    # rung: span containment, capped to block answer-dumping
    if (
        norm_gold
        and len(answer) <= MAX_CONTAINMENT_ANSWER_CHARS
        and norm_gold in norm_answer
    ):
        return Judgment(success=True, method=JudgeMethod.CONTAINMENT)

    return None  # residual band → LLM judge


# ---------------------------------------------------------------------------
# Soft (LLM) judges — only the residual band ever reaches these
# ---------------------------------------------------------------------------


def _judge_json(judge_llm: Any, prompt: str) -> dict[str, Any]:
    raw = to_text(judge_llm.prompt(prompt))
    try:
        return extract_json_blob(raw)
    except ValueError:
        retry = to_text(
            judge_llm.prompt(
                "Extract the single JSON object from the following text and return "
                "ONLY it:\n\n" + raw
            )
        )
        return extract_json_blob(retry)


def judge_equivalence(
    judge_llm: Any, *, item: QAItem, answer: str, judge_model: str
) -> Judgment:
    prompt = _EQUIV_PROMPT.format(
        question=item.question, gold=item.gold_answer,
        evidence=item.evidence_sentence or "(none recorded)", candidate=answer,
    )
    try:
        payload = _judge_json(judge_llm, prompt)
    except Exception as exc:  # tri-state contract: judge failure ≠ model failure
        return Judgment(
            success=None, failure_reason=FailureReason.JUDGE_ERROR,
            details=f"judge call failed after retry: {type(exc).__name__}: {exc}",
            judge_model=judge_model,
        )
    raw = json.dumps(payload, ensure_ascii=False)
    if bool(payload.get("equivalent")):
        return Judgment(
            success=True, method=JudgeMethod.LLM_JUDGE,
            judge_model=judge_model, judge_raw=raw,
        )
    return Judgment(
        success=False, method=JudgeMethod.LLM_JUDGE,
        failure_reason=FailureReason.JUDGE_REJECT,
        details=str(payload.get("reason") or "judge rejected"),
        judge_model=judge_model, judge_raw=raw,
    )


def judge_claim(
    judge_llm: Any, *, claim: str, paper_text: str, norm_paper: str,
    index: PaperChunkIndex, max_chars: int, judge_model: str,
    field: str = "", research_goals: str = "",
) -> Judgment:
    if normalize_text(claim) and normalize_text(claim) in norm_paper:
        return Judgment(success=True, method=JudgeMethod.VERBATIM)

    # Goal-conditioned field → goal-aware standard (see _RELEVANCE_CLAIM_PROMPT).
    relevance = field == "read_why" and bool(research_goals)
    extra = {"judged_against": "paper+goals"} if relevance else {}

    def render(context: str) -> str:
        if relevance:
            return _RELEVANCE_CLAIM_PROMPT.format(
                goals=research_goals, claim=claim, context=context
            )
        return _CLAIM_PROMPT.format(claim=claim, context=context)

    chunks = index.top_chunks(claim, CLAIM_JUDGE_TOP_K)
    context = "\n\n[...]\n\n".join(chunks) if chunks else paper_text[:max_chars]
    try:
        payload = _judge_json(judge_llm, render(context))
        verdict = str(payload.get("verdict") or "").strip().lower()
        if verdict == "not_enough_info":
            # Retrieval miss ≠ unfaithful claim: one second pass with full text.
            payload = _judge_json(judge_llm, render(paper_text[:max_chars]))
            verdict = str(payload.get("verdict") or "").strip().lower()
    except Exception as exc:  # tri-state contract: judge failure ≠ model failure
        return Judgment(
            success=None, failure_reason=FailureReason.JUDGE_ERROR,
            details=f"claim judge failed after retry: {type(exc).__name__}: {exc}",
            judge_model=judge_model,
        )
    raw = json.dumps(payload, ensure_ascii=False)
    if verdict == "supported":
        return Judgment(
            success=True, method=JudgeMethod.LLM_JUDGE,
            judge_model=judge_model, judge_raw=raw, extra=extra,
        )
    return Judgment(
        success=False, method=JudgeMethod.LLM_JUDGE,
        failure_reason=FailureReason.UNSUPPORTED_CLAIM,
        details=f"verdict={verdict or 'missing'}; evidence={str(payload.get('evidence') or '')[:200]}",
        judge_model=judge_model, judge_raw=raw, extra=extra,
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _judged_keys(rows: list[dict[str, Any]]) -> set[tuple[str, str, int, int | None]]:
    return {
        (str(r["item_id"]), str(r["condition"]), int(r["run_number"]), r.get("claim_idx"))
        for r in rows
    }


def judge_run(
    *,
    meta: BenchmarkMeta,
    items: list[BenchmarkItem],
    papers_dir: Path,
    paths: RunPaths,
    judge_llm: Any,
    judge_model: str,
    max_text_chars: int,
    research_goals: str = "",
    force: bool = False,
    progress_cb: Callable[[str], None] | None = None,
) -> dict[str, int]:
    """Judge every response trial (latest row per trial key). Independently
    resumable; ``force=True`` discards prior judgments and re-judges all —
    responses are never touched, so judge-model ablations are free.

    ``research_goals`` (the run's snapshot, "; "-joined) switches read_why
    claims to the goal-aware support standard; empty → paper-only for all."""
    by_id = items_by_id(items)
    responses = list(latest_by_key(load_jsonl(paths.responses)).values())
    if force and paths.judgments.exists():
        paths.judgments.unlink()
    already = _judged_keys(load_jsonl(paths.judgments))

    texts: dict[str, str] = {}
    norms: dict[str, str] = {}
    indexes: dict[str, PaperChunkIndex] = {}
    harness_faults: dict[str, str] = {}
    for paper in meta.papers:
        try:
            texts[paper.item_key] = load_frozen_text(
                papers_dir, paper.item_key, expected_sha256=paper.text_sha256
            )
        except (FileNotFoundError, ValueError) as exc:
            # Documented contract: a broken substrate is a HARNESS_FAULT for
            # that paper's trials, never a model failure (reason preserved).
            harness_faults[paper.item_key] = f"{type(exc).__name__}: {exc}"

    counts = {"judged": 0, "skipped": 0, "escalated": 0}

    def emit(row_id: dict[str, Any], judgment: Judgment, claim_idx: int | None = None) -> None:
        record = {
            "run_id": row_id.get("run_id"), "item_id": row_id["item_id"],
            "track": row_id.get("track"), "condition": row_id["condition"],
            "run_number": row_id["run_number"], "claim_idx": claim_idx,
            "judged_at": now_iso_z(), **judgment.to_row(),
        }
        with paths.judgments.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        counts["judged"] += 1

    for row in responses:
        item_id = str(row["item_id"])
        track = str(row.get("track") or "qa")
        paper_key = item_id.split(":", 2)[1] if ":" in item_id else ""

        if track == "qa":
            key = (item_id, str(row["condition"]), int(row["run_number"]), None)
            if key in already:
                counts["skipped"] += 1
                continue
            if paper_key in harness_faults:
                emit(row, Judgment(success=None, failure_reason=FailureReason.HARNESS_FAULT,
                                   details=harness_faults[paper_key]))
                continue
            item = by_id.get(item_id)
            if item is None:
                emit(row, Judgment(success=None, failure_reason=FailureReason.HARNESS_FAULT,
                                   details=f"{item_id} not in benchmark file"))
                continue
            verdict = hard_qa_judgment(item, row)
            if verdict is None:
                counts["escalated"] += 1
                assert isinstance(item, QAItem)
                verdict = judge_equivalence(
                    judge_llm, item=item,
                    answer=str((row.get("parsed") or {}).get("answer") or ""),
                    judge_model=judge_model,
                )
            emit(row, verdict)
        else:  # claims: one judgment per claim
            if row.get("status") != "ok":
                key = (item_id, str(row["condition"]), int(row["run_number"]), None)
                if key not in already:
                    emit(row, Judgment(success=False, failure_reason=FailureReason.MODEL_ERROR,
                                       details=str(row.get("error") or "trial raised")))
                continue
            if paper_key in harness_faults:
                key = (item_id, str(row["condition"]), int(row["run_number"]), None)
                if key not in already:
                    emit(row, Judgment(success=None, failure_reason=FailureReason.HARNESS_FAULT,
                                       details=harness_faults[paper_key]))
                continue
            # Mirror the QA track: an unknown/empty paper (rebuilt/edited benchmark)
            # is a harness fault, not an uncaught KeyError on texts[paper_key].
            if not paper_key or paper_key not in texts:
                key = (item_id, str(row["condition"]), int(row["run_number"]), None)
                if key not in already:
                    emit(row, Judgment(success=None, failure_reason=FailureReason.HARNESS_FAULT,
                                       details=f"paper {paper_key!r} not in benchmark file"))
                continue
            claims = list(((row.get("parsed") or {}).get("claims")) or [])
            if paper_key not in indexes:
                norms[paper_key] = normalize_text(texts[paper_key])
                indexes[paper_key] = PaperChunkIndex(texts[paper_key])
            for claim_idx, entry in enumerate(claims):
                key = (item_id, str(row["condition"]), int(row["run_number"]), claim_idx)
                if key in already:
                    counts["skipped"] += 1
                    continue
                verdict = judge_claim(
                    judge_llm, claim=str(entry.get("claim") or ""),
                    paper_text=texts[paper_key], norm_paper=norms[paper_key],
                    index=indexes[paper_key], max_chars=max_text_chars,
                    judge_model=judge_model,
                    field=str(entry.get("field") or ""),
                    research_goals=research_goals,
                )
                verdict.extra["field"] = str(entry.get("field") or "")
                emit(row, verdict, claim_idx=claim_idx)
        if progress_cb:
            progress_cb(f"judged {item_id} ({counts['judged']} verdicts, {counts['escalated']} escalations)")

    return counts
