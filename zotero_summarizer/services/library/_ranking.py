"""Post-scoring queue ORDERING helpers (split from ``reading_queue`` to keep it
≤500 LOC): content de-duplication + goal-aware re-rank.

Both operate on the already-scored unread records and only change their ORDER —
banding/tags/distribution stay computed from the gate's relevance score, so the
``derivation == prediction`` invariant is untouched. ``reading_queue`` re-exports
these so ``reading_queue._dedup_by_content`` etc. remain the public seam.
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.services._common import state as get_state

# Blend weight for goal-text similarity vs the gate score in the queue ORDER
# (NOT banding). Measured on the blind-judge benchmark: 0.6·gate + 0.4·goal lifts
# NDCG@10 0.38→0.72 and P@10 50%→100%, floating on-goal clinical-agentic papers
# the gate buries (goal_sim Spearman-vs-judgment 0.72 vs the gate's 0.40). The
# gate alone over-weights "similar to what I've saved"; this adds "similar to
# what I said I want." Set to 0.0 to disable.
_GOAL_RERANK_WEIGHT = 0.4

# Fallback ordering only (gate not ready): priority tier then recency.
_PRIORITY_RANK: dict[str, int] = {
    "must_read": 3, "should_read": 2, "could_read": 1, "": 0, "dont_read": -1,
}


def _content_key(rec: dict[str, Any]) -> str:
    """Normalized-full-title identity for de-duplication. The same paper imported
    twice into Zotero gets two distinct ``item_key``s but an identical title;
    author strings are NOT used because the copies often list authors in a
    different order or truncate them (so an author guard misses real dups). Two
    genuinely-distinct papers sharing an identical full title is negligible in a
    personal library (and a v1/v2 preprint dup is desirable to collapse)."""
    return "".join(ch for ch in (rec.get("title") or "").lower() if ch.isalnum())


def _dedup_by_content(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse duplicate library items (same paper, two Zotero copies) to ONE,
    keeping the first occurrence — so call this AFTER the rank sort and the
    best-scored copy survives. Stable (preserves rank order). Items with no
    title are never merged (an empty key can't collide)."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in records:
        key = _content_key(r)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(r)
    return out


def _goal_affinity(item_keys: list[str]) -> dict[str, float]:
    """``{item_key: goal-text similarity}`` for the unread items, from the corpus
    EmbeddingCache (cached embeddings → goal embeddings; no model load). Empty
    when the corpus isn't enabled, so the sort cleanly falls back to gate-only."""
    if not item_keys:
        return {}
    # Optional-feature boundary (mirrors classifier._build_aux_providers): no
    # config / corpus-disabled → no goal signal → caller sorts by the gate alone.
    app_state = getattr(get_state(), "app_state", None)
    config = getattr(app_state, "config", None) if app_state is not None else None
    corpus_cfg = getattr(config, "corpus", None) if config is not None else None
    if corpus_cfg is None or not getattr(corpus_cfg, "enabled", False):
        return {}
    from zotero_summarizer.storage.corpus import EmbeddingCache

    cache = EmbeddingCache(get_settings().corpus_db_path, corpus_cfg.embedding_model)
    return cache.goal_affinity_for_items(item_keys)


def _blended_sort(unread: list[dict[str, Any]]) -> None:
    """Sort the unread queue in place by a blend of the gate's relevance and
    goal-text similarity (min-max normalized over the scored set), so on-goal
    papers the gate under-ranks rise. Unscored items sink to the bottom; banding
    is untouched (computed elsewhere from the gate score). See
    ``_GOAL_RERANK_WEIGHT``."""
    scored = [r for r in unread if r["relevance_score"] is not None]
    rels = [float(r["relevance_score"]) for r in scored]
    gss = [float(r["goal_sim"]) for r in scored if r.get("goal_sim") is not None]
    rlo, rhi = (min(rels), max(rels)) if rels else (0.0, 1.0)
    glo, ghi = (min(gss), max(gss)) if gss else (0.0, 1.0)
    w = _GOAL_RERANK_WEIGHT

    def _norm(v: float, lo: float, hi: float) -> float:
        return (v - lo) / (hi - lo) if hi > lo else 0.5

    def key(r: dict[str, Any]) -> tuple[int, float, str]:
        if r["relevance_score"] is None:
            return (0, 0.0, r["date_added"])  # unscored → bottom
        rn = _norm(float(r["relevance_score"]), rlo, rhi)
        gn = _norm(float(r["goal_sim"]), glo, ghi) if r.get("goal_sim") is not None else 0.0
        return (1, (1.0 - w) * rn + w * gn, r["date_added"])

    unread.sort(key=key, reverse=True)


def sort_unread(unread: list[dict[str, Any]], *, model_ready: bool) -> None:
    """Order the unread queue IN PLACE (the queue's normal, non-search order).

    Gate ready + goals set → goal-blended (attaches ``goal_sim`` per row); gate
    ready + no goals → gate-score-then-recency; gate not ready → priority-tier
    then recency. Only ORDER changes; banding stays from the gate score."""
    if model_ready:
        goal_sims = _goal_affinity([r["item_key"] for r in unread]) if _GOAL_RERANK_WEIGHT > 0 else {}
        for r in unread:
            r["goal_sim"] = goal_sims.get(r["item_key"])
        if goal_sims:
            _blended_sort(unread)
        else:
            unread.sort(
                key=lambda c: (c["relevance_score"] is not None, c["relevance_score"] or 0.0, c["date_added"]),
                reverse=True,
            )
    else:
        unread.sort(
            key=lambda c: (_PRIORITY_RANK.get(c["reading_priority"], 0), c["date_added"]),
            reverse=True,
        )


__all__ = [
    "_GOAL_RERANK_WEIGHT",
    "_PRIORITY_RANK",
    "_content_key",
    "_dedup_by_content",
    "_goal_affinity",
    "_blended_sort",
    "sort_unread",
]
