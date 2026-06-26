"""Post-scoring queue prep + ORDERING helpers (split from ``reading_queue`` to keep
it ≤500 LOC): row assembly + partition (``_build_recs``), content de-duplication,
and goal-aware re-rank (``_order_and_dedup``).

The ordering helpers operate on the already-scored unread records and only change
their ORDER — banding/tags/distribution stay computed from the gate's relevance
score, so the ``derivation == prediction`` invariant is untouched. ``reading_queue``
re-exports these so ``reading_queue._dedup_by_content`` etc. remain the public seam.
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.services._common import band_primary_enabled
from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.services._common import state as get_state
from zotero_summarizer.services.emoji_signals import ALL_EMOJIS, HARD_VETO_EMOJIS
from zotero_summarizer.services.library._score_distribution import _entry_prestige
from zotero_summarizer.services.model.rank_blend import (
    GOAL_BLEND_WEIGHT,
    blend_scores,
    quality_bonus,
)

# "Read / handled" = engaged-with (any signal emoji) or vetoed.
_HANDLED_EMOJIS: frozenset[str] = frozenset(ALL_EMOJIS) | HARD_VETO_EMOJIS


def _is_read(tags: list[str]) -> bool:
    return any(tag in _HANDLED_EMOJIS for tag in tags)

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
# The math lives in the shared, pure ``rank_blend.quality_bonus`` (one definition
# for both consumers); this module only adapts the queue record + resolves the mode.
# Added to the normalized blend key, capped so it only reorders WITHIN a relevance
# band (banding derives from the raw 1-5 score, not this key). A paper with no
# review gets 0.


def _quality_bonus(rec: dict[str, Any]) -> float:
    """Capped quality lift for a queue row — the shared pure
    ``rank_blend.quality_bonus`` adapted to the reading-queue record shape; the
    grade-only-vs-band-primary mode is the shared ``_common.band_primary_enabled``."""
    return quality_bonus(
        rec.get("quality_band"), rec.get("quality_grade"), use_band=band_primary_enabled()
    )


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
        # goal_sim is precomputed at rescore time and carried on each rec from the
        # score cache (_build_recs). Only items still missing it — legacy cache
        # entries or rows not yet rescored — need a live lookup; that's cheap (the
        # corpus matrix is process-cached). A scored row with no corpus embedding
        # stays None and is simply re-checked (a dict miss), never blocking.
        if GOAL_BLEND_WEIGHT > 0:
            missing = [
                r["item_key"] for r in unread
                if r.get("relevance_score") is not None and r.get("goal_sim") is None
            ]
            if missing:
                sims = _goal_affinity(missing)
                for r in unread:
                    if r["item_key"] in sims:
                        r["goal_sim"] = sims[r["item_key"]]
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


def _build_recs(
    rows: list[dict[str, Any]],
    *,
    cached: dict[str, Any],
    verdict_priority: dict[str, str],
    reviews: dict[str, Any],
    proposed_verdicts: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition library rows into ``(unread, read/handled)`` queue records.

    "Handled" = engaged (emoji) OR explicitly rejected (``dont_read``); those drop
    out of the unread queue. A POSITIVE verdict is a reading intent — the paper
    stays in ``unread`` and pins to the top (``sort_unread``). The
    ``proposed_verdict`` and quality fields are display-only sidecars (never fed to
    the hide/pin logic)."""
    unread: list[dict[str, Any]] = []
    read: list[dict[str, Any]] = []
    for it in rows:
        is_read = _is_read(it.get("tags") or [])
        user_priority = verdict_priority.get(it["item_key"], "")
        entry = cached.get(it["item_key"])
        prestige_score, prestige_known = _entry_prestige(entry)
        qual = (reviews.get(it["item_key"]) or {}).get("quality") or {}
        rec = {
            "item_key": it["item_key"],
            "title": it.get("title") or "",
            "authors": it.get("authors") or "",
            "reading_priority": it.get("reading_priority") or "",
            "user_priority": user_priority,
            "has_pdf": bool(it.get("has_pdf")),
            "date_added": it.get("date_added") or "",
            "read": is_read,
            "relevance_score": entry["relevance_score"] if entry else None,
            "why_reason": entry["why_reason"] if entry else None,
            # Carried from the score cache (computed at rescore time) so the open
            # path skips the corpus matmul; None when absent (legacy entry / no
            # embedding) — sort_unread does a cheap live lookup for those.
            "goal_sim": entry.get("goal_sim") if entry else None,
            "prestige_score": prestige_score,
            "prestige_known": prestige_known,
            "proposed_verdict": proposed_verdicts.get(it["item_key"]),
            "quality_grade": qual.get("grade") or None,
            "quality_band": qual.get("quality_band") or None,
        }
        (read if (is_read or user_priority == "dont_read") else unread).append(rec)
    return unread, read


def _order_and_dedup(
    unread: list[dict[str, Any]],
    read: list[dict[str, Any]],
    *,
    search: str,
    semantic_requested: bool,
    model_ready: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Order the unread set then collapse duplicate library items.

    "Meaning" search hybrid-re-ranks the unread set (order only — scores/banding
    untouched), falling back to the normal goal-blended/recency sort when the
    corpus is off / no match. Dedup runs AFTER the sort so the best-scored copy
    survives a top slot; ``read`` is de-duped too and any read copy whose paper
    already survives in ``unread`` is dropped (else a paper with one read + one
    unread copy would show in BOTH lists under ``include_read``)."""
    search_flags: dict[str, Any] = {}
    if semantic_requested:
        from zotero_summarizer.services.library import _search
        unread, search_flags = _search.order_unread_semantic(search, unread)
    if not search_flags.get("semantic"):
        sort_unread(unread, model_ready=model_ready)
    unread = _dedup_by_content(unread)
    _unread_keys = {k for r in unread if (k := _content_key(r))}
    read = [r for r in _dedup_by_content(read) if _content_key(r) not in _unread_keys]
    return unread, read, search_flags


__all__ = [
    "_PRIORITY_RANK",
    "_USER_VERDICT_RANK",
    "_content_key",
    "_dedup_by_content",
    "_goal_affinity",
    "_known_prestige",
    "_quality_bonus",
    "_blended_sort",
    "_build_recs",
    "_is_read",
    "_order_and_dedup",
    "sort_unread",
]
