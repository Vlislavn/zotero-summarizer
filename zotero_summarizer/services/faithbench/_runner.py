"""Run stage: ask the model under test, append one long-form row per trial.

Resumable by construction: ``responses.jsonl`` is append-only and keyed by
``(item_id, condition, run_number)``; on start the done-key set is loaded and
completed trials are skipped. ``manifest.json`` snapshots the run config and
refuses a resume whose flags would silently mix two models in one file.

A trial that raises is recorded as ``status=exception`` and counts as a
failure downstream (the ARE discipline: exceptions are failures, only
*unjudgeable* rows leave the denominator). That per-trial boundary is the
benchmark's contract — the error string is preserved in the row, never
swallowed. ``KeyboardInterrupt`` propagates; at most the in-flight trial is
lost.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from zotero_summarizer.services._common import extract_json_blob, now_iso_z, to_text
from zotero_summarizer.services.faithbench import _build_claims
from zotero_summarizer.services.faithbench._constants import RETRIEVAL_TOP_K
from zotero_summarizer.services.faithbench._corpus import PaperChunkIndex, load_frozen_text
from zotero_summarizer.services.faithbench._dataset import BenchmarkItem, BenchmarkMeta

LOGGER = logging.getLogger(__name__)

CONDITIONS = ("full_text", "retrieval")
TRACKS = ("qa", "claims")
CLAIMS_CONDITION = "digest"

# Public: services/library/qa.py reuses this EXACT prompt so the product Q&A
# runs the same instruction the benchmark validated (single source of truth).
ANSWER_PROMPT = (
    "Answer the question using ONLY the provided paper text. If the text does "
    "not contain the answer, you MUST abstain — do not guess, do not use outside "
    "knowledge.\n\n"
    "Paper text:\n{context}\n\n"
    "Question: {question}\n\n"
    "Return exactly ONE JSON object, nothing else:\n"
    '{{"answer": "<short answer, a few words>" , "quote": "<verbatim supporting '
    'sentence from the text>"}}\n'
    'To abstain, return {{"answer": null, "quote": null}}.'
)


@dataclass(frozen=True)
class RunPaths:
    run_dir: Path

    @property
    def manifest(self) -> Path:
        return self.run_dir / "manifest.json"

    @property
    def responses(self) -> Path:
        return self.run_dir / "responses.jsonl"

    @property
    def judgments(self) -> Path:
        return self.run_dir / "judgments.jsonl"

    @property
    def report_json(self) -> Path:
        return self.run_dir / "report.json"

    @property
    def report_md(self) -> Path:
        return self.run_dir / "report.md"

    @property
    def claims_cache_dir(self) -> Path:
        return self.run_dir / "claims_cache"


# ---------------------------------------------------------------------------
# JSONL helpers — last row per trial key wins (a --retry-errors resume appends
# a fresh attempt for a previously failed key).
# ---------------------------------------------------------------------------


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def trial_key(row: dict[str, Any]) -> tuple[str, str, int]:
    return (str(row["item_id"]), str(row["condition"]), int(row["run_number"]))


def latest_by_key(rows: list[dict[str, Any]]) -> dict[tuple[str, str, int], dict[str, Any]]:
    out: dict[tuple[str, str, int], dict[str, Any]] = {}
    for row in rows:  # file order == chronological order
        out[trial_key(row)] = row
    return out


def done_keys(rows: list[dict[str, Any]], *, retry_errors: bool) -> set[tuple[str, str, int]]:
    latest = latest_by_key(rows)
    if retry_errors:
        return {k for k, row in latest.items() if row.get("status") == "ok"}
    return set(latest)


# ---------------------------------------------------------------------------
# Manifest (resume guard)
# ---------------------------------------------------------------------------

_MANIFEST_GUARD_FIELDS = ("model", "benchmark_sha256", "conditions", "tracks", "runs")


def write_or_check_manifest(paths: RunPaths, manifest: dict[str, Any]) -> dict[str, Any]:
    """First start writes the manifest; a resume must match the guard fields."""
    if not paths.manifest.exists():
        paths.run_dir.mkdir(parents=True, exist_ok=True)
        paths.manifest.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return manifest
    existing = json.loads(paths.manifest.read_text(encoding="utf-8"))
    for field in _MANIFEST_GUARD_FIELDS:
        if existing.get(field) != manifest.get(field):
            raise RuntimeError(
                f"resume refused: manifest {field}={existing.get(field)!r} but this "
                f"invocation has {manifest.get(field)!r} — one run file must never "
                "mix two configurations"
            )
    return existing


# ---------------------------------------------------------------------------
# Answer parsing (strict, one retry)
# ---------------------------------------------------------------------------


# String spellings of an abstention: models often emit the LITERAL "null"
# instead of JSON null (measured: a trap "hallucination" in the v2 baseline was
# exactly this). Folding them into abstention is parse normalization, same
# class as numeric tolerance — never a semantic judgment.
_NULL_STRINGS = frozenset({"null", "none", "n/a"})


def parse_answer(raw: str) -> dict[str, Any]:
    """``{"answer": str|None, "abstained": bool, "quote": str|None}``.
    Raises ``ValueError`` when no JSON object is recoverable."""
    payload = extract_json_blob(raw)
    answer = payload.get("answer")
    answer_text = None if answer is None else str(answer).strip() or None
    if answer_text is not None and answer_text.lower() in _NULL_STRINGS:
        answer_text = None
    quote = payload.get("quote")
    return {
        "answer": answer_text,
        "abstained": answer_text is None,
        "quote": None if quote is None else str(quote),
    }


def answer_with_retry(llm: Any, prompt: str) -> tuple[dict[str, Any], str]:
    raw = to_text(llm.prompt(prompt))
    try:
        return parse_answer(raw), raw
    except ValueError:
        LOGGER.warning("faithbench run: answer JSON parse failed, retrying once")
        retry_raw = to_text(
            llm.prompt(
                'Extract ONE valid JSON object {"answer": ..., "quote": ...} from the '
                "following text and return ONLY it:\n\n" + raw
            )
        )
        return parse_answer(retry_raw), retry_raw


# ---------------------------------------------------------------------------
# The run itself
# ---------------------------------------------------------------------------


def _qa_context(item: BenchmarkItem, *, condition: str, text: str,
                index: PaperChunkIndex, max_chars: int) -> str:
    if condition == "full_text":
        return text[:max_chars]
    chunks = index.top_chunks(item.question, RETRIEVAL_TOP_K)
    return "\n\n[...]\n\n".join(chunks) if chunks else text[: max_chars // 10]


def run_benchmark(
    *,
    run_id: str,
    meta: BenchmarkMeta,
    items: list[BenchmarkItem],
    papers_dir: Path,
    paths: RunPaths,
    llm: Any,
    config: Any,
    decompose_llm: Any | None,
    conditions: tuple[str, ...] = CONDITIONS,
    tracks: tuple[str, ...] = TRACKS,
    runs: int = 1,
    limit: int | None = None,
    retry_errors: bool = False,
    serial: bool = True,
    max_workers: int = 4,
    progress_cb: Callable[[str], None] | None = None,
) -> dict[str, int]:
    """Execute all pending trials; returns ``{executed, skipped, failed}``."""
    if "claims" in tracks and decompose_llm is None:
        raise ValueError("claims track requested but no decompose_llm provided")

    max_chars = int(config.quality_review.max_text_chars)
    texts: dict[str, str] = {}
    indexes: dict[str, PaperChunkIndex] = {}
    for paper in meta.papers:
        # Integrity check up front: drift means the whole run would be judged
        # against the wrong substrate — refuse instead of recording garbage.
        texts[paper.item_key] = load_frozen_text(
            papers_dir, paper.item_key, expected_sha256=paper.text_sha256
        )

    bench_items = items[: limit] if limit else items
    done = done_keys(load_jsonl(paths.responses), retry_errors=retry_errors)
    write_lock = threading.Lock()

    def emit(row: dict[str, Any]) -> None:
        with write_lock:
            with paths.responses.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()

    def qa_trial(item: BenchmarkItem, condition: str, run_number: int) -> dict[str, Any]:
        text = texts[item.paper_item_key]
        if condition == "retrieval" and item.paper_item_key not in indexes:
            indexes[item.paper_item_key] = PaperChunkIndex(text)
        context = _qa_context(
            item, condition=condition, text=text,
            index=indexes.get(item.paper_item_key) or PaperChunkIndex(text),
            max_chars=max_chars,
        )
        prompt = ANSWER_PROMPT.format(context=context, question=item.question)
        started = now_iso_z()
        t0 = perf_counter()
        parsed, raw = answer_with_retry(llm, prompt)
        return {
            "run_id": run_id, "item_id": item.item_id, "kind": item.kind, "track": "qa",
            "condition": condition, "run_number": run_number,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
            "response_text": raw[:4000], "parsed": parsed,
            "latency_seconds": round(perf_counter() - t0, 3),
            "started_at": started, "status": "ok", "error": None,
        }

    def claims_trial(paper_key: str, run_number: int) -> dict[str, Any]:
        paper = meta.paper_by_key(paper_key)
        started = now_iso_z()
        t0 = perf_counter()
        digest_dump, digest_sha = _build_claims.digest_for_paper(
            title=paper.title, full_text=texts[paper_key], config=config, llm=llm
        )
        claims = _build_claims.decompose_digest(
            digest_dump=digest_dump, digest_sha=digest_sha, title=paper.title,
            decompose_llm=decompose_llm, cache_dir=paths.claims_cache_dir,
        )
        return {
            "run_id": run_id, "item_id": f"claims:{paper_key}", "kind": "claims",
            "track": "claims", "condition": CLAIMS_CONDITION, "run_number": run_number,
            "prompt_sha256": None,
            "response_text": json.dumps(digest_dump, ensure_ascii=False)[:4000],
            "parsed": {"claims": claims, "digest_sha": digest_sha},
            "latency_seconds": round(perf_counter() - t0, 3),
            "started_at": started, "status": "ok", "error": None,
        }

    pending: list[tuple[Callable[[], dict[str, Any]], tuple[str, str, int]]] = []
    if "qa" in tracks:
        for item in bench_items:
            for condition in conditions:
                for run_number in range(1, runs + 1):
                    key = (item.item_id, condition, run_number)
                    if key not in done:
                        pending.append(
                            (lambda i=item, c=condition, r=run_number: qa_trial(i, c, r), key)
                        )
    if "claims" in tracks:
        for paper in meta.papers:
            for run_number in range(1, runs + 1):
                key = (f"claims:{paper.item_key}", CLAIMS_CONDITION, run_number)
                if key not in done:
                    pending.append(
                        (lambda p=paper.item_key, r=run_number: claims_trial(p, r), key)
                    )

    skipped = (len(bench_items) * len(conditions) * runs if "qa" in tracks else 0) + (
        len(meta.papers) * runs if "claims" in tracks else 0
    ) - len(pending)
    counts = {"executed": 0, "skipped": skipped, "failed": 0}

    def execute(thunk: Callable[[], dict[str, Any]], key: tuple[str, str, int]) -> None:
        try:
            row = thunk()
        except Exception as exc:  # per-trial boundary: exception IS the measurement
            row = {
                "run_id": run_id, "item_id": key[0], "kind": "unknown",
                "track": "claims" if key[0].startswith("claims:") else "qa",
                "condition": key[1], "run_number": key[2], "prompt_sha256": None,
                "response_text": None, "parsed": None, "latency_seconds": None,
                "started_at": now_iso_z(), "status": "exception",
                "error": f"{type(exc).__name__}: {exc}",
            }
            counts["failed"] += 1
            LOGGER.warning("faithbench trial %s failed: %s", key, exc)
        emit(row)
        counts["executed"] += 1
        if progress_cb:
            progress_cb(f"[{counts['executed']}/{len(pending)}] {key[0]} {key[1]} run{key[2]}")

    if serial or len(pending) <= 1:
        for thunk, key in pending:
            execute(thunk, key)
    else:
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
            futures = [pool.submit(execute, thunk, key) for thunk, key in pending]
            for future in as_completed(futures):
                future.result()  # re-raise anything outside the per-trial boundary

    return counts
