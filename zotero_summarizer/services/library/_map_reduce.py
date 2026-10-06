"""Map-reduce deep review: summarise each paper chunk on a CHEAP/LOCAL model (the map —
parallel, low per-call cost), then synthesise the notes into the structured digest on the
BIG/API model (the reduce — the complex decision step).

The synthesis prompt contains per-chunk notes rather than the full paper. Verification
separately reads the original text, so notes cannot authorize their own hallucinations;
its context cost is not bounded by note size. Contrast the default ``rank`` strategy
(BM25-select chunks to fit one budget) — see ``tools/eval_chunking.py`` for the A/B
that decides which wins on coverage-per-cost.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, NamedTuple

from zotero_summarizer.models import GoalsConfig, PaperDigest
from zotero_summarizer.services._common import to_text
from zotero_summarizer.services.library._prompt_security import UNTRUSTED_INPUT_RULE, untrusted_input
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
    prompt = UNTRUSTED_INPUT_RULE + "\n\n" + DEFAULT_MAP_PROMPT.format(chunk=untrusted_input(chunk))
    note = to_text(map_llm.prompt(prompt)).strip()
    if not note:
        raise ValueError("map_reduce_digest: empty chunk summary")
    return note


class ChunkBudget(NamedTuple):
    """The chunking budget trio for ``digest_for_strategy``: the rank/prefix output
    cap, the map_reduce per-chunk size, and its map-step parallelism."""

    max_chars: int
    chunk_chars: int
    sub_concurrency: int


def digest_for_strategy(
    title: str,
    full_text: str,
    config: GoalsConfig,
    *,
    map_llm: Any,
    reduce_llm: Any,
    budget: ChunkBudget,
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
    from zotero_summarizer.services.library._source_admission import admit_source

    admit_source(full_text)
    strategy = config.quality_review.chunk_strategy
    if strategy == "map_reduce":
        return map_reduce_digest(
            title=title, full_text=full_text, config=config, map_llm=map_llm,
            reduce_llm=reduce_llm, chunk_chars=budget.chunk_chars,
            sub_concurrency=budget.sub_concurrency, focus_prompt=focus_prompt,
            response_format=response_format,
        )
    return assess_digest(
        title=title, full_text=full_text, config=config, llm=reduce_llm,
        focus_prompt=focus_prompt, max_chars=budget.max_chars, prefix=(strategy == "prefix"),
        response_format=response_format, verifier_llm=map_llm,
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
    focus_prompt: str = "",
    response_format: dict[str, Any] | None = None,
) -> PaperDigest:
    """MAP each chunk on ``map_llm`` (parallel up to ``sub_concurrency``), REDUCE the notes into
    a ``PaperDigest`` on ``reduce_llm``. The reduce reuses ``quality_review.assess_digest`` (its
    hardened JSON contract + one-retry). Notes are generation context only; verification
    uses the original paper, never the generated notes. Every chunk must yield a nonempty
    note, otherwise partial coverage fails before reduction. ``response_format`` is forwarded
    to the reduce's assess_digest when the reduce provider supports structured output. Errors
    propagate (caught at deep_review's per-item boundary)."""
    from zotero_summarizer.services.library._source_admission import admit_source
    from zotero_summarizer.services.library._review_attempt import observed_client

    admit_source(full_text)
    recorded_map = observed_client(map_llm, 'map')
    chunks = split_chunks(full_text, chunk_chars)
    if not chunks:
        raise ValueError("map_reduce_digest: empty paper text")

    if sub_concurrency > 1 and len(chunks) > 1:
        with ThreadPoolExecutor(max_workers=sub_concurrency) as pool:
            notes = list(pool.map(lambda chunk: _map_chunk(recorded_map, chunk), chunks))
    else:
        notes = [_map_chunk(recorded_map, chunk) for chunk in chunks]

    combined = "\n\n".join(f"[chunk {i + 1}/{len(notes)}]\n{note}" for i, note in enumerate(notes))
    extra = {"response_format": response_format} if response_format else {}
    digest = assess_digest(
        title=title, full_text=combined, config=config, llm=reduce_llm,
        max_chars=len(combined) + 1, verifier_llm=map_llm,
        focus_prompt=focus_prompt, verification_text=full_text, **extra,
    )
    return digest.model_copy(update={"basis": "map_reduce"})
