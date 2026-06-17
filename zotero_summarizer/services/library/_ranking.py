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
from zotero_summarizer.services.model.rank_blend import GOAL_BLEND_WEIGHT, blend_scores

# The blend math + weights (0.4 goal / 0.15 prestige, blind-judge provenance)
# live in ``services/model/rank_blend`` — shared with the Today slate: same
# primitive, two consumers, so the surfaces can never drift.

# Fallback ordering only (gate not ready): priority tier then recency.
_PRIORITY_RANK: dict[str, int] = {
    "must_read": 3, "should_read": 2, "could_read": 1, "": 0, "dont_read": -1,
}

# The user's explicit verdict OUTRANKS the model: a paper labelled must/should/
# could_read pins to the top of Read next (priority order), so a label makes it
# findable rather than burying it in the ranked majority. ``dont_read`` never
# reaches the sort — it's handled-filtered out of ``unread`` upstream.
_USER_VERDICT_RANK: dict[str, int] = {"must_read": 3, "should_read": 2, "could_read": 1}


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


# Bounded deep-review QUALITY lift (user-requested: "качественные статьи наверху").
# Added to the normalized blend key, capped so it can NEVER leap a relevance band or
# override the measured goal/prestige blend (the primary signal). Library-only; a
# paper with no deep review (no grade) gets 0. A/B float up, D nudges down slightly.
_QUALITY_BONUS: dict[str, float] = {"A": 0.06, "B": 0.03, "C": 0.0, "D": -0.02}


def _quality_bonus(rec: dict[str, Any]) -> float:
    return _QUALITY_BONUS.get(str(rec.get("quality_grade") or "").upper(), 0.0)


def _known_prestige(rec: dict[str, Any]) -> float | None:
    """The row's prestige score ONLY when it is real OpenAlex evidence
    (``prestige_known``) — else None. Cold-start / uncited / no-record rows have
    no *known* prestige, so the blend treats them as median (never penalised)."""
    if not rec.get("prestige_known"):
        return None
    val = rec.get("prestige_score")
    return float(val) if isinstance(val, (int, float)) else None


def _blended_sort(unread: list[dict[str, Any]]) -> None:
    """Sort the unread queue in place by the shared relevance × goal × prestige
    blend (``services/model/rank_blend.blend_scores`` — min-max per cohort,
    weight fold-back for absent signals, median-of-known for unknown prestige),
    so on-goal AND high-quality papers the gate under-ranks rise. Unscored items
    sink to the bottom; banding is untouched (computed elsewhere from the gate
    score). This module only ADAPTS library records to the blend: relevance =
    gate score, goal = ``goal_sim``, prestige = KNOWN evidence via
    ``_known_prestige`` (never a derived fallback), tie-break = ``date_added``."""
    scored = [r for r in unread if r["relevance_score"] is not None]
    keys = blend_scores(
        [float(r["relevance_score"]) for r in scored],
        [None if r.get("goal_sim") is None else float(r["goal_sim"]) for r in scored],
        [_known_prestige(r) for r in scored],
    )
    blended = {id(r): k for r, k in zip(scored, keys)}

    def key(r: dict[str, Any]) -> tuple[int, float, str]:
        if r["relevance_score"] is None:
            return (0, 0.0, r["date_added"])  # unscored → bottom
        return (1, blended[id(r)] + _quality_bonus(r), r["date_added"])

    unread.sort(key=key, reverse=True)


def sort_unread(unread: list[dict[str, Any]], *, model_ready: bool) -> None:
    """Order the unread queue IN PLACE (the queue's normal, non-search order).

    Gate ready → relevance × goal × prestige blend (attaches ``goal_sim`` per row;
    each optional term folds into relevance when its signal is absent, so with no
    goals AND no known prestige this is gate-score-then-recency). Gate not ready →
    priority-tier then recency. Only ORDER changes; banding stays from the gate
    score. See ``_blended_sort`` / ``rank_blend.GOAL_BLEND_WEIGHT``."""
    if model_ready:
        goal_sims = _goal_affinity([r["item_key"] for r in unread]) if GOAL_BLEND_WEIGHT > 0 else {}
        for r in unread:
            r["goal_sim"] = goal_sims.get(r["item_key"])
        # One ordering path: the blend self-degrades to pure relevance when no
        # goal/prestige signal exists, so the prestige lift applies even with no
        # goals set ("best of the best on top" regardless of goal config).
        _blended_sort(unread)
    else:
        unread.sort(
            key=lambda c: (_PRIORITY_RANK.get(c["reading_priority"], 0), c["date_added"]),
            reverse=True,
        )
    # The user's explicit verdict wins over the model's order: pin labelled
    # papers (must/should/could_read) to the top in priority order. Stable, so
    # the blended/recency order is preserved within each verdict tier AND across
    # the unlabelled rank-0 majority (a no-op when nothing is labelled).
    if any(r.get("user_priority") in _USER_VERDICT_RANK for r in unread):
        unread.sort(
            key=lambda c: _USER_VERDICT_RANK.get(c.get("user_priority") or "", 0),
            reverse=True,
        )


__all__ = [
    "_PRIORITY_RANK",
    "_USER_VERDICT_RANK",
    "_content_key",
    "_dedup_by_content",
    "_goal_affinity",
    "_known_prestige",
    "_quality_bonus",
    "_blended_sort",
    "sort_unread",
]
