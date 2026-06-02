"""Library hybrid search: BM25 (lexical) + dense cosine fused via Reciprocal Rank
Fusion, then a local cross-encoder reranks the top candidates. All local.

Produces only an ORDER + a per-row search score — it never touches the gate's
relevance score, banding, or the histogram (the ``derivation == prediction``
invariant of ``_ranking`` holds).

Degradation ladder (search must never hard-fail — requested, since the reranker
downloads on first use): rerank → (model loading/off) RRF fusion → (no BM25)
dense-only → (corpus off / no candidates) empty, so the caller falls back to the
existing substring search. The returned ``status`` carries the UI flags.
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.services._common import state as get_state
from zotero_summarizer.services.model.reranker import get_reranker
from zotero_summarizer.storage.corpus_bm25 import get_corpus_bm25

_TOP_CANDIDATES = 100   # per-retriever depth AND the fused rerank-pool size
_TOP_N = 50             # results shown after rerank
_RRF_K = 60             # reciprocal-rank-fusion constant (standard)


def _rrf(ranked_lists: list[list[str]], k: int = _RRF_K) -> dict[str, float]:
    """Reciprocal Rank Fusion over ranked key lists → ``{key: sum 1/(k+rank)}``.
    Robust to heterogeneous score scales (BM25 unbounded vs cosine [-1,1])."""
    out: dict[str, float] = {}
    for keys in ranked_lists:
        for rank, key in enumerate(keys):
            out[key] = out.get(key, 0.0) + 1.0 / (k + rank + 1)
    return out


def _corpus_cfg() -> Any:
    app_state = getattr(get_state(), "app_state", None)
    config = getattr(app_state, "config", None) if app_state is not None else None
    return getattr(config, "corpus", None) if config is not None else None


def _top(scores: dict[str, float], n: int) -> list[str]:
    return [k for k, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:n]]


def hybrid_search(
    query: str,
    item_keys: list[str],
    *,
    top_candidates: int = _TOP_CANDIDATES,
    top_n: int = _TOP_N,
) -> tuple[list[str], dict[str, float], dict[str, Any]]:
    """Rank ``item_keys`` by relevance to ``query``. Returns
    ``(ordered_keys, {key: search_score}, status)``: ``ordered_keys`` ⊆ item_keys,
    best first, ≤ ``top_n``. ``status`` keys: ``mode``, ``reranked`` (bool),
    ``reranker_loading`` (bool), ``semantic_unavailable`` (bool)."""
    q = str(query or "").strip()
    if not q or not item_keys:
        return [], {}, {"mode": "none", "reranked": False, "reranker_loading": False,
                        "semantic_unavailable": False}

    corpus_cfg = _corpus_cfg()
    if corpus_cfg is None or not getattr(corpus_cfg, "enabled", False):
        return [], {}, {"mode": "corpus_off", "reranked": False, "reranker_loading": False,
                        "semantic_unavailable": True}

    # 1. Dense (cosine) over the resident embedding cache (model already loaded).
    cache = getattr(get_state(), "embedding_cache", None)
    dense = cache.query_affinity_for_items(q, item_keys) if cache is not None else {}
    dense_top = _top(dense, top_candidates)

    # 2. BM25 (lexical).
    bm25_scores: dict[str, float] = {}
    if getattr(corpus_cfg, "bm25_enabled", True):
        bm = get_corpus_bm25(get_settings().corpus_db_path)
        bm25_scores = bm.search(q, item_keys, top_k=top_candidates)
    bm25_top = _top(bm25_scores, top_candidates)

    # 3. Fuse (RRF). Empty union → no candidates → caller falls back to substring.
    fused = _rrf([dense_top, bm25_top])
    if not fused:
        return [], {}, {"mode": "no_candidates", "reranked": False, "reranker_loading": False,
                        "semantic_unavailable": True}
    fused_keys = _top(fused, top_candidates)

    def _fusion_result(reranker_loading: bool) -> tuple[list[str], dict[str, float], dict[str, Any]]:
        ordered = fused_keys[:top_n]
        return ordered, {k: fused[k] for k in ordered}, {
            "mode": "hybrid_fusion", "reranked": False,
            "reranker_loading": reranker_loading, "semantic_unavailable": False,
        }

    # 4. Coherence rerank (local cross-encoder), if enabled.
    if not getattr(corpus_cfg, "reranker_enabled", True):
        return _fusion_result(False)

    reranker = get_reranker(corpus_cfg.reranker_model)
    if reranker.is_ready():
        texts = get_corpus_bm25(get_settings().corpus_db_path).texts_for(fused_keys)
        pairs = [(k, texts[k]) for k in fused_keys if texts.get(k)]
        ranked = reranker.rerank(q, pairs, top_n)
        if ranked:
            return [k for k, _ in ranked], dict(ranked), {
                "mode": "hybrid_reranked", "reranked": True,
                "reranker_loading": False, "semantic_unavailable": False,
            }
    # Not ready (or rerank produced nothing) → warm up in the background, serve
    # the fusion order now; the next search reranks.
    reranker.ensure_loaded_async()
    return _fusion_result(reranker.is_loading())


def order_unread_semantic(
    search: str, unread: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Re-order ``unread`` by hybrid-search relevance to ``search``, attaching
    ``search_score`` to the ranked rows. Returns ``(subset, flags)`` where
    ``subset`` is the ranked rows only (best first, capped by ``hybrid_search``)
    and ``flags`` carries ``semantic`` / ``reranked`` / ``reranker_loading`` for
    the queue's response. When nothing ranks (corpus off / no candidates), returns
    the rows unchanged + ``semantic=False`` so the caller keeps the normal order.

    This is the seam ``reading_queue`` calls so its own footprint stays tiny."""
    ordered, scores, status = hybrid_search(search, [r["item_key"] for r in unread])
    if not ordered:
        return unread, {"semantic": False, "reranked": False, "reranker_loading": False}
    rank_of = {k: i for i, k in enumerate(ordered)}
    for r in unread:
        if r["item_key"] in rank_of:
            r["search_score"] = scores.get(r["item_key"])
    subset = sorted(
        (r for r in unread if r["item_key"] in rank_of),
        key=lambda r: rank_of[r["item_key"]],
    )
    return subset, {
        "semantic": True,
        "reranked": bool(status.get("reranked")),
        "reranker_loading": bool(status.get("reranker_loading")),
    }
