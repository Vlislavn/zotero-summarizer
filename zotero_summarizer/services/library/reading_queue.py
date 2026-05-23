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
import math
import threading
from typing import Any

from zotero_summarizer.services._common import now_iso_z

from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.services._common import state as get_state
from zotero_summarizer.services.emoji_signals import ALL_EMOJIS, HARD_VETO_EMOJIS

# "Read / handled" = engaged-with (any signal emoji) or vetoed.
_HANDLED_EMOJIS: frozenset[str] = frozenset(ALL_EMOJIS) | HARD_VETO_EMOJIS

# Fallback ordering only (model not ready): priority tier then recency.
_PRIORITY_RANK: dict[str, int] = {
    "must_read": 3, "should_read": 2, "could_read": 1, "": 0, "dont_read": -1,
}

# get_items clamps its limit to 500; that's the scan window ("most-recent 500").
_SCAN_LIMIT = 500
_CACHE_FILENAME = "reading_queue.json"

# Human labels for the top SHAP reason — mirrors PrestigeWaterfall.featureLabel.
_FRIENDLY: dict[str, str] = {
    "semantic_match_specter2": "Topic match",
    "corpus_affinity": "Goal match",
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
_LOCK = threading.Lock()
_RUNNING = False
_LAST_ERROR: str | None = None


def is_running() -> bool:
    with _LOCK:
        return _RUNNING


def last_error() -> str | None:
    with _LOCK:
        return _LAST_ERROR


def try_start() -> bool:
    global _RUNNING, _LAST_ERROR
    with _LOCK:
        if _RUNNING:
            return False
        _RUNNING = True
        _LAST_ERROR = None
        return True


def finish(error: str | None = None) -> None:
    global _RUNNING, _LAST_ERROR
    with _LOCK:
        _RUNNING = False
        _LAST_ERROR = error


def run_in_background(target) -> None:
    threading.Thread(target=target, daemon=True).start()


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
    aux = pred.aux_context or {}
    h = aux.get("max_author_h_index")
    prestige = None
    if h is not None:
        prestige = min(1.0, math.log1p(max(0.0, float(h))) / math.log1p(30.0))
    shap_top = [
        {"feature": str(c.get("feature") or ""), "value": float(c.get("contribution") or 0.0)}
        for c in (pred.shap_contribs or [])
    ]
    prestige_inputs = {
        k: aux[k] for k in ("max_author_h_index", "venue_works_count", "cited_by_count")
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


def _score_items(items: list[dict[str, Any]], *, return_shap: bool) -> dict[str, Any]:
    """Score items with the loaded gate → ``{item_key: FeedPrediction}``.
    Empty when the gate is unavailable (caller falls back)."""
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
    and never re-triggers the pass. ``full=True`` (manual Rescore) rebuilds from
    scratch, re-attempting prior sentinels. Batched with a flush after each so
    partial results appear while a large run is still in progress.
    """
    try:
        page = _reader().get_items(limit=_SCAN_LIMIT)
        cached = {} if full else _read_cache(gate_sha)
        todo = [
            it for it in page.get("items", [])
            if not _is_read(it.get("tags") or [])
            and (it.get("abstract") or "").strip()
            and it["item_key"] not in cached
        ]
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
    preds = _score_items([item], return_shap=True)
    pred = preds.get(key)
    return scoring_from_prediction(pred) if pred is not None else None


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
    """
    from zotero_summarizer.storage import repositories

    rows = repositories.list_label_verdicts(get_settings().triage_db_path, limit=5000)
    return frozenset(str(r["item_key"]) for r in rows if r.get("item_key"))


def build_reading_queue(
    *,
    include_read: bool = False,
    limit: int = 30,
    refresh: bool = False,
    collection: str = "",
    tag: str = "",
    search: str = "",
) -> dict[str, Any]:
    """Ranked read-next queue. Read/handled status is applied live; scores come
    from the background cache. Scoring is NEVER auto-triggered on open — it runs
    only on an explicit ``refresh`` (the Rescore button), so opening is instant
    even right after a gate retrain (stale scores show with ``scores_stale``).

    ``collection``/``tag``/``search`` filter the displayed rows via the reader's
    own filtering; the score cache stays global (a Rescore scans the whole
    library), so filters only select which cached scores appear.

    Returns ``{status, items, total_unread, read_hidden, model_ready, error,
    computed_at, scores_stale}``."""
    rows = _reader().get_items(
        collection_key=collection or None,
        tag=tag or None,
        search=search or None,
        limit=_SCAN_LIMIT,
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
        }
        if handled:
            read.append(rec)
        else:
            unread.append(rec)

    if model_ready:
        unread.sort(
            key=lambda c: (c["relevance_score"] is not None, c["relevance_score"] or 0.0, c["date_added"]),
            reverse=True,
        )
    else:
        unread.sort(
            key=lambda c: (_PRIORITY_RANK.get(c["reading_priority"], 0), c["date_added"]),
            reverse=True,
        )

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
    }
