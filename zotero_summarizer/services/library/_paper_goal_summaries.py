"""Goal-conditioned, grounded per-section summaries for the Goal Match Board.

For each of the reader's standing research goals, retrieve the passages of THIS
paper most relevant to that goal (hybrid: a stronger dense leg + BM25 → RRF →
cross-encoder rerank, with a degradation ladder), gate on a relevance floor, and
abstract ONLY the fired goals into a ≤3-sentence grounded summary. Extract-then-
abstract per goal (query-focused) — cheaper and more faithful on a local model
than map-reducing the whole PDF six times.

The board always renders all goals; ``retrieval_state`` separates a grounded
negative (``miss``) from degraded retrieval (``not_retrieved``) so a confident
"not addressed" is never a false negative.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from zotero_summarizer.models import GoalSummary
from zotero_summarizer.services.faithbench._corpus import chunk_text
from zotero_summarizer.services.library._grounding import quote_is_grounded
from zotero_summarizer.storage.corpus_bm25 import tokenize
from zotero_summarizer.services.library._search import _rrf
from zotero_summarizer.services.model.reranker import get_reranker

LOGGER = logging.getLogger(__name__)

DEFAULT_EMBEDDER = "BAAI/bge-large-en-v1.5"   # stronger per-chunk embedder (user choice)
DEFAULT_RERANKER = "BAAI/bge-reranker-v2-m3"
RELEVANCE_FLOOR = 0.35   # min best-chunk cosine for a goal to be a HIT
CANDIDATE_N = 12         # fused candidates fed to the reranker
SUMMARY_CHUNKS = 8       # top chunks fed to the facet abstraction

_EMBEDDERS: dict[str, Any] = {}
_EMBED_LOCK = threading.Lock()

_DEFAULT_GOAL_FACET_PROMPT = (
    'A reader has the standing research interest: "{goal}".\n\n'
    "Using ONLY the paper passages below, write a <=3-sentence summary of what THIS paper "
    "says relevant to that interest (its challenge / approach / outcome on this topic). "
    "If the passages do not actually address the interest, set relevant=false and summary=\"\".\n"
    "Give 1-2 verbatim supporting quotes copied from the passages.\n\n"
    "Passages:\n{passages}\n\n"
    'Return ONE strict JSON object: {{"relevant": true|false, "summary": "...", '
    '"supporting_quotes": ["..."]}}. Start {{ end }}.'
)


class GoalFacetResponse(BaseModel):
    relevant: bool = Field(default=False)
    summary: str = Field(default="")
    supporting_quotes: list[str] = Field(default_factory=list)


# Batched facet: all retrieval-HIT goals summarized in ONE LLM call (the big
# call-count saving on a local model — 6 goal calls collapse to 1). Each entry is
# keyed by goal_index into the hit-goal list the prompt enumerates.
_BATCHED_GOAL_PROMPT = (
    "A reader has {n} standing research interests, each with its OWN retrieved passages "
    "from a single paper. For EACH goal, using ONLY that goal's passages, write a "
    "<=3-sentence summary of what THIS paper says relevant to that interest (its "
    "challenge / approach / outcome). If a goal's passages do not actually address it, "
    "set relevant=false and summary=\"\". Give 1-2 verbatim supporting quotes copied from "
    "that goal's passages.\n\n{blocks}\n\n"
    'Return ONE strict JSON object: {{"summaries": [{{"goal_index": <int 0-based>, '
    '"relevant": true|false, "summary": "...", "supporting_quotes": ["..."]}}, ...]}} — '
    "exactly one entry per goal index above. Start {{ end }}."
)


class _BatchedGoalFacet(BaseModel):
    goal_index: int = Field(default=-1)
    relevant: bool = Field(default=False)
    summary: str = Field(default="")
    supporting_quotes: list[str] = Field(default_factory=list)


class BatchedGoalResponse(BaseModel):
    summaries: list[_BatchedGoalFacet] = Field(default_factory=list)


def _get_embedder(model_name: str) -> Any:
    """Lazy process-level singleton sentence-transformers embedder.

    Optional-dependency boundary (mirrors corpus.py / reranker.py): a missing
    sentence-transformers or a model-load failure degrades the dense leg to
    ``not_retrieved`` rather than crashing the review — the degradation ladder
    the design requires."""
    with _EMBED_LOCK:
        if model_name not in _EMBEDDERS:
            try:
                from sentence_transformers import SentenceTransformer
                _EMBEDDERS[model_name] = SentenceTransformer(model_name)
                LOGGER.info("goal-summaries embedder ready: %s", model_name)
            except Exception:  # noqa: BLE001 - optional dep / download boundary
                LOGGER.warning("goal-summaries embedder %s unavailable; dense leg off", model_name)
                _EMBEDDERS[model_name] = None
        return _EMBEDDERS[model_name]


def _section_chunks(sections: list[dict[str, Any]], full_text: str) -> list[dict[str, Any]]:
    """Section-aware chunks (no chunk spans two sections), each tagged with its
    section title + page. Falls back to whole-body chunking when no sections."""
    out: list[dict[str, Any]] = []
    for sec in sections or []:
        text = str(sec.get("text") or "").strip()
        if not text:
            continue
        title = str(sec.get("title") or "Section")
        page = sec.get("page")
        for piece in chunk_text(text):
            out.append({"text": piece, "section_title": title, "page": page})
    if not out and (full_text or "").strip():
        out = [{"text": p, "section_title": "Body", "page": None} for p in chunk_text(full_text)]
    return out


class _ChunkBM25:
    """BM25 over a fixed chunk list (reuses the faithbench tokenizer); token
    overlap when rank_bm25 is unavailable. ``available`` marks the lexical leg."""

    def __init__(self, chunk_texts: list[str]) -> None:
        self._docs = [tokenize(c) for c in chunk_texts]
        try:
            from rank_bm25 import BM25Okapi
            self._bm25 = BM25Okapi(self._docs) if self._docs else None
            self.available = self._bm25 is not None
        except ImportError:
            self._bm25 = None
            self.available = False

    def ranked(self, query: str) -> list[int]:
        q = tokenize(query)
        if not q or not self._docs:
            return []
        if self._bm25 is not None:
            scores = list(self._bm25.get_scores(q))
        else:
            qs = set(q)
            scores = [float(len(qs.intersection(d))) for d in self._docs]
        return [i for i in sorted(range(len(scores)), key=lambda i: scores[i], reverse=True) if scores[i] > 0]


@dataclass(slots=True)
class _GoalCtx:
    """Shared retrieval context for every goal in one summarize_for_goals call."""
    texts: list[str]
    chunks: list[dict[str, Any]]
    bm25: _ChunkBM25
    chunk_mat: Any          # float32 ndarray | None (None when dense leg is off)
    goal_vecs: dict[str, Any] | None   # goal → float32 ndarray
    has_dense: bool
    reranker: Any
    floor: float
    llm: Any
    prompt_tmpl: str


@dataclass(slots=True)
class _GoalRetrieval:
    """Per-goal retrieval result for a goal that PASSED the gate (needs the LLM)."""
    goal: str
    score: float
    ctx_chunks: list[dict[str, Any]]
    context: str


def summarize_for_goals(
    *, goals: list[str], sections: list[dict[str, Any]], full_text: str, llm: Any,
    embedder_model: str = DEFAULT_EMBEDDER, reranker_model: str = DEFAULT_RERANKER,
    relevance_floor: float = RELEVANCE_FLOOR, facet_prompt: str | None = None,
    reporter: Any = None, batch: bool = False, sub_concurrency: int = 1,
) -> list[GoalSummary]:
    """One GoalSummary per goal (board always renders all).

    ``batch=True`` — one LLM call for all gate-passing goals (local-tier speedup).
    ``sub_concurrency > 1`` — per-goal calls fan out concurrently (remote provider);
    each goal still gets its own full-attention call so quality is unchanged.
    Serial path used when ``sub_concurrency == 1`` (local provider, RAM safety).
    Errors propagate — the deep_review orchestrator wraps this layer."""
    chunks = _section_chunks(sections, full_text)
    texts = [c["text"] for c in chunks]
    if not texts:
        return [GoalSummary(goal=g, retrieval_state="not_retrieved") for g in goals]

    bm25 = _ChunkBM25(texts)
    embedder = _get_embedder(embedder_model)
    chunk_mat = goal_vecs = None
    if embedder is not None:
        import numpy as np
        chunk_mat = np.asarray(embedder.encode(texts, normalize_embeddings=True), dtype="float32")
        goal_vecs = {g: np.asarray(embedder.encode([g], normalize_embeddings=True)[0], dtype="float32") for g in goals}

    reranker = get_reranker(reranker_model)
    reranker.ensure_loaded_async()
    ctx = _GoalCtx(
        texts=texts, chunks=chunks, bm25=bm25,
        chunk_mat=chunk_mat, goal_vecs=goal_vecs, has_dense=embedder is not None,
        reranker=reranker, floor=relevance_floor, llm=llm,
        prompt_tmpl=facet_prompt or _DEFAULT_GOAL_FACET_PROMPT,
    )
    if batch:
        return _summarize_batched(goals, ctx, reporter)

    n = len(goals)
    if reporter is not None:
        reporter.phase("goals", total=n)

    if sub_concurrency <= 1 or n <= 1:
        out: list[GoalSummary] = []
        for i, goal in enumerate(goals):
            out.append(_one_goal(goal, ctx))
            if reporter is not None:
                reporter.sub(i + 1, n)
        return out

    # Parallel path: fan out per-goal LLM calls; retrieval (CPU/embed) is done
    # inside _one_goal and is already concurrent-safe (no shared mutable state).
    done_counter = 0
    counter_lock = threading.Lock()
    ordered: list[GoalSummary | None] = [None] * n

    def _one(idx: int, goal: str) -> tuple[int, GoalSummary]:
        return idx, _one_goal(goal, ctx)

    with ThreadPoolExecutor(max_workers=min(sub_concurrency, n)) as pool:
        futures = {pool.submit(_one, i, g): i for i, g in enumerate(goals)}
        for future in as_completed(futures):
            idx, result = future.result()  # raises on LLM error → propagates
            ordered[idx] = result
            nonlocal_done = 0
            with counter_lock:
                done_counter += 1
                nonlocal_done = done_counter
            if reporter is not None:
                reporter.sub(nonlocal_done, n)

    return [s for s in ordered if s is not None]


def _summarize_batched(goals: list[str], ctx: _GoalCtx, reporter: Any) -> list[GoalSummary]:
    """Per-goal retrieval (local, cheap) → ONE LLM call for all gate-passing goals
    → map back. Miss/degraded goals need no LLM. A goal the batch omits still
    renders (hit, no grounded summary) — the board always shows every goal."""
    early: dict[str, GoalSummary] = {}
    retrievals: list[_GoalRetrieval] = []
    for goal in goals:
        summ, ret = _retrieve_goal(goal, ctx)
        if summ is not None:
            early[goal] = summ
        else:
            retrievals.append(ret)

    facets: dict[int, _BatchedGoalFacet] = {}
    if retrievals:
        if reporter is not None:
            reporter.phase("goals", total=1)
        facets = _batch_goals(retrievals, ctx)
        if reporter is not None:
            reporter.sub(1, 1)

    hits: dict[str, GoalSummary] = {}
    for idx, ret in enumerate(retrievals):
        facet = facets.get(idx)
        if facet is None:
            hits[ret.goal] = GoalSummary(
                goal=ret.goal, retrieval_state="hit", score=ret.score, relevant=True, abstained=True
            )
        else:
            hits[ret.goal] = _facet_to_summary(
                ret, relevant=facet.relevant, summary=facet.summary, quotes=facet.supporting_quotes
            )
    return [early[g] if g in early else hits[g] for g in goals]


def _retrieve_goal(goal: str, ctx: _GoalCtx) -> tuple[GoalSummary | None, _GoalRetrieval | None]:
    """Retrieve + gate one goal. Returns ``(early_summary, None)`` for a confident
    miss / degraded retrieval (no LLM needed) or ``(None, retrieval)`` for a goal
    that passed the gate and needs an LLM facet — so the caller can batch all the
    LLM-needing goals into one call."""
    # Degraded retrieval (no dense leg AND no lexical leg) → never a confident MISS.
    if not ctx.has_dense and not ctx.bm25.available:
        return GoalSummary(goal=goal, retrieval_state="not_retrieved"), None

    dense_ranked: list[int] = []
    dense_max = -1.0
    if ctx.has_dense:
        import numpy as np
        cos = ctx.chunk_mat @ ctx.goal_vecs[goal]
        dense_ranked = list(np.argsort(-cos))
        dense_max = float(cos.max()) if len(cos) else -1.0
    bm25_ranked = ctx.bm25.ranked(goal)

    fused = _rrf([[str(i) for i in dense_ranked[:CANDIDATE_N]], [str(i) for i in bm25_ranked[:CANDIDATE_N]]])
    cand = [int(k) for k, _ in sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:CANDIDATE_N]]
    reranked = ctx.reranker.rerank(goal, [(str(i), ctx.texts[i]) for i in cand], SUMMARY_CHUNKS) if ctx.reranker.is_ready() else []
    ordered = [int(k) for k, _ in reranked] or cand

    # Relevance gate uses the interpretable dense cosine; without a dense leg we
    # cannot trust a negative, so a lexical-only run that finds nothing is degraded.
    if ctx.has_dense:
        score = round(max(0.0, min(3.0, (dense_max - 0.15) / 0.85 * 3.0)), 2)
        if dense_max < ctx.floor:
            return GoalSummary(goal=goal, retrieval_state="miss", score=score, relevant=False), None
    else:
        if not bm25_ranked:
            return GoalSummary(goal=goal, retrieval_state="not_retrieved"), None
        score = 1.5  # lexical-only: no dense gate, weak signal

    ctx_chunks = [ctx.chunks[i] for i in ordered[:SUMMARY_CHUNKS]]
    context = "\n\n".join(c["text"] for c in ctx_chunks)
    return None, _GoalRetrieval(goal=goal, score=score, ctx_chunks=ctx_chunks, context=context)


def _facet_to_summary(ret: _GoalRetrieval, *, relevant: bool, summary: str, quotes: list[str]) -> GoalSummary:
    """Map a facet response (single OR batched) for a gate-passing goal to a
    GoalSummary, enforcing per-quote grounding against that goal's passages."""
    grounded = [q for q in (quotes or []) if quote_is_grounded(q, ret.context)]
    if not relevant:
        # Facet model read the retrieved chunks and judged not addressed → MISS over cosine gate.
        return GoalSummary(goal=ret.goal, retrieval_state="miss", score=ret.score, relevant=False, abstained=True)
    if not grounded:
        return GoalSummary(goal=ret.goal, retrieval_state="hit", score=ret.score, relevant=True, abstained=True)
    key_sections = sorted({
        c["section_title"] for c in ret.ctx_chunks
        if any(quote_is_grounded(q, c["text"]) for q in grounded)
    }) or sorted({c["section_title"] for c in ret.ctx_chunks[:2]})
    return GoalSummary(
        goal=ret.goal, retrieval_state="hit", score=ret.score, relevant=True, abstained=False,
        summary=str(summary or "").strip() or None,
        key_sections=key_sections, supporting_quotes=grounded[:2],
    )


def _one_goal(goal: str, ctx: _GoalCtx) -> GoalSummary:
    """Per-goal path: retrieve + one LLM facet call (non-batch mode)."""
    early, ret = _retrieve_goal(goal, ctx)
    if early is not None:
        return early
    parsed = ctx.llm.pydantic_prompt(
        prompt=ctx.prompt_tmpl.format(goal=goal, passages=ret.context), pydantic_model=GoalFacetResponse
    )
    return _facet_to_summary(ret, relevant=parsed.relevant, summary=parsed.summary, quotes=parsed.supporting_quotes)


def _batch_goals(retrievals: list[_GoalRetrieval], ctx: _GoalCtx) -> dict[int, _BatchedGoalFacet]:
    """ONE LLM call summarizing ALL gate-passing goals. Returns ``{goal_index: facet}``
    for the indices the model returned (a missing index → caller marks that goal
    hit/abstained; a malformed JSON raises out to the goal-layer boundary)."""
    blocks = "\n\n".join(
        f"[Goal {i}]: {r.goal}\nPassages:\n{r.context}" for i, r in enumerate(retrievals)
    )
    prompt = _BATCHED_GOAL_PROMPT.format(n=len(retrievals), blocks=blocks)
    parsed = ctx.llm.pydantic_prompt(prompt=prompt, pydantic_model=BatchedGoalResponse)
    return {
        int(f.goal_index): f for f in (parsed.summaries or [])
        if 0 <= int(f.goal_index) < len(retrievals)
    }
