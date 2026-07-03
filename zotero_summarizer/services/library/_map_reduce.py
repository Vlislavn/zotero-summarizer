"""Map-reduce deep review: summarise each paper chunk on a CHEAP/LOCAL model (the map —
parallel, low per-call cost), then synthesise the notes into the structured digest on the
BIG/API model (the reduce — the complex decision step).

This is the chunk-local / synthesis-API split: the expensive long-context reasoning only sees
short per-chunk notes, so the whole paper is represented (not just a prefix or the top-ranked
chunks), while the heavy synthesis runs once on the remote model. Contrast the default ``rank``
strategy (BM25-select chunks to fit one budget, single call) — see ``tools/eval_chunking.py``
for the A/B that decides which wins on coverage-per-cost.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from zotero_summarizer.models import GoalsConfig, PaperDigest
from zotero_summarizer.services._common import to_text
from zotero_summarizer.services.library import quality_review
from zotero_summarizer.services.triage.prompts import DEFAULT_MAP_PROMPT


def assess_digest(**kwargs: Any) -> PaperDigest:
    """Compatibility seam for tests and callers that patch this module-level name."""
    return quality_review.assess_digest(**kwargs)


def split_chunks(text: str, chunk_chars: int, *, overlap: int = 200) -> list[str]:
    """Fixed-size char chunks with a small overlap (so a fact split across a boundary still
    lands whole in one chunk). Empty/whitespace chunks are dropped. Deterministic + testable."""
    if chunk_chars <= 0:
        raise ValueError("chunk_chars must be positive")
    if overlap < 0 or overlap >= chunk_chars:
        raise ValueError("overlap must be in [0, chunk_chars)")
    body = text or ""
    step = chunk_chars - overlap
    chunks = [body[i:i + chunk_chars] for i in range(0, len(body), step)]
    return [c for c in chunks if c.strip()]


def _map_chunk(map_llm: Any, chunk: str) -> str:
    return to_text(map_llm.prompt(DEFAULT_MAP_PROMPT.format(chunk=chunk))).strip()


def digest_for_strategy(
    strategy: str,
    title: str,
    full_text: str,
    config: GoalsConfig,
    *,
    map_llm: Any,
    reduce_llm: Any,
    max_chars: int,
    chunk_chars: int,
    sub_concurrency: int,
    focus_prompt: str = "",
    response_format: dict[str, Any] | None = None,
) -> PaperDigest:
    """Dispatch ``config.quality_review.chunk_strategy`` to the right digest path.

    The single live entry point deep_review calls — keeps the strategy branching out
    of the orchestrator (LOC budget) and in the chunking module (single responsibility).
    ``rank`` (default) = section-aware + BM25 ``select_review_text``; ``prefix`` = naive
    ``[:cap]`` truncate (A/B baseline); ``map_reduce`` = chunk-local / synthesis-API.
    ``response_format`` (decoder-level JSON Schema) is forwarded to the rank/prefix
    assess_digest call; map_reduce's reduce reuses assess_digest and gets it too. Errors
    propagate to deep_review's per-item boundary (never a fabricated review)."""
    if strategy == "map_reduce":
        return map_reduce_digest(
            title=title, full_text=full_text, config=config, map_llm=map_llm,
            reduce_llm=reduce_llm, chunk_chars=chunk_chars, sub_concurrency=sub_concurrency,
            response_format=response_format,
        )
    return assess_digest(
        title=title, full_text=full_text, config=config, llm=reduce_llm,
        focus_prompt=focus_prompt, max_chars=max_chars, prefix=(strategy == "prefix"),
        response_format=response_format,
    )


def map_reduce_digest(
    title: str,
    full_text: str,
    config: GoalsConfig,
    *,
    map_llm: Any,
    reduce_llm: Any,
    chunk_chars: int = 8000,
    sub_concurrency: int = 1,
    response_format: dict[str, Any] | None = None,
) -> PaperDigest:
    """MAP each chunk on ``map_llm`` (parallel up to ``sub_concurrency``), REDUCE the notes into
    a ``PaperDigest`` on ``reduce_llm``. The reduce reuses ``quality_review.assess_digest`` (its
    hardened JSON contract + one-retry) — the notes ARE the source text it synthesises, so the
    whole paper is represented. ``response_format`` (decoder-level JSON Schema) is forwarded
    to the reduce's assess_digest when the reduce provider supports structured output. Errors
    propagate (caught at deep_review's per-item boundary)."""
    chunks = split_chunks(full_text, chunk_chars)
    if not chunks:
        raise ValueError("map_reduce_digest: empty paper text")

    if sub_concurrency > 1 and len(chunks) > 1:
        with ThreadPoolExecutor(max_workers=sub_concurrency) as pool:
            notes = list(pool.map(lambda chunk: _map_chunk(map_llm, chunk), chunks))
    else:
        notes = [_map_chunk(map_llm, chunk) for chunk in chunks]

    combined = "\n\n".join(f"[chunk {i + 1}/{len(notes)}]\n{note}" for i, note in enumerate(notes) if note)
    extra = {"response_format": response_format} if response_format else {}
    digest = assess_digest(
        title=title, full_text=combined, config=config, llm=reduce_llm,
        max_chars=len(combined) + 1, **extra,
    )
    return digest.model_copy(update={"basis": "map_reduce"})
