"""Stage-2 (Library) reading queue: rank UNREAD library items by the gate's
relevance score, with a one-line reason, so "what to read next" is explainable.

Why background-cached: scoring an item needs its SPECTER2 embedding, which is
~0.5s/item when not cached — too slow to do for the whole library on every
request. So scores are computed in a background job and cached, mirroring
``services.border_cache``.

Key design points (see the plan):
  * Scoring is NEVER auto-triggered on open. Opening the queue only reads the
    cache, so it's instant; recompute happens solely on an explicit Rescore
    (``refresh=True``). This is the fix for "scoring re-runs slowly on open".
  * Scores survive a gate retrain: the cache stores the gate's
    ``golden_csv_sha256`` only to flag staleness (``scores_stale``), not to wipe
    scores — a retrain no longer forces a full rescore on the next open.
  * Read-status is applied LIVE at request time (current Zotero emoji tags), so
    a paper you just tagged 🧠 drops out immediately, no rescore.
  * The annotation detail reuses these exact cached scores
    (``get_cached_scoring``), so the queue and the "Why this score?" panel agree.
"""
from __future__ import annotations

import json
from typing import Any

from zotero_summarizer.services._common import now_iso_z

from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.services.library import _flight
from zotero_summarizer.services._common import state as get_state
from zotero_summarizer.services.emoji_signals import ALL_EMOJIS, HARD_VETO_EMOJIS
from zotero_summarizer.services.library._ranking import (  # noqa: F401 — re-export the seam
    _blended_sort,
    _content_key,
    _dedup_by_content,
    _goal_affinity,
    sort_unread,
)
from zotero_summarizer.services.library._score_distribution import (
    _HIST_EDGES,  # noqa: F401 — re-export for the stable public seam
    _entry_prestige,
    prestige_floor,
    score_distribution as _score_distribution,
)

# "Read / handled" = engaged-with (any signal emoji) or vetoed.
_HANDLED_EMOJIS: frozenset[str] = frozenset(ALL_EMOJIS) | HARD_VETO_EMOJIS

_CACHE_FILENAME = "reading_queue.json"

# Human labels for the top SHAP reason. (A long-gone PrestigeWaterfall frontend
# component used to mirror this map; the queue card's "why" text is now the only
# consumer.) ``corpus_affinity`` is the ENGAGEMENT signal (pos−neg cosine to the
# library you saved) — the goal-text signal is ``goal_sim``, which is not a SHAP
# feature; labeling affinity "Goal match" here was the label-drift bug.
_FRIENDLY: dict[str, str] = {
    "semantic_match_specter2": "Topic match",
    "corpus_affinity": "Library affinity",
    "prestige_score": "Prestige",
    "nearest_kept_cosine": "Like papers you kept",
    "positive_centroid_cosine": "Like your library",
    "recent_centroid_cosine": "Like recent reads",
    "topic_drift": "Topic drift",
    "author_overlap_count": "Author overlap",
    "has_doi": "Has DOI",
    "has_venue": "Has venue",
    "year_recency": "Recency",
    "title_log_len": "Title length",
    "abstract_log_len": "Abstract length",
}

# ---------------------------------------------------------------------------
# Single-flight background-job state (separate from border_cache's).
# ---------------------------------------------------------------------------
_LATCH = _flight.FlightLatch()


def is_running() -> bool:
    return _LATCH.is_running()


def last_error() -> str | None:
    return _LATCH.last_error()


def try_start() -> bool:
    return _LATCH.try_start()


def finish(error: str | None = None) -> None:
    _LATCH.finish(error)


run_in_background = _flight.run_in_background


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------



def _is_read(tags: list[str]) -> bool:
    return any(tag in _HANDLED_EMOJIS for tag in tags)


def _reader():
    reader = getattr(get_state(), "zotero_reader", None)
    if reader is None:
        raise RuntimeError(
            "Zotero reader unavailable; cannot build the reading queue "
            "(check ZOTERO_DATA_DIR)"
        )
    return reader


def _gate():
    return getattr(get_state(), "classifier_gate", None)


def _gate_sha() -> str | None:
    gate = _gate()
    return gate.golden_csv_sha256 if gate is not None else None


def _cache_path():
    from zotero_summarizer.services.model.classifier_persistence import DEFAULT_MODEL_DIR
    return DEFAULT_MODEL_DIR / _CACHE_FILENAME


def _read_cache(gate_sha: str) -> dict[str, Any]:
    """Return the per-item score dict (whatever is on disk, even if it was
    computed against a different gate). Empty only when no cache file exists."""
    return _read_cache_with_meta(gate_sha)[0]


def _read_cache_with_meta(gate_sha: str) -> tuple[dict[str, Any], str | None, bool]:
    """Return ``(scores, computed_at, stale)``.

    Scores are returned even when the cache's ``gate_sha`` differs from the
    loaded gate's — we never wipe scores on a retrain (that would force a slow
    full rescore on the next open). ``stale`` flags that mismatch so the UI can
    nudge the user to Rescore for scores against the current model.
    ``({}, None, False)`` when no cache file exists.
    """
    path = _cache_path()
    if not path.exists():
        return {}, None, False
    payload = json.loads(path.read_text(encoding="utf-8"))
    stale = payload.get("gate_sha") != gate_sha
    return payload.get("scores") or {}, payload.get("computed_at"), stale


def _write_cache(gate_sha: str, scores: dict[str, Any]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"gate_sha": gate_sha, "computed_at": now_iso_z(), "scores": scores}, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(path)


def scoring_from_prediction(pred: Any) -> dict[str, Any]:
    """Convert a gate ``FeedPrediction`` into the ``scoring`` shape the
    PrestigeWaterfall renders. composite == the gate's raw_score (1–5): the SHAP
    bars sum to it, so the waterfall is internally consistent."""
    from zotero_summarizer.services.model.prestige import percentile_to_score

    aux = pred.aux_context or {}
    # Prestige = field+year-normalized citation percentile mapped to [1,5] via the
    # SAME function the gate feature uses (single source). With no percentile yet
    # (cold-start / uncited) we fall back to the provisional author-reputation
    # prior (``cold_start_prestige``, computed once in _compute_aux_with_context);
    # that may itself be None when the lift is off or no author signal exists.
    # Either way ``citation_percentile`` stays None, so _entry_prestige reports
    # the paper as UNKNOWN and the quality floor never demotes it — raw
    # h-index/venue/cites remain "why" panel context only.
    pct = aux.get("citation_percentile")
    prestige = percentile_to_score(pct) if pct is not None else aux.get("cold_start_prestige")
    shap_top = [
        {"feature": str(c.get("feature") or ""), "value": float(c.get("contribution") or 0.0)}
        for c in (pred.shap_contribs or [])
    ]
    prestige_inputs = {
        k: aux[k]
        for k in (
            "citation_percentile", "max_author_h_index", "venue_works_count",
            "cited_by_count", "max_author_field_percentile", "cold_start_prestige",
        )
        if aux.get(k) is not None
    }
    return {
        "composite_score": float(pred.raw_score),
        "prestige_score": prestige,
        "shap_top": shap_top,
        "prestige_inputs": prestige_inputs or None,
    }


def _why_reason(scoring: dict[str, Any]) -> str | None:
    """One-line reason = the top contributing feature (excluding the baseline)."""
    candidates = [s for s in (scoring.get("shap_top") or []) if s["feature"] != "bias"]
    if not candidates:
        return None
    top = max(candidates, key=lambda s: s["value"])
    if top["value"] <= 0:
        top = max(candidates, key=lambda s: abs(s["value"]))
    name = top["feature"]
    return _FRIENDLY.get(name, name.replace("_", " ").title())


def _score_items(
    items: list[dict[str, Any]], *, return_shap: bool, prestige_network: bool = True,
) -> dict[str, Any]:
    """Score items with the loaded gate → ``{item_key: FeedPrediction}``.
    Empty when the gate is unavailable (caller falls back).

    ``prestige_network=False`` makes the gate's OpenAlex prestige lookup
    cache-only (no network) — used by the interactive ``live_scoring`` path so
    opening a paper never blocks on a multi-second OpenAlex search."""
    gate = _gate()
    if gate is None or not items:
        return {}
    settings_ = get_settings()
    config = get_state().app_state.config
    preds = gate.predict(
        items,
        corpus_db_path=settings_.corpus_db_path,
        goals_config=config,
        return_shap=return_shap,
        prestige_network=prestige_network,
    )
    return {p.item_key: p for p in preds}


_SCORE_BATCH = 50

# Cache entry for an item the gate produced no prediction for. Recording it
# (rather than skipping) is what stops the perpetual "Scoring…" spinner: an
# un-scorable item is marked attempted once, so it no longer counts as
# "missing" and never re-triggers the background pass.
_UNSCORABLE = {"relevance_score": None, "why_reason": None, "scoring": None, "unscorable": True}


def _compute_scores_into_cache(gate_sha: str, *, full: bool = False) -> None:
    """Background: score unread library items for ``gate_sha``.

    Every *attempted* item gets a cache entry — a real score or an
    ``_UNSCORABLE`` sentinel — so an item the gate can't score is recorded once
    and never re-triggers the pass. ``full=True`` (manual Rescore) re-attempts
    EVERY item (including prior sentinels) but starts from the EXISTING cache
    and overwrites entries batch by batch — mid-run readers (the queue, the
    rank/tag syncs) see "old score until replaced by new score", and a mid-run
    crash keeps the old scores. The old wipe-up-front semantics left the cache
    near-empty for the minutes a whole-library pass takes and truncated on a
    crash. Entries for items that left the library are purged only after the
    pass COMPLETES (what the wipe used to provide). Batched with a flush after
    each so partial results appear while a large run is still in progress.
    """
    try:
        # Whole-library scan (read AND unread) so every scorable paper gets a
        # cached score — needed for the global Zotero rank. The displayed queue
        # routes read items aside separately; here we score them too. No-abstract
        # items are skipped (the gate needs an abstract) and handled at rank time.
        page = _reader().get_all_items()
        items = [
            it for it in page.get("items", [])
            if (it.get("abstract") or "").strip()
        ]
        cached = _read_cache(gate_sha)
        todo = items if full else [it for it in items if it["item_key"] not in cached]
        for start in range(0, len(todo), _SCORE_BATCH):
            chunk = todo[start:start + _SCORE_BATCH]
            preds = _score_items(chunk, return_shap=True)
            for it in chunk:
                pred = preds.get(it["item_key"])
                if pred is None:
                    cached[it["item_key"]] = dict(_UNSCORABLE)
                    continue
                scoring = scoring_from_prediction(pred)
                cached[it["item_key"]] = {
                    "relevance_score": float(pred.raw_score),
                    "why_reason": _why_reason(scoring),
                    "scoring": scoring,
                }
            _write_cache(gate_sha, cached)
        if full:
            # Complete pass: now (and only now) drop entries for items no longer
            # in the library, so deletions don't linger in the cache forever.
            current_keys = {it["item_key"] for it in items}
            cached = {k: v for k, v in cached.items() if k in current_keys}
        # Persist once more so a full rebuild with an empty todo still lands.
        _write_cache(gate_sha, cached)
    except Exception as exc:  # noqa: BLE001 — surfaced via last_error + re-raised
        finish(error=f"{type(exc).__name__}: {exc}")
        raise
    finish(error=None)


def live_scoring(item: dict[str, Any]) -> dict[str, Any] | None:
    """Score a single item on-demand → ``scoring`` dict (or None if the gate is
    off or the item has no abstract). Used by the annotation detail when the
    item isn't in the queue cache yet, so an opened paper still explains itself."""
    if not str(item.get("title") or "").strip() or not str(item.get("abstract") or "").strip():
        return None
    key = item.get("item_key") or item.get("item_id")
    # Cache-only prestige: this runs on the request path (opening a paper's "why
    # this score?" detail), so it must never block on an OpenAlex network search.
    # The score for an item that was triaged/rescored is already in the cache.
    preds = _score_items([item], return_shap=True, prestige_network=False)
    pred = preds.get(key)
    return scoring_from_prediction(pred) if pred is not None else None


def read_score_cache() -> dict[str, dict[str, Any]]:
    """The current gate's cached scores, keyed by item_key (scored entries only):
    ``{relevance, prestige, prestige_known}``. Empty when the gate is off or
    nothing is scored yet. Public seam for the relevance-tag sync so it reads the
    cache once (relevance for the band, prestige for the quality floor)."""
    gate_sha = _gate_sha()
    if gate_sha is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, entry in _read_cache(gate_sha).items():
        score = entry.get("relevance_score")
        if score is None:
            continue
        prestige_score, prestige_known = _entry_prestige(entry)
        out[str(key)] = {
            "relevance": float(score),
            "prestige": prestige_score,
            "prestige_known": prestige_known,
        }
    return out


def get_cached_scoring(item_key: str) -> dict[str, Any] | None:
    """Return the cached ``scoring`` dict for an item (for the annotation detail
    to reuse, so the queue and the waterfall show the same score). None when the
    gate is off or the item hasn't been scored yet."""
    gate_sha = _gate_sha()
    if gate_sha is None:
        return None
    entry = _read_cache(gate_sha).get(item_key)
    return entry.get("scoring") if entry else None


def _verdicted_keys() -> frozenset[str]:
    """Item keys the user has cast any verdict on (``label_verdicts``).

    These are "handled" — read AND labelled — so they drop out of Read next
    (e.g. a paper you marked ``dont_read`` must not reappear), mirroring the
    Today slate's handled-filter. Any verdict counts, not just ``dont_read``.

    Uses the UNCAPPED key reader: a paged fetch with a fixed limit silently
    un-hides handled papers once the table outgrows it — the same failure
    class as the 500-cap training bug fixed in June 2026.
    """
    from zotero_summarizer.storage import repositories

    return frozenset(repositories.list_label_verdict_keys(get_settings().triage_db_path))


def build_reading_queue(
    *,
    include_read: bool = False,
    limit: int = 30,
    refresh: bool = False,
    collection: str = "",
    tag: str = "",
    search: str = "",
    semantic: bool = False,
) -> dict[str, Any]:
    """Ranked queue over the WHOLE library. Read/handled status is applied live;
    scores come from the background cache. Scoring is NEVER auto-triggered on open
    — it runs only on an explicit ``refresh`` (the Rescore button), so opening just
    reads + ranks the library (no scoring) even right after a gate retrain (stale
    scores show with ``scores_stale``). ``limit`` caps the returned list (the
    frontend requests the whole library and reveals it incrementally).

    ``collection``/``tag``/``search`` filter the rows via the reader's own
    filtering; the score cache is global (a Rescore scans the whole library), so
    filters only select which cached scores appear.

    Returns ``{status, items, total_unread, read_hidden, model_ready, error,
    computed_at, scores_stale}``."""
    # "Meaning" search ranks the WHOLE (collection/tag-scoped) library by hybrid
    # relevance, so the substring filter is bypassed; "Exact" keeps it.
    semantic_requested = bool(semantic and str(search or "").strip())
    rows = _reader().get_all_items(
        collection_key=collection or None,
        tag=tag or None,
        search=None if semantic_requested else (search or None),
        include_abstract=False,  # the queue never displays the abstract
    ).get("items", [])
    gate_sha = _gate_sha()
    model_ready = gate_sha is not None
    cached, computed_at, stale = (
        _read_cache_with_meta(gate_sha) if model_ready else ({}, None, False)
    )
    verdicted = _verdicted_keys()

    unread: list[dict[str, Any]] = []
    read: list[dict[str, Any]] = []
    for it in rows:
        tags = it.get("tags") or []
        is_read = _is_read(tags)
        # "Handled" = engaged (emoji) OR the user has cast a verdict on it.
        handled = is_read or it["item_key"] in verdicted
        entry = cached.get(it["item_key"])
        prestige_score, prestige_known = _entry_prestige(entry)
        rec = {
            "item_key": it["item_key"],
            "title": it.get("title") or "",
            "authors": it.get("authors") or "",
            "reading_priority": it.get("reading_priority") or "",
            "has_pdf": bool(it.get("has_pdf")),
            "date_added": it.get("date_added") or "",
            "read": is_read,
            "relevance_score": entry["relevance_score"] if entry else None,
            "why_reason": entry["why_reason"] if entry else None,
            "prestige_score": prestige_score,
            "prestige_known": prestige_known,
        }
        if handled:
            read.append(rec)
        else:
            unread.append(rec)

    # "Meaning" search: hybrid (BM25 + dense + cross-encoder rerank) re-ranks the
    # unread set and replaces it with the ranked top-N — order only, scores/banding
    # untouched. Falls back to the normal order when the corpus is off / no match.
    search_flags: dict[str, Any] = {}
    if semantic_requested:
        from zotero_summarizer.services.library import _search
        unread, search_flags = _search.order_unread_semantic(search, unread)
    if not search_flags.get("semantic"):
        # Normal queue order (goal-blended when the gate is ready; else recency).
        sort_unread(unread, model_ready=model_ready)

    # Collapse duplicate library items (same paper imported twice) AFTER the sort
    # so the best-scored copy survives — duplicates were wasting top slots.
    unread = _dedup_by_content(unread)
    # Dedup `read` too, AND drop any read copy whose paper already survives in
    # `unread` — otherwise a paper with one read + one unread copy would show in
    # BOTH lists under include_read (now routine with the whole-library read). The
    # actionable unread copy wins; titleless rows (empty key) are never merged.
    _unread_keys = {k for r in unread if (k := _content_key(r))}
    read = [r for r in _dedup_by_content(read) if _content_key(r) not in _unread_keys]

    # Compute ONLY on an explicit refresh (Rescore). A prior crash (last_error
    # set) is surfaced and not auto-retried — Rescore clears it.
    err = last_error() if model_ready else None
    if model_ready and refresh:
        if try_start():
            run_in_background(lambda: _compute_scores_into_cache(gate_sha, full=True))
        status = "computing"
    elif is_running():
        status = "computing"
    elif err:
        status = "error"
    else:
        status = "ready"

    items = unread[:limit]
    if include_read:
        read.sort(key=lambda c: c["date_added"], reverse=True)
        items = items + read[:limit]

    return {
        "status": status,
        "items": items,
        "total_unread": len(unread),
        "read_hidden": len(read),
        "model_ready": model_ready,
        "error": err if status == "error" else None,
        "computed_at": computed_at,
        "scores_stale": bool(stale and cached),
        # Hybrid-search flags for the UI (absent/False in normal/Exact mode).
        "semantic": bool(search_flags.get("semantic")),
        "reranked": bool(search_flags.get("reranked")),
        "reranker_loading": bool(search_flags.get("reranker_loading")),
        "semantic_unavailable": bool(semantic_requested and not search_flags.get("semantic")),
        # Distribution over the full unread queue (not just the shown slice), so
        # the histogram reflects everything the current filter selects. The
        # prestige floor (median of the library's KNOWN prestige) gates the
        # effective top bands so the legend matches the quality-gated tags.
        "distribution": _score_distribution(
            unread, prestige_floor([_entry_prestige(e) for e in cached.values()])
        ),
    }
