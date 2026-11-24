"""Post-scoring queue prep + ORDERING helpers (split from ``reading_queue`` to keep
it ≤500 LOC): row assembly + partition (``_build_recs``), content de-duplication,
and goal-aware re-rank (``_order_and_dedup``).

The ordering helpers operate on the already-scored unread records and only change
their ORDER — banding/tags/distribution stay computed from the gate's relevance
score, so the ``derivation == prediction`` invariant is untouched. ``reading_queue``
re-exports these so ``reading_queue._dedup_by_content`` etc. remain the public seam.
    """
from __future__ import annotations

import re
from statistics import median
from typing import Any

from zotero_summarizer.domain import TRIAGE_APPROVED_TAG_TOKEN, score_to_priority
from zotero_summarizer.services.model.quality_flags import band_primary_enabled, quality_promote_enabled
from zotero_summarizer.services.emoji_signals import HARD_VETO_EMOJIS, READ_EMOJIS
from zotero_summarizer.services.library._row_nearest import attach_nearest_kept, corpus_cache
from zotero_summarizer.services.library._row_why import concrete_why
from zotero_summarizer.services.library._score_distribution import _entry_prestige
from zotero_summarizer.services.library._score_distribution import entry_prestige_evidence
from zotero_summarizer.services.model.rank_blend import (
    GOAL_BLEND_WEIGHT,
    blend_parts,
    blend_scores,
    order_within_bands,
    promote_band,
    quality_bonus,
)

# "Read / handled" = engaged-with (any non-meta engagement emoji) or vetoed.
# Meta emojis (🤖, 🔮, ⚪, 🗣) are excluded — they carry no engagement signal.
# The automated "✅ triage-approved" tag is also excluded: ✅ is an engagement
# emoji, but the triage system applies this tag without user action.
_READ_TAGS: frozenset[str] = READ_EMOJIS | HARD_VETO_EMOJIS
_PREVIEW_CHARS = 260
_TLDR_CHARS = 300
_READ_DECISIONS = frozenset({"read", "skim", "skip"})
_EMAIL_ONLY = re.compile(r"\S+@\S+\.\S+")


def _is_read(tags: list[str]) -> bool:
    return any(
        emoji in tag
        for tag in tags
        if tag and TRIAGE_APPROVED_TAG_TOKEN not in tag
        for emoji in _READ_TAGS
    )

# The blend math + weights (0.4 goal / 0.15 prestige, blind-judge provenance)
# live in ``services/model/rank_blend`` — shared with the Today slate: same
# primitive, two consumers, so the surfaces can never drift.

# Fallback ordering only (gate not ready): priority tier then recency.
_PRIORITY_RANK: dict[str, int] = {
    "must_read": 3, "should_read": 2, "could_read": 1, "": 0, "dont_read": -1,
}


def _plain_authors(authors: Any) -> Any:
    """Some feeds put a publisher e-mail in the creator field; show no author instead."""
    if isinstance(authors, str) and _EMAIL_ONLY.fullmatch(authors.strip()):
        return ""
    return authors or ""


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
    cache = corpus_cache() if item_keys else None
    return cache.goal_affinity_for_items(item_keys) if cache is not None else {}


def _known_prestige(rec: dict[str, Any]) -> float | None:
    """The row's prestige score ONLY when it is real OpenAlex evidence
    (``prestige_known``) — else None. Cold-start / uncited / no-record rows have
    no *known* prestige, so the blend treats them as median (never penalised)."""
    if not rec.get("prestige_known"):
        return None
    val = rec.get("prestige_score")
    return float(val) if isinstance(val, (int, float)) else None


def _blend_inputs(scored: list[dict[str, Any]]) -> tuple[list[float], list[float | None], list[float | None]]:
    """The scored cohort as the blend's parallel lists (relevance, goal_sim, KNOWN prestige)."""
    return (
        [float(r["relevance_score"]) for r in scored],
        [None if r.get("goal_sim") is None else float(r["goal_sim"]) for r in scored],
        [_known_prestige(r) for r in scored],
    )


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
    keys = blend_scores(*_blend_inputs(scored))
    base = sorted(zip(scored, keys), key=lambda pair: (pair[1], pair[0]["date_added"]), reverse=True)
    use_band = band_primary_enabled()
    order = order_within_bands(
        [score_to_priority(r["relevance_score"]) for r, _ in base],
        [(key + quality_bonus(r.get("quality_band"), r.get("quality_grade"), use_band=use_band),
          r["date_added"]) for r, key in base],
    )
    unscored = sorted(
        (r for r in unread if r["relevance_score"] is None),
        key=lambda r: r["date_added"], reverse=True,
    )
    unread[:] = [base[i][0] for i in order] + unscored


def _group_rank(rec: dict[str, Any], lift_must: bool) -> int:
    """0 = the owner's own Must read (``decided`` + ``user_priority``: a machine
    label is not a decision), 1 = still to decide, 2 = otherwise decided."""
    if not rec.get("decided"):
        return 1
    return 0 if lift_must and rec.get("user_priority") == "must_read" else 2


def _quality_share(rec: dict[str, Any]) -> float | None:
    """The review's share of passed checklist items (0..1), or None without an applicable review."""
    met, applicable = rec.get("quality_met"), rec.get("quality_applicable")
    if not isinstance(met, (int, float)) or not isinstance(applicable, (int, float)) or applicable <= 0:
        return None
    return min(1.0, max(0.0, met / applicable))


def rank_explain(unread: list[dict[str, Any]]) -> dict[str, Any]:
    """Attach the blend's parts so the client can re-order the queue (Read-next presets).

    Call AFTER ``sort_unread`` (it fills the missing ``goal_sim``) and BEFORE
    ``_dedup_by_content`` — the min-max cohort is exactly the one ``_blended_sort``
    ranked. Every row gets ``rank_group`` (``_group_rank``); every scored row gets
    ``rank_parts`` (``blend_parts`` terms + raw ``quality``, KNOWN signals only).
    Returns the response fields ``rank_coverage`` ``{signal: {present, of}}``,
    ``rank_fill`` (the term of an unknown row) and ``balanced_weights``; ``{}`` when
    nothing is scored. Ranking itself is untouched."""
    for rec in unread:
        rec["rank_group"] = _group_rank(rec, True)
    scored = [r for r in unread if r["relevance_score"] is not None]
    if not scored:
        return {}
    parts, fill, weights = blend_parts(*_blend_inputs(scored))
    qualities = []
    for rec, row in zip(scored, parts):
        share = _quality_share(rec)
        if share is not None:
            row["quality"] = round(share, 3)
            qualities.append(row["quality"])
        rec["rank_parts"] = row
    if qualities:
        fill["quality"] = round(median(qualities), 3)
    coverage = {
        signal: {"present": sum(1 for row in parts if signal in row), "of": len(scored)}
        for signal in ("relevance", "goals", "prestige", "quality")
    }
    return {"rank_coverage": coverage, "rank_fill": fill, "balanced_weights": weights}


def sort_unread(unread: list[dict[str, Any]], *, model_ready: bool, lift_must: bool = True) -> None:
    """Order the unread queue IN PLACE (the queue's normal, non-search order).

    Gate ready → relevance × goal × prestige blend (attaches ``goal_sim`` per row;
    each optional term folds into relevance when its signal is absent, so with no
    goals AND no known prestige this is gate-score-then-recency). Gate not ready →
    priority-tier then recency. Only ORDER changes; banding stays from the gate
    score. See ``_blended_sort`` / ``rank_blend.GOAL_BLEND_WEIGHT``. ``lift_must``
    puts the owner's own Must read first (off for a text search)."""
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
    # One stable pass (blended order kept within each group, nothing hidden):
    # the owner's own unread Must read lead as a group, then what still needs a
    # decision, then the rest of the papers the owner already decided (own verdict).
    if any(r.get("decided") for r in unread):
        unread.sort(key=lambda c: _group_rank(c, lift_must))


def _review_row_fields(review: dict[str, Any] | None) -> dict[str, Any]:
    """Display-only row fields from the current projected review (``None`` → all null).

    ``has_code``: true = a repo was found and is not known dead; false = ``code_link``
    present but nothing found / dead repo; null = no review or no ``code_link`` key."""
    digest = (review or {}).get("digest")
    if not digest:
        return {"review_digest": None, "read_decision": None, "estimated_read_minutes": None, "has_code": None}
    decision = str(digest.get("read_decision") or "")
    minutes = digest.get("estimated_read_minutes")
    link = review.get("code_link")
    has_code = None if not isinstance(link, dict) else bool(link.get("found")) and link.get("exists") is not False
    return {
        "review_digest": {"tldr": str(digest.get("tldr") or "")[:_TLDR_CHARS]},
        "read_decision": decision if decision in _READ_DECISIONS else None,
        "estimated_read_minutes": minutes if isinstance(minutes, int) and not isinstance(minutes, bool) else None,
        "has_code": has_code,
    }


def _build_recs(
    rows: list[dict[str, Any]],
    *,
    cached: dict[str, Any],
    verdict_priority: dict[str, str],
    reviews: dict[str, Any],
    proposed_verdicts: dict[str, Any],
    own_verdicts: frozenset[str] | set[str] = frozenset(),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition library rows into ``(unread, read/handled)`` queue records.

    "Handled" = engaged (emoji) OR explicitly rejected (``dont_read``); those drop
    out of the unread queue. A POSITIVE verdict is a reading intent — the paper
    stays in ``unread`` but is flagged ``decided`` (own verdict, ``own_verdicts``)
    and is grouped by ``sort_unread`` (own Must read first, other decided last). The
    ``proposed_verdict`` and quality fields are display-only sidecars (never fed to
    the hide/pin logic)."""
    from zotero_summarizer.services.library.review_fleet.verdict_store import proposal_matches_review

    unread: list[dict[str, Any]] = []
    read: list[dict[str, Any]] = []
    for it in rows:
        is_read = _is_read(it.get("tags") or [])
        user_priority = verdict_priority.get(it["item_key"], "")
        entry = cached.get(it["item_key"])
        prestige_score, prestige_known = _entry_prestige(entry)
        author_h_index, prestige_evidence = entry_prestige_evidence(entry)
        qual = (reviews.get(it["item_key"]) or {}).get("quality") or {}
        quality_grade = qual.get("grade") or None
        quality_band = qual.get("quality_band") or None
        relevance_score = entry["relevance_score"] if entry else None
        goal_sim = entry.get("goal_sim") if entry else None
        # Quality → must_read promotion (OFF by default; ZS_QUALITY_PROMOTE). The
        # gate's compressed regressor never reaches the must_read band on its own,
        # so a high-quality + on-goal + gate-confirmed paper is lifted here. The
        # raw gate band is the floor; promotion only crosses ONE band up (never
        # invents must_read from dont_read). A None signal → no promotion (no
        # hide-on-absent-evidence contract). User verdicts still win (sort_unread).
        reading_priority = it.get("reading_priority") or ""
        if quality_promote_enabled():
            reading_priority = promote_band(
                reading_priority,
                grade=quality_grade,
                relevance_score=relevance_score,
                goal_sim=goal_sim,
            )
        rec = {
            "item_key": it["item_key"],
            "title": it.get("title") or "",
            "authors": _plain_authors(it.get("authors")),
            "venue": it.get("venue") or "",
            "reading_priority": reading_priority,
            "user_priority": user_priority,
            "decided": it["item_key"] in own_verdicts,
            "has_pdf": bool(it.get("has_pdf")),
            "date_added": it.get("date_added") or "",
            # Zotero-column sort fields (client-side sort in the Library UI).
            "publication_date": it.get("publication_date") or "",
            "year": str(it.get("publication_date") or "")[:4],
            # Two lines of the row; the full abstract stays server-side.
            "abstract_preview": " ".join(str(it.get("abstract") or "").split())[:_PREVIEW_CHARS],
            "date_modified": it.get("date_modified") or "",
            "read": is_read,
            "relevance_score": relevance_score,
            # Concrete sentence (see _row_why); None when only bookkeeping features
            # (title length, DOI…) contributed — no reason beats a generic one.
            "why_reason": concrete_why(entry),
            # Carried from the score cache (computed at rescore time) so the open
            # path skips the corpus matmul; None when absent (legacy entry / no
            # embedding) — sort_unread does a cheap live lookup for those.
            "goal_sim": goal_sim,
            "prestige_score": prestige_score,
            "prestige_known": prestige_known,
            # `prestige_evidence` (citation OR author h-index) drives the client
            # Prestige FILTER; `prestige_known` (citation only) stays the conservative
            # banding-FLOOR signal — kept separate on purpose.
            "prestige_evidence": prestige_evidence,
            "max_author_h_index": author_h_index,
            "proposed_verdict": (
                proposed_verdicts.get(it["item_key"])
                if proposal_matches_review(
                    proposed_verdicts.get(it["item_key"]), reviews.get(it["item_key"]),
                ) else None
            ),
            "quality_grade": quality_grade,
            "quality_band": quality_band,
            "quality_met": qual.get("coverage_met"),
            "quality_applicable": qual.get("coverage_applicable"),
            **_review_row_fields(reviews.get(it["item_key"])),
        }
        (read if (is_read or user_priority == "dont_read") else unread).append(rec)
    attach_nearest_kept(unread)
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
    unread copy would show in BOTH lists under ``include_read``).

    The third item is the search flags plus, for the plain (no-search) ranked queue,
    ``"rank"``: the ``rank_explain`` response fields, computed on the pre-dedup cohort."""
    search_flags: dict[str, Any] = {}
    if semantic_requested:
        from zotero_summarizer.services.library import _search
        unread, search_flags = _search.order_unread_semantic(search, unread)
    if not search_flags.get("semantic"):
        sort_unread(unread, model_ready=model_ready, lift_must=not (search or "").strip())
        if model_ready and not (search or "").strip():
            search_flags["rank"] = rank_explain(unread)
    unread = _dedup_by_content(unread)
    _unread_keys = {k for r in unread if (k := _content_key(r))}
    read = [r for r in _dedup_by_content(read) if _content_key(r) not in _unread_keys]
    return unread, read, search_flags


__all__ = [
    "_PRIORITY_RANK",
    "_content_key",
    "_dedup_by_content",
    "_goal_affinity",
    "_known_prestige",
    "_blended_sort",
    "_build_recs",
    "_is_read",
    "_order_and_dedup",
    "rank_explain",
    "sort_unread",
]
