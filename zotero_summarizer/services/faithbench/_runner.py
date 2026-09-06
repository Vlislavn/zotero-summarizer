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
import inspect
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Callable
from uuid import uuid4

from zotero_summarizer.services._common import atomic_write, extract_json_blob, now_iso_z, to_text, write_json_atomic
from zotero_summarizer.services.library._prompt_security import UNTRUSTED_INPUT_RULE, untrusted_input
from zotero_summarizer.services.faithbench import _build_claims
from zotero_summarizer.services.faithbench._constants import RETRIEVAL_TOP_K
from zotero_summarizer.services.faithbench._corpus import (
    PaperChunkIndex, _CONTEXT_SEPARATOR, _clip_chunks, load_frozen_text,
)
from zotero_summarizer.services.faithbench._dataset import (
    BenchmarkItem, BenchmarkMeta, _benchmark_sha, _require_qa_cohort, _validate_identity, _validate_item,
    load_benchmark, require_review_approval,
)

LOGGER = logging.getLogger(__name__)
CONDITIONS = ("full_text", "retrieval")
TRACKS = ("qa", "claims")
CLAIMS_CONDITION = "digest"

# Public: services/library/qa.py reuses this EXACT prompt so the product Q&A
# runs the same instruction the benchmark validated (single source of truth).
ANSWER_PROMPT = (
    UNTRUSTED_INPUT_RULE + "\n\n"
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


@dataclass(frozen=True)
class RunInputs:
    """The frozen run artifacts ``run_benchmark`` and ``judge_run`` both take."""

    meta: BenchmarkMeta
    items: list[BenchmarkItem]
    papers_dir: Path
    paths: RunPaths

    def __post_init__(self) -> None:
        _validate_identity(self.meta, self.items)


# ---------------------------------------------------------------------------
# JSONL helpers — last row per trial key wins (a --retry-errors resume appends
# a fresh attempt for a previously failed key).
# ---------------------------------------------------------------------------


def _decode_jsonl(raw: bytes) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("JSONL trial rows must be objects")
    return rows


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return _decode_jsonl(path.read_bytes()) if path.exists() else []


def _repair_jsonl_tail(path: Path) -> None:
    """Resume-only recovery: archive an interrupted EOF, never skip interior damage."""
    # ponytail: one writer per run; add process locking before concurrent resumes.
    if not path.exists():
        return
    raw = path.read_bytes()
    if not raw or raw.endswith(b"\n"):
        return
    prefix, separator, tail = raw.rpartition(b"\n")
    prefix += separator
    _decode_jsonl(prefix)
    try:
        _decode_jsonl(tail)
    except (json.JSONDecodeError, UnicodeDecodeError):
        backup = path.with_name(f"{path.name}.interrupted-{uuid4().hex}")
        atomic_write(backup, lambda tmp: tmp.write_bytes(raw))
        LOGGER.warning("Recovering interrupted EOF in %s; original archived at %s", path, backup)
        repaired = prefix
    else:
        repaired = raw + b"\n"
    atomic_write(path, lambda tmp: tmp.write_bytes(repaired))


def trial_key(row: dict[str, Any]) -> tuple[str, str, int]:
    return (str(row["item_id"]), str(row["condition"]), int(row["run_number"]))


def _response_sha(row: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True).encode("utf-8")).hexdigest()


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

_MANIFEST_GUARD_FIELDS = (
    "run_id", "model", "provider_name", "base_url", "benchmark_sha256", "benchmark_content_sha256",
    "benchmark_review_sha256",
    "generation_sha256", "research_goals", "conditions", "tracks", "runs", "limit", "serial", "max_workers",
)


def generation_identity(config: Any, models: tuple) -> str:
    """Conservative generation identity; never inspect clients or retain credentials."""
    from zotero_summarizer.models import PaperDigest
    from zotero_summarizer.models.providers import ResolvedStage
    from zotero_summarizer.services import _adapters, _common
    from zotero_summarizer.services.faithbench import _corpus
    from zotero_summarizer.services.library import _review_text, quality_review
    from zotero_summarizer.services.llm import factory

    if len(models) != 2 or not isinstance(models[0], ResolvedStage) or (
        models[1] is not None and not isinstance(models[1], ResolvedStage)
    ):
        raise ValueError("Generation identity requires resolved model profiles")
    modules = (_build_claims, _corpus, quality_review, _review_text, _common, _adapters, factory)
    sources = [Path(__file__), Path(inspect.getfile(PaperDigest)), *(Path(m.__file__) for m in modules)]
    payload = {"config": config.model_dump(mode="json"),
               "models": [model.model_dump(mode="json") if model else None for model in models],
               "timeout_seconds": _common.settings().summary_timeout_seconds,
               "prompts": [ANSWER_PROMPT, _build_claims._DECOMPOSE_PROMPT, quality_review._DEFAULT_DIGEST_PROMPT],
               "schema": PaperDigest.model_json_schema(),
               "sources": [hashlib.sha256(path.read_bytes()).hexdigest() for path in sources]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _load_manifest(paths: RunPaths) -> dict[str, Any]:
    manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
    sha = manifest.get("benchmark_sha256") if isinstance(manifest, dict) else None
    if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha):
        raise ValueError("Run manifest must contain a valid benchmark SHA-256")
    return manifest


def write_or_check_manifest(paths: RunPaths, manifest: dict[str, Any]) -> dict[str, Any]:
    """First start writes the manifest; a resume must match the guard fields."""
    identity_fields = ["generation_sha256", "benchmark_content_sha256"]
    if "qa" in manifest.get("tracks", []):
        identity_fields.append("benchmark_review_sha256")
    for field in identity_fields:
        if not isinstance(manifest.get(field), str) or not re.fullmatch(r"[a-f0-9]{64}", manifest[field]):
            raise ValueError(f"Run generation identity is missing or invalid: {field}")
    if not paths.manifest.exists():
        if any(path.exists() and path.stat().st_size for path in (paths.responses, paths.judgments)):
            raise RuntimeError("Run manifest is missing; refusing to relabel existing trial artifacts")
        write_json_atomic(paths.manifest, manifest)
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
    chunks = _clip_chunks(index.top_chunks(item.question, RETRIEVAL_TOP_K), max_chars)
    return _CONTEXT_SEPARATOR.join(chunks) if chunks else text[: max_chars // 10]


@dataclass(frozen=True)
class RunOptions:
    """Execution knobs for ``run_benchmark``, bundled so call sites don't have
    to spell out all of them; defaults match the previous individual kwargs."""

    conditions: tuple[str, ...] = CONDITIONS
    tracks: tuple[str, ...] = TRACKS
    runs: int = 1
    limit: int | None = None
    retry_errors: bool = False
    serial: bool = True
    max_workers: int = 4

    def __post_init__(self) -> None:
        for name, allowed in (("conditions", CONDITIONS), ("tracks", TRACKS)):
            values = getattr(self, name)
            if not isinstance(values, tuple) or not values or any(v not in allowed for v in values):
                raise ValueError(f"{name} must be a nonempty tuple drawn from {allowed}")
            if len(set(values)) != len(values):
                raise ValueError(f"{name} must not contain duplicates")
        for name in ("runs", "limit", "max_workers"):
            value = getattr(self, name)
            if name == "limit" and value is None:
                continue
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.limit is not None and "qa" not in self.tracks:
            raise ValueError("limit requires the qa track")


@dataclass(frozen=True)
class _TrialContext:
    """Captured locals a trial closure needs — passed explicitly instead of
    closing over ``run_benchmark``'s frame."""

    run_id: str
    meta: BenchmarkMeta
    texts: dict[str, str]
    indexes: dict[str, PaperChunkIndex]
    config: Any
    llm: Any
    decompose_llm: Any | None
    paths: RunPaths
    max_chars: int


def _qa_trial(ctx: _TrialContext, item: BenchmarkItem, condition: str, run_number: int) -> dict[str, Any]:
    text = ctx.texts[item.paper_item_key]
    if condition == "retrieval" and item.paper_item_key not in ctx.indexes:
        ctx.indexes[item.paper_item_key] = PaperChunkIndex(text)
    context = _qa_context(
        item, condition=condition, text=text,
        index=ctx.indexes.get(item.paper_item_key) or PaperChunkIndex(text),
        max_chars=ctx.max_chars,
    )
    prompt = ANSWER_PROMPT.format(
        context=untrusted_input(context), question=untrusted_input(item.question))
    started = now_iso_z()
    t0 = perf_counter()
    parsed, raw = answer_with_retry(ctx.llm, prompt)
    return {
        "run_id": ctx.run_id, "item_id": item.item_id, "kind": item.kind, "track": "qa",
        "condition": condition, "run_number": run_number,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
        "response_text": raw[:4000], "parsed": parsed,
        "latency_seconds": round(perf_counter() - t0, 3),
        "started_at": started, "status": "ok", "error": None,
    }


def _claims_trial(ctx: _TrialContext, paper_key: str, run_number: int) -> dict[str, Any]:
    paper = ctx.meta.paper_by_key(paper_key)
    started = now_iso_z()
    t0 = perf_counter()
    digest_dump, digest_sha = _build_claims.digest_for_paper(
        title=paper.title, full_text=ctx.texts[paper_key], config=ctx.config, llm=ctx.llm
    )
    claims = _build_claims.decompose_digest(
        digest_dump=digest_dump, digest_sha=digest_sha, title=paper.title,
        decompose_llm=ctx.decompose_llm, cache_dir=ctx.paths.claims_cache_dir,
    )
    return {
        "run_id": ctx.run_id, "item_id": f"claims:{paper_key}", "kind": "claims",
        "track": "claims", "condition": CLAIMS_CONDITION, "run_number": run_number,
        "prompt_sha256": None,
        "response_text": json.dumps(digest_dump, ensure_ascii=False)[:4000],
        "parsed": {"claims": claims, "digest_sha": digest_sha},
        "latency_seconds": round(perf_counter() - t0, 3),
        "started_at": started, "status": "ok", "error": None,
    }


def _check_run_identity(run_id: str, inputs: RunInputs, config: Any, models: tuple, options: RunOptions) -> None:
    manifest = _load_manifest(inputs.paths)
    benchmark = Path(manifest["benchmark_path"])
    load_benchmark(benchmark, expected_sha256=manifest["benchmark_sha256"])
    review_sha = require_review_approval(benchmark, inputs.items) if "qa" in options.tracks else None
    write_or_check_manifest(inputs.paths, {
        **manifest, "run_id": run_id, "generation_sha256": generation_identity(config, models),
        "benchmark_content_sha256": _benchmark_sha(inputs.meta, inputs.items),
        "benchmark_review_sha256": review_sha,
        "research_goals": list(config.research_goals),
        "conditions": list(options.conditions), "tracks": list(options.tracks),
        "runs": options.runs, "limit": options.limit, "serial": options.serial, "max_workers": options.max_workers,
    })
    if "claims" in options.tracks and models[1] is None:
        raise ValueError("claims track requires a decomposer generation identity")


def run_benchmark(
    *,
    run_id: str,
    inputs: RunInputs,
    llm: Any,
    config: Any,
    decompose_llm: Any | None,
    generation_models: tuple,
    options: RunOptions = RunOptions(),
    progress_cb: Callable[[str], None] | None = None,
) -> dict[str, int]:
    """Execute all pending trials; returns ``{executed, skipped, failed}``."""
    meta, items, papers_dir, paths = inputs.meta, inputs.items, inputs.papers_dir, inputs.paths
    if "qa" in options.tracks:
        _require_qa_cohort(items)
    if "claims" in options.tracks and decompose_llm is None:
        raise ValueError("claims track requested but no decompose_llm provided")
    _check_run_identity(run_id, inputs, config, generation_models, options)

    max_chars = int(config.quality_review.max_text_chars)
    texts: dict[str, str] = {}
    indexes: dict[str, PaperChunkIndex] = {}
    for paper in meta.papers:
        # Integrity check up front: drift means the whole run would be judged
        # against the wrong substrate — refuse instead of recording garbage.
        texts[paper.item_key] = load_frozen_text(
            papers_dir, paper.item_key, expected_sha256=paper.text_sha256
        )
    for item in items:
        _validate_item(item, meta, texts)

    bench_items = items[: options.limit]
    _repair_jsonl_tail(paths.responses)
    done = done_keys(load_jsonl(paths.responses), retry_errors=options.retry_errors)
    write_lock = threading.Lock()

    def emit(row: dict[str, Any]) -> None:
        with write_lock:
            with paths.responses.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()

    ctx = _TrialContext(
        run_id=run_id, meta=meta, texts=texts, indexes=indexes, config=config,
        llm=llm, decompose_llm=decompose_llm, paths=paths, max_chars=max_chars,
    )

    pending: list[tuple[Callable[[], dict[str, Any]], tuple[str, str, int]]] = []
    if "qa" in options.tracks:
        for item in bench_items:
            for condition in options.conditions:
                for run_number in range(1, options.runs + 1):
                    key = (item.item_id, condition, run_number)
                    if key not in done:
                        pending.append(
                            (lambda i=item, c=condition, r=run_number: _qa_trial(ctx, i, c, r), key)
                        )
    if "claims" in options.tracks:
        for paper in meta.papers:
            for run_number in range(1, options.runs + 1):
                key = (f"claims:{paper.item_key}", CLAIMS_CONDITION, run_number)
                if key not in done:
                    pending.append(
                        (lambda p=paper.item_key, r=run_number: _claims_trial(ctx, p, r), key)
                    )

    skipped = (
        len(bench_items) * len(options.conditions) * options.runs if "qa" in options.tracks else 0
    ) + (
        len(meta.papers) * options.runs if "claims" in options.tracks else 0
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

    if options.serial or len(pending) <= 1:
        for thunk, key in pending:
            execute(thunk, key)
    else:
        with ThreadPoolExecutor(max_workers=options.max_workers) as pool:
            futures = [pool.submit(execute, thunk, key) for thunk, key in pending]
            for future in as_completed(futures):
                future.result()  # re-raise anything outside the per-trial boundary

    return counts
