"""On-demand DEEP review of the top unread library picks (Stage-2 "Read next").

A heavier, opt-in companion to ``reading_queue`` (which only scores abstracts
with the gate). For each requested item this produces a single **condensed paper
digest** from the LOCAL PDF via ``services.quality_review.assess_digest`` — what
the paper is about + how to use it (read/skip + why, parts to read, relevance to
the user's goals, controversies, impact, unknown-unknowns, implementation) plus
a quality grade + 5 dimensions. No separate relevance re-score (it contradicted
the gate and confused users); the digest is the whole output.

Results land in ``deep_reviews.json`` keyed by ``item_key`` and are surfaced by
``review_detail.build_library_detail``. The digest is also upserted to the Zotero
item as one short note (best-effort).

**Concurrency: per-item jobs over one provider-aware pool** (mirrors
``paper_render``). Each requested paper becomes its own ``_JOBS[item_key]`` entry
with its OWN live progress, run on a shared ``ThreadPoolExecutor`` sized by
``deep_review_fleet_concurrency``: a **local** model runs 1 at a time (the 2nd
paper QUEUES — one on-device model can't serve two reviews without thrashing host
RAM), a **remote** model runs up to its ``max_sub_concurrency`` concurrently. So
you can deep-review a second paper while the first is still running, and each
paper's panel polls ``status(item_key)`` for ITS own progress (not a global one).
Re-submitting a paper that's already running is a no-op (per-item single-flight).

Empty/partial results are valid: a paper with no local PDF is marked
``needs_pdf`` and nothing else runs — an honest "no full text", not a masked
error. A per-item failure is recorded on that item's job (with the connectivity
hint) and never affects another paper's review.
"""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from zotero_summarizer.services._common import (
    deep_review_fleet_concurrency,
    deep_review_sub_concurrency,
    now_iso_z,
    settings,
    write_json_atomic,
)

from zotero_summarizer.services.library import (
    _deep_review_errors,
    _deep_review_layers,
    _deep_review_progress,
    quality_review,
    reading_queue,
)
from zotero_summarizer.services._common import state as get_state

LOGGER = logging.getLogger(__name__)

_CACHE_FILENAME = "deep_reviews.json"
_DEFAULT_TOP_K = 5

# ---------------------------------------------------------------------------
# Per-item job registry + one provider-aware review pool (mirrors paper_render).
# ---------------------------------------------------------------------------
# Upper bound on concurrent paper reviews when a remote provider sets no
# max_sub_concurrency (an API has no host-RAM limit, but unbounded fan-out is
# still impolite). Named constant, not a magic literal.
_MAX_CONCURRENT = 8
# Keep all running jobs + the most-recent finished ones, so the registry (and the
# aggregate status's counts) stay bounded across a long session.
_MAX_FINISHED_JOBS = 12

_LOCK = threading.Lock()          # guards _JOBS
_CACHE_LOCK = threading.Lock()    # guards the read-merge-write of deep_reviews.json
_POOL_LOCK = threading.Lock()     # guards (re)building the shared pool
_JOBS: dict[str, dict[str, Any]] = {}
_POOL: ThreadPoolExecutor | None = None
_POOL_SIZE = 0


def _ensure_pool(provider: Any) -> ThreadPoolExecutor:
    """The shared review pool, sized provider-aware: local→1 (a 2nd paper QUEUES
    behind the 1st), remote→``max_sub_concurrency`` else ``_MAX_CONCURRENT``. Rebuilt
    when the size changes (a provider / LLM-routing edit).

    # ponytail: a rebuild just swaps the pool ref — in-flight reviews finish on the
    # old pool's threads; a size change is a rare config edit, so this is cheap.
    """
    global _POOL, _POOL_SIZE
    size = deep_review_fleet_concurrency(provider, _MAX_CONCURRENT)
    with _POOL_LOCK:
        if _POOL is None or size != _POOL_SIZE:
            _POOL = ThreadPoolExecutor(max_workers=size, thread_name_prefix="deep-review")
            _POOL_SIZE = size
        return _POOL


def _trim_jobs_locked() -> None:
    """Drop the oldest FINISHED jobs beyond ``_MAX_FINISHED_JOBS`` (running jobs are
    always kept). Caller holds ``_LOCK``."""
    finished = [(k, j) for k, j in _JOBS.items() if j.get("status") != "running"]
    if len(finished) <= _MAX_FINISHED_JOBS:
        return
    finished.sort(key=lambda kv: kv[1].get("started_at") or "")
    for key, _ in finished[: len(finished) - _MAX_FINISHED_JOBS]:
        _JOBS.pop(key, None)


def _set_job(item_key: str, **fields: Any) -> None:
    """Merge ``fields`` into ``_JOBS[item_key]`` and trim finished jobs (lock-guarded)."""
    with _LOCK:
        job = _JOBS.get(item_key) or {}
        job.update(fields)
        _JOBS[item_key] = job
        _trim_jobs_locked()


def _set_job_progress(item_key: str, progress: dict[str, Any]) -> None:
    """ReviewReporter sink: write the live within-item progress onto THIS item's job."""
    with _LOCK:
        job = _JOBS.get(item_key)
        if job is not None:
            job["progress"] = progress


def status(item_key: str | None = None) -> dict[str, Any]:
    """Poll payload ``{status, total, completed, error, started_at, progress}``.

    With ``item_key`` set, reports THAT paper's job (``running``/``ready``/``error``,
    or ``idle`` when no job is tracked) — the per-paper panel polls this so it shows
    its OWN progress. Without ``item_key``, an AGGREGATE: ``running`` if ANY review is
    in flight (the ``university_access`` "is a review running?" gate + the review-fleet
    poll rely on this), else ``error``/``ready``/``idle`` over the tracked jobs."""
    with _LOCK:
        if item_key is not None:
            job = _JOBS.get(item_key)
            if job is None:
                return {"status": "idle", "total": 0, "completed": 0, "error": None,
                        "started_at": None, "progress": {}}
            return {
                "status": str(job.get("status") or "idle"),
                "total": 1,
                "completed": int(job.get("completed") or 0),
                "error": job.get("error"),
                "started_at": job.get("started_at"),
                "progress": dict(job.get("progress") or {}),
            }
        jobs = list(_JOBS.values())
    running = [j for j in jobs if j.get("status") == "running"]
    error = next((j.get("error") for j in jobs if j.get("status") == "error" and j.get("error")), None)
    completed = sum(1 for j in jobs if j.get("status") in ("ready", "error"))
    if running:
        state = "running"
    elif error:
        state = "error"
    elif completed > 0:
        state = "ready"
    else:
        state = "idle"
    return {
        "status": state,
        "total": len(jobs),
        "completed": completed,
        "error": error,
        "started_at": min((j.get("started_at") for j in jobs if j.get("started_at")), default=None),
        "progress": dict((running[0].get("progress") if running else {}) or {}),
    }


def _try_rebuild_render(item_key: str) -> None:
    """Rebuild the HTML artifact with the new digest baked in.

    User-authorized background side-effect — user selected 'Auto-rebuild after review'.
    Never raises: if the rebuild fails (e.g. PDF moved), the digest is already saved.
    """
    try:
        from zotero_summarizer.services.library import paper_render  # lazy: avoids circular at load time
        state = paper_render._read_state(item_key)
        if state is None or state.get("status") != "completed":
            return
        paper_render.build_paper_read(item_key, force=True)
    except Exception as exc:  # noqa: BLE001 — auto-rebuild is user-authorized best-effort
        LOGGER.warning("auto-rebuild after deep review failed for %s: %s", item_key, exc)


# ---------------------------------------------------------------------------
# JSON cache (item_key -> review entry). Quality is gate-independent, so unlike
# reading_queue this is NOT keyed by the gate sha; re-run to refresh.
# ---------------------------------------------------------------------------


def _cache_path():
    from zotero_summarizer.services.model.classifier_persistence import DEFAULT_MODEL_DIR
    return DEFAULT_MODEL_DIR / _CACHE_FILENAME


def _read_all() -> dict[str, Any]:
    path = _cache_path()
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("reviews") or {}


def _write_all(reviews: dict[str, Any]) -> None:
    write_json_atomic(_cache_path(), {"updated_at": now_iso_z(), "reviews": reviews})


def _write_one(item_key: str, entry: dict[str, Any]) -> None:
    """Persist ONE review entry via a locked read-merge-write, so concurrent per-item
    workers never clobber each other's keys in the shared ``deep_reviews.json``."""
    with _CACHE_LOCK:
        reviews = _read_all()
        reviews[item_key] = entry
        _write_all(reviews)


def get_cached_review(item_key: str) -> dict[str, Any] | None:
    """The stored deep-review entry for an item (for ``review_detail``), or None."""
    if not item_key:
        return None
    return _read_all().get(item_key)


def cached_review_keys() -> set[str]:
    """All item_keys with a stored deep review — one cache read (prewarm reuses this
    instead of calling ``get_cached_review`` per row, which re-reads the whole file)."""
    return set(_read_all())


# ---------------------------------------------------------------------------
# Per-item work + the background job
# ---------------------------------------------------------------------------


def _review_one(
    item: dict[str, Any],
    *,
    reader: Any,
    config: Any,
    extractor: Any,
    llm: Any,
    quality_enabled: bool,
    llm_digest: Any = None,
    focus_prompt: str = "",
    prestige_scores: dict[str, Any] | None = None,
    prestige_floor_value: float | None = None,
    lean_tier: bool = False,
    sub_concurrency: int = 1,
    progress_sink: Any = None,
) -> dict[str, Any] | None:
    """Condensed paper DIGEST (what it's about + how to use it + quality) for one
    library item. ``None`` when the item vanished from Zotero (caller skips it).

    The PDF normally comes from Zotero's local attachment (``detail['pdf_path']``);
    Zotero's "Find Available PDF" handles Cloudflare/SSO sources a headless client
    can't. The review-fleet may instead inject an already-acquired PDF path on the
    ``item`` dict (``item['pdf_path']`` — a local cache download via the university
    browser/OA chain), which takes precedence so a verdict works without a Zotero
    attachment. With neither path we mark ``needs_pdf`` and do nothing else —
    honest, and cheap (no doomed fetch). The digest is also written to Zotero as one
    short note (best-effort; the in-app digest is unaffected by a note-write failure).
    """
    item_key = str(item["item_key"])
    detail = reader.get_item_detail(item_key)
    if detail is None:
        return None

    title = str(detail.get("title") or item.get("title") or "") or "Untitled"
    # An injected cache path (fleet's university/OA acquisition) wins over the
    # Zotero attachment — the decouple that lets a verdict happen while Zotero is open.
    pdf_path = str(item.get("pdf_path") or detail.get("pdf_path") or "")

    digest_dump: dict[str, Any] | None = None
    quality_dump: dict[str, Any] | None = None
    goal_dump: list[dict[str, Any]] | None = None
    paper_type_dump: dict[str, Any] | None = None
    note_written = False
    note_error: str | None = None

    if pdf_path and quality_enabled:
        sink = progress_sink if progress_sink is not None else (lambda _p: None)
        reporter = _deep_review_progress.ReviewReporter(item_key, title, sink)
        reporter.phase("extract")
        # A corrupt PDF raises out of extract_text and is handled by the per-item
        # boundary in _review_worker (recorded on this item's job).
        text = extractor.extract_text(pdf_path).strip()
        if text:
            qr = config.quality_review
            max_chars = int(qr.lean_max_text_chars if lean_tier else qr.max_text_chars)
            reporter.phase("digest", is_call=True)
            digest = quality_review.assess_digest(
                title=title, full_text=text, config=config, llm=llm_digest or llm,
                focus_prompt=focus_prompt, max_chars=max_chars,
            )
            digest_dump = digest.model_dump()
            quality_dump, goal_dump, paper_type_dump = _deep_review_layers.extra_layers(_deep_review_layers.ExtraLayersCtx(
                item_key=item_key, title=title, pdf_path=pdf_path, text=text,
                digest_dump=digest_dump, llm=llm, config=config,
                prestige=(prestige_scores or {}).get(item_key),
                prestige_floor_value=prestige_floor_value, reporter=reporter,
                lean_tier=lean_tier, sub_concurrency=sub_concurrency, item_type=detail.get("item_type"),
            ))
            reporter.phase("note")
            try:
                from zotero_summarizer.services.zotero.zotero import zotero_upsert_digest_note
                zotero_upsert_digest_note(item_key, digest)
                note_written = True
            except Exception as exc:  # noqa: BLE001 — note write must not fail the digest
                note_error = f"{type(exc).__name__}: {exc}"
                LOGGER.warning("digest note write for %s failed: %s", item_key, exc)
            reporter.summary()

    return {
        "digest": digest_dump,
        "quality": quality_dump,
        "goal_summaries": goal_dump,
        "paper_type": paper_type_dump,
        "needs_pdf": not bool(pdf_path),
        # Set by _review_worker from the acquire result when a fetch was DECLARED-but-
        # gated (paywall/no session) — the per-paper pane shows login_url as a
        # click-to-open sign-in link. Default off (most reviews have a PDF).
        "needs_login": False,
        "login_url": "",
        "gate_relevance": item.get("gate_relevance"),
        "reviewed_at": now_iso_z(),
        "zotero_note_written": note_written,
        "zotero_note_error": note_error,
    }


def _load_prestige_context() -> tuple[dict[str, Any], float | None]:
    """``(scores_by_item, library_prestige_floor)`` for the quality HIGHLIGHT bar.

    Reads the existing global score cache (one read per job). Boundary: a missing/
    unreadable cache yields ``({}, None)`` so the floor is simply inert — never a
    crash, and never demotes papers (asymmetric)."""
    try:
        scores, _stale = reading_queue.read_score_cache_with_staleness()
    except Exception as exc:  # noqa: BLE001 — prestige is optional enrichment
        LOGGER.warning("prestige context unavailable: %s", exc)
        return {}, None
    floor = reading_queue.prestige_floor(
        [(s.get("prestige"), bool(s.get("prestige_known"))) for s in scores.values()]
    )
    return scores, floor


def _build_ctx() -> dict[str, Any]:
    """Resolve the shared per-run review context (LLM clients, config, reader, the
    library prestige floor, provider-derived tiers) ONCE per ``start`` call. The
    ``_provider`` key (private) is used for pool sizing + error hinting and is filtered
    out before the kwargs reach ``_review_one``. Raises if the Zotero reader is absent."""
    app = get_state()
    config = app.app_state.config
    cfg = config.quality_review
    extractor = getattr(app, "pdf_extractor", None)
    reader = getattr(app, "zotero_reader", None)
    if reader is None:
        raise RuntimeError("Zotero reader unavailable; cannot deep-review")
    provider = app.resolve_stage_provider("deep_review")
    prestige_scores, prestige_floor_value = _load_prestige_context()
    return {
        "reader": reader,
        "config": config,
        "extractor": extractor,
        "quality_enabled": bool(cfg.enabled and extractor is not None),
        "llm": app.resolve_stage_client("deep_review"),
        # DIGEST reasons (quality); trivial calls keep the fast thinking-off default
        # (a thinking-off digest goes empty/hallucinates — see README). No-op sans flag.
        "llm_digest": app.resolve_stage_client("deep_review", enable_thinking=True),
        "prestige_scores": prestige_scores,
        "prestige_floor_value": prestige_floor_value,
        # `lean_deep_review` providers (ollama, prefill-bound) use the cheaper tier;
        # keyed on the flag, NOT is_local (MLX is loopback but fast).
        "lean_tier": bool(getattr(provider, "lean_deep_review", False)),
        # Sub-call concurrency WITHIN one paper (local→serial, remote→capped); shared
        # with verify-deep-review so the CLI receipt matches production.
        "sub_concurrency": deep_review_sub_concurrency(provider),
        "_provider": provider,
    }


def _resolve_items(top_k: int, item_keys: list[str] | None, overrides: dict[str, str]) -> list[dict[str, Any]]:
    """Build the per-item dicts: the explicit ``item_keys`` (per-paper button / fleet,
    honoring ``pdf_overrides``) or the top-``top_k`` unread reading-queue picks."""
    if item_keys:
        # Per-paper: title is re-read inside _review_one; gate_relevance is display-only.
        return [
            {
                "item_key": key,
                "title": "",
                "gate_relevance": (reading_queue.get_cached_scoring(key) or {}).get("composite_score"),
                "pdf_path": overrides.get(key, ""),
            }
            for key in item_keys
        ]
    queue = reading_queue.build_reading_queue(limit=max(1, top_k))
    return [
        {"item_key": row["item_key"], "title": row.get("title") or "", "gate_relevance": row.get("relevance_score")}
        for row in (queue.get("items") or [])[:top_k]
    ]


def _review_worker(item: dict[str, Any], ctx: dict[str, Any], focus_prompt: str) -> None:
    """Pool task: review ONE paper, persist it, and settle THIS item's job. A failure
    is recorded on the item's job (with the connectivity hint) and never touches another
    paper's review (per-item boundary)."""
    item_key = str(item["item_key"])
    kwargs = {k: v for k, v in ctx.items() if not k.startswith("_")}
    acquired = None
    try:
        # Per-paper button: fetch a PDF first (arXiv/OA/PMC → browser session) for a pick
        # with no Zotero attachment, then review FROM it. A failure here propagates to the
        # per-item boundary below (recorded as the item's error), never silently swallowed.
        if ctx.get("_acquire_missing") and not item.get("pdf_path"):
            from zotero_summarizer.services.library import _pdf_acquire
            _set_job_progress(item_key, {"phase": "acquire", "phase_label": "Fetching full text…"})
            acquired = _pdf_acquire.acquire_for_item(item_key)
            if acquired.path is not None:
                item["pdf_path"] = str(acquired.path)
        entry = _review_one(
            item, focus_prompt=focus_prompt,
            progress_sink=lambda prog: _set_job_progress(item_key, prog), **kwargs,
        )
        if entry is not None:
            # The fetch was DECLARED-but-gated (paywall/no session): carry the actionable
            # needs_login + landing URL so the pane shows a click-to-open sign-in link
            # (not the misleading generic "no full text"). Only when no PDF landed.
            if acquired is not None and acquired.needs_login and not item.get("pdf_path"):
                entry["needs_login"] = True
                entry["login_url"] = acquired.login_url
            _write_one(item_key, entry)
            _try_rebuild_render(item_key)
        _set_job(item_key, status="ready", completed=1, progress={}, error=None)
    except Exception as exc:  # noqa: BLE001 — per-item background boundary
        LOGGER.warning("deep_review failed item=%s: %s", item_key, exc)
        hint = _deep_review_errors.summarize_errors([f"{type(exc).__name__}: {exc}"], ctx.get("_provider"))
        _set_job(item_key, status="error", completed=1, progress={}, error=hint)


def _submit(item: dict[str, Any], ctx: dict[str, Any], focus_prompt: str) -> None:
    """Per-item single-flight: start a review for ``item`` unless one is already running
    for that key, on the shared provider-aware pool (local→queue, remote→concurrent)."""
    item_key = str(item["item_key"])
    with _LOCK:
        existing = _JOBS.get(item_key)
        if existing is not None and existing.get("status") == "running":
            return  # already in flight for this paper — don't double-review
        _JOBS[item_key] = {"status": "running", "started_at": now_iso_z(), "completed": 0, "progress": {}, "error": None}
    _ensure_pool(ctx.get("_provider")).submit(_review_worker, item, ctx, focus_prompt)


def start(
    top_k: int = _DEFAULT_TOP_K,
    *,
    item_keys: list[str] | None = None,
    focus_prompt: str = "",
    pdf_overrides: dict[str, str] | None = None,
    acquire_missing: bool = False,
) -> dict[str, Any]:
    """Kick off deep review(s). With ``item_keys`` set, reviews exactly those papers
    (the per-paper button / the review-fleet); otherwise the top-``top_k`` unread picks.
    ``focus_prompt`` shapes the LLM's emphasis. ``pdf_overrides`` (``item_key -> local
    PDF path``) lets the fleet review from an acquired cache file instead of a missing
    Zotero attachment (honored only for the ``item_keys`` branch).

    ``acquire_missing`` (the per-paper button): for a pick with no local Zotero PDF,
    fetch one first via ``_pdf_acquire`` (arXiv/OA/PMC headless → the browser using your
    session) and review FROM THAT — so a paper without a Zotero attachment is fetched,
    not just flagged ``needs_pdf``. The fleet leaves this off (it pre-acquires + passes
    ``pdf_overrides``). Keep it to small ``item_keys`` runs — acquisition is a single
    stateful browser session, serialized by a module lock, so concurrent acquires queue.

    Each paper runs as its own job on the shared provider-aware pool — concurrent for a
    remote provider, queued for a local one. Already-running papers are not re-submitted.
    Returns the AGGREGATE ``status()`` + ``accepted: True`` (there's no single-flight to
    reject; the field is kept for the review-fleet's poll contract). On a setup failure
    (no Zotero reader / queue build) the targeted papers are marked errored so their
    panels surface the cause."""
    overrides = pdf_overrides or {}
    try:
        ctx = _build_ctx()
        ctx["_acquire_missing"] = bool(acquire_missing)
        items = _resolve_items(top_k, item_keys, overrides)
    except Exception as exc:  # noqa: BLE001 — surface setup failure on the targeted jobs
        msg = f"{type(exc).__name__}: {exc}"
        LOGGER.warning("deep_review start failed: %s", msg)
        for key in item_keys or []:
            _set_job(str(key), status="error", completed=1, progress={}, error=msg,
                     started_at=now_iso_z())
        return {**status(), "accepted": True, "error": msg}
    for item in items:
        _submit(item, ctx, focus_prompt)
    return {**status(), "accepted": True}


__all__ = ["start", "status", "get_cached_review"]
