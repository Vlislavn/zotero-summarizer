"""Opt-in PDF digests cached in deep_reviews.json and surfaced by review_detail.

Missing PDFs and per-item failures are operational outcomes, not scientific
judgments. Explicit feed contexts disable browser acquisition.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from zotero_summarizer.services._common import (
    deep_review_fleet_concurrency,
    deep_review_sub_concurrency,
    now_iso_z,
)

from zotero_summarizer.models import PaperDigest
from zotero_summarizer.services.library import (
    _deep_review_errors,
    _deep_review_layers,
    _deep_review_progress,
    _map_reduce,
    _paper_body,
    _review_cache,
    _review_queue,
    quality_review,
    reading_queue,
    _review_attempt,
)
# Cache primitives are re-exported here as the public seam.
from zotero_summarizer.services.library._review_cache import (  # noqa: F401
    REVIEW_CONTRACT_VERSION,
    _cache_path,
    _read_all,
    _write_one,
    cached_review_keys,
    copy_review,
    current_review_keys,
    current_reviews,
    get_cached_review,
    get_current_review,
    get_usable_review,
    review_is_current,
    review_status,
    usable_reviews,
)
from zotero_summarizer.services._common import state as get_state
from zotero_summarizer.services.llm import factory

LOGGER = logging.getLogger(__name__)
_DEFAULT_TOP_K = 5

# Remote APIs still get a bounded default fan-out.
_MAX_CONCURRENT = 8
_MAX_FINISHED_JOBS = 12

_LOCK = threading.Lock()          # guards _JOBS
_POOL_LOCK = threading.Lock()     # guards (re)building the shared pool
_JOBS: dict[str, dict[str, Any]] = {}
_POOL: ThreadPoolExecutor | None = None
_POOL_SIZE = 0
_QUEUE = _review_queue.ReviewQueue()  # who takes the next free pool slot (owner requests first)


def _ensure_pool(provider: Any) -> ThreadPoolExecutor:
    """The shared review pool, sized provider-aware (local→1, remote→``max_sub_concurrency``
    else ``_MAX_CONCURRENT``); rebuilt when the size changes. The replaced pool is shut
    down without waiting: its in-flight reviews finish on its own threads."""
    global _POOL, _POOL_SIZE
    size = deep_review_fleet_concurrency(provider, _MAX_CONCURRENT)
    with _POOL_LOCK:
        if _POOL is None or size != _POOL_SIZE:
            if _POOL is not None:
                _POOL.shutdown(wait=False)
            _POOL = ThreadPoolExecutor(max_workers=size, thread_name_prefix="deep-review")
            _POOL_SIZE = size
        return _POOL


def _set_job(item_key: str, *, preserve_running: bool = False, **fields: Any) -> None:
    """Merge and trim under the registry lock; admission errors may preserve active work."""
    if fields.get('status') in {'ready', 'error'}:
        fields['attempt'] = _review_attempt.current_metadata()
    with _LOCK:
        job = _JOBS.get(item_key) or {}
        if preserve_running and job.get("status") == "running":
            return
        job.update(fields)
        _JOBS[item_key] = job
        _deep_review_progress.trim_finished(_JOBS, _MAX_FINISHED_JOBS)


def _set_job_progress(item_key: str, progress: dict[str, Any]) -> None:
    with _LOCK:
        job = _JOBS.get(item_key)
        if job is not None:
            job["progress"] = progress
            job["progress_at"] = time.perf_counter()


def status(item_key: str | None = None) -> dict[str, Any]:
    """Keyed attempt status; aggregate errors reflect only the latest finished job."""
    with _LOCK:
        if item_key is not None:
            job = _JOBS.get(item_key)
            if job is None:
                return {"status": "idle", "total": 0, "completed": 0, "error": None,
                        "started_at": None, "progress": {}, "diagnostic": None}
            return {
                "status": str(job.get("status") or "idle"),
                "total": 1,
                "completed": int(job.get("completed") or 0),
                "error": job.get("error"),
                "attempt": job.get("attempt"),
                "diagnostic": job.get("diagnostic") if job.get("status") == "error" else None,
                "started_at": job.get("started_at"),
                "progress": _deep_review_progress.live_progress(job),
                "queue_ahead": _QUEUE.ahead(item_key),  # queued jobs that start first; None once it has a slot
            }
        jobs = list(_JOBS.values())
    return _deep_review_progress.aggregate_status(jobs)


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


def _review_one(
    item: dict[str, Any],
    *,
    reader: Any,
    config: Any,
    extractor: Any,
    llm: Any,
    quality_enabled: bool,
    llm_digest: Any = None,
    llm_map: Any = None,
    response_format: dict[str, Any] | None = None,
    focus_prompt: str = "",
    prestige_scores: dict[str, Any] | None = None,
    prestige_floor_value: float | None = None,
    lean_tier: bool = False,
    sub_concurrency: int = 1,
    progress_sink: Any = None,
    provenance: dict[str, Any] | None = None,
    feed_context: Any = None, feed_out: dict | None = None,
) -> dict[str, Any] | None:
    """Review one item: explicit PDF override wins over its attachment.

    Missing items return None; missing PDFs report needs_pdf without acquisition.
    Accepted original-source evidence accompanies the policy-adjusted digest.
    Publication and generated-note staging belong to the worker, not computation.
    """
    from zotero_summarizer.services.library._review_identity import build_review_identity
    from zotero_summarizer.services.library._digest_audit import bind_cached_audit
    item_key = str(item["item_key"])
    detail = reader.get_item_detail(item_key)
    if detail is None:
        return None

    title = str(detail.get("title") or item.get("title") or "") or "Untitled"
    pdf_path = str(item.get("pdf_path") or detail.get("pdf_path") or "")
    source_kind = "override" if item.get("pdf_path") else ("library" if pdf_path else "none")
    review_identity = build_review_identity(
        config=config, pdf_path=pdf_path, source_kind=source_kind, focus_prompt=focus_prompt)
    verification_audit: dict[str, Any] = {}

    model_digest = digest_dump = quality_dump = goal_dump = paper_type_dump = section_overlay = code_link_dump = text_window = None
    reading_policy_flags: list[str] = []

    if pdf_path and quality_enabled:
        sink = progress_sink if progress_sink is not None else (lambda _p: None)
        reporter = _deep_review_progress.ReviewReporter(item_key, title, sink)
        reporter.phase("extract")
        text, text_window = _paper_body.read_body(extractor, pdf_path, config, lean_tier)  # ONE cleaned body for every stage
        if not text:
            raise ValueError("PDF extraction returned no text")
        def compute_digest() -> Any:
            reporter.phase("digest", is_call=True)
            digest = _map_reduce.digest_for_strategy(
                title, text, config, map_llm=llm_map or llm, reduce_llm=llm_digest or llm,
                budget=_map_reduce.ChunkBudget(
                    max_chars=text_window["cap"], chunk_chars=config.quality_review.map_chunk_chars, sub_concurrency=sub_concurrency, whole_body=text_window["whole_body"],
                ),
                focus_prompt=focus_prompt, response_format=response_format, verification_audit=verification_audit,
                **({"feed_context": feed_context, "feed_out": feed_out} if feed_context is not None else {}))
            reporter.finish("digest")
            return digest

        digest, (quality_dump, goal_dump, paper_type_dump, section_overlay, code_link_dump) = _deep_review_layers.digest_with_layers(
            compute_digest, _deep_review_layers.ExtraLayersCtx(
                item_key=item_key, title=title, pdf_path=pdf_path, text=text, max_chars=text_window["cap"],
                llm=llm, llm_light=llm_map, config=config,
                prestige=(prestige_scores or {}).get(item_key),
                prestige_floor_value=prestige_floor_value, reporter=reporter, lean_tier=lean_tier,
                sub_concurrency=sub_concurrency, item_type=detail.get("item_type"),
                web_article=bool(item.get("web_article")),
                abstract=str(detail.get("abstract") or item.get("abstract") or ""),
            ))
        model_digest, digest_dump = digest.model_dump(), digest.model_dump()  # distinct: projection must not alias the retained raw digest
        from zotero_summarizer.services.library.review_fleet.propose import apply_reading_policy
        digest_dump, _, reading_policy_flags = apply_reading_policy(
            digest_dump, quality_dump,
        )
        PaperDigest.model_validate(digest_dump)
        reporter.summary()

    final_identity = build_review_identity(config=config, pdf_path=pdf_path, source_kind=source_kind, focus_prompt=focus_prompt)
    if review_identity != final_identity:
        raise ValueError("Review source changed during generation")
    return bind_cached_audit({
        "review_contract_version": REVIEW_CONTRACT_VERSION,
        "review_identity": final_identity,
        "verification_audit": verification_audit or None,
        "not_established_fields": ((verification_audit.get("lineage") or [{}])[-1]).get("not_established_fields", []),
        "provenance": provenance or {},
        "text_window": text_window,
        "digest": digest_dump,
        "quality": quality_dump,
        "goal_summaries": goal_dump,
        "paper_type": paper_type_dump,
        "section_overlay": section_overlay,
        "code_link": code_link_dump,
        "model_digest": model_digest,
        "model_read_decision": str((model_digest or {}).get("read_decision") or ""),
        "reading_policy_flags": reading_policy_flags,
        "needs_pdf": not bool(pdf_path),
        "needs_login": False,
        "login_url": "",
        "gate_relevance": item.get("gate_relevance"),
        "reviewed_at": now_iso_z(),
        "zotero_note_written": False,
        "zotero_note_error": None,
        "timings": _review_attempt.current_timings(),
    })


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


def _build_ctx(reader: Any = None) -> dict[str, Any]:
    """Resolve the shared per-run review context (LLM clients, config, reader, the
    library prestige floor, provider-derived tiers) ONCE per ``start`` call. The
    ``_provider`` key (private) is used for pool sizing + error hinting and is filtered
    out before the kwargs reach ``_review_one``. ``reader`` overrides the resolved
    library reader — the in-place Today review passes an ``AppLibraryReader`` so feed
    candidates resolve by ``stable_feed_key`` even when a live Zotero reader is present."""
    app = get_state()
    config = app.app_state.config
    cfg = config.quality_review
    extractor = getattr(app, "pdf_extractor", None)
    # Zotero-optional: live Zotero reader when present, else the app library over
    # kept feed papers (PDF acquired into the local cache by _pdf_acquire).
    from zotero_summarizer.services.zotero.zotero import get_library_reader

    reader = reader or get_library_reader(app)
    from zotero_summarizer.models.providers import resolve_stage

    resolved = resolve_stage(config.llm_routing, "deep_review")
    provider = app.resolve_stage_provider("deep_review")
    prestige_scores, prestige_floor_value = _load_prestige_context()
    return {
        "reader": reader,
        "config": config,
        "extractor": extractor,
        "quality_enabled": bool(cfg.enabled and extractor is not None),
        "llm": factory.resolve_stage_client(app, "deep_review", enable_thinking=False),
        "llm_digest": factory.resolve_stage_client(app, "deep_review", enable_thinking=provider.thinking_on and config.review_depth != "fast"),  # DIGEST reasons unless provider thinking_effort=off or review_depth=fast
        "llm_map": factory.resolve_stage_client(app, "feed", enable_thinking=False) if cfg.chunk_strategy == "map_reduce" else None,
        "prestige_scores": prestige_scores,
        "prestige_floor_value": prestige_floor_value,
        "lean_tier": bool(getattr(provider, "lean_deep_review", False)),  # ollama=prefill-bound→lean; keyed on flag, not is_local
        # Sub-call concurrency within one paper (local→serial, remote→capped); shared with verify-deep-review.
        "sub_concurrency": deep_review_sub_concurrency(provider),
        # Decoder-level structured output: JSON Schema for PaperDigest when the provider supports it
        # (vLLM-style: logit constraint + reasoning survives, measured 2026-06-27); None → prompt-level fallback.
        "response_format": quality_review.build_response_format(PaperDigest) if getattr(provider, "structured_output", False) else None,
        "provenance": {
            "provider": getattr(provider, "name", resolved.provider.name),
            "model": resolved.model,
            "prompt_schema_version": REVIEW_CONTRACT_VERSION,
            "review_depth": str(config.review_depth),
        },
        "_provider": provider,
    }


def _resolve_items(top_k: int, item_keys: list[str] | None, overrides: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the per-item dicts: the explicit ``item_keys`` (per-paper button / fleet,
    honoring ``pdf_overrides``) or the top-``top_k`` unread reading-queue picks."""
    if item_keys is not None:
        # Per-paper: title is re-read inside _review_one; gate_relevance is display-only.
        items = []
        for key in dict.fromkeys(item_keys):
            override = overrides.get(key, "")
            acquired = override if isinstance(override, dict) else None
            item = {
                "item_key": key, "title": "",
                "gate_relevance": (reading_queue.get_cached_scoring(key) or {}).get("composite_score"),
                "pdf_path": acquired.get("path", "") if acquired else override,
            }
            if acquired:
                item["acquired_pdf"] = acquired
            items.append(item)
        return items
    queue = reading_queue.build_reading_queue(limit=max(1, top_k))
    return [
        {"item_key": row["item_key"], "title": row.get("title") or "", "gate_relevance": row.get("relevance_score")}
        for row in (queue.get("items") or [])[:top_k]
    ]


@_review_attempt.tracked_worker
def _review_worker(item: dict[str, Any], ctx: dict[str, Any], focus_prompt: str) -> None:
    """Review and settle one item without affecting another item's cache or job."""
    item_key = str(item["item_key"])
    kwargs = {k: v for k, v in ctx.items() if not k.startswith("_")}
    context = (ctx.get("_feed_contexts") or {}).get(item_key)
    acquired = None
    try:
        if ctx.get("_acquire_missing") and not item.get("pdf_path"):
            from zotero_summarizer.services.library import _pdf_acquire
            _set_job_progress(item_key, {"phase": "acquire", "phase_label": "Fetching full text…"})
            if context is not None:
                detail = ctx["reader"].get_item_detail(item_key) or {}
                acquired = _pdf_acquire.acquire_pdf_for(item_key, detail, allow_browser=False)
            else:
                acquired = _pdf_acquire.acquire_for_item(item_key, reader=ctx.get("reader"))
            if acquired.path is not None:
                item.update({
                    "pdf_path": str(acquired.path), "web_article": acquired.web_article,
                    "acquired_pdf": acquired.as_override(),
                })
        feed_out = {}
        if context is not None:
            from zotero_summarizer.services.library import _feed_assessment
            kwargs.update(feed_context=context, feed_out=feed_out)
        entry = _review_one(
            item, focus_prompt=focus_prompt,
            progress_sink=lambda prog: _set_job_progress(item_key, prog), **kwargs,
        )
        if entry is not None:
            if context is not None:
                _feed_assessment.bind(entry, feed_out)
            if item.get("acquired_pdf"):
                entry["acquired_pdf"] = item["acquired_pdf"]
            if acquired is not None and not item.get("pdf_path"):
                entry["acquire_outcome"] = acquired.outcome
                if acquired.needs_login:
                    entry["needs_login"] = True
                    entry["login_url"] = acquired.login_url
            if _review_attempt.publishable_entry(entry, get_current_review(item_key), acquired):
                with _review_cache.review_publication_lock():
                    _review_cache.keep_previous_if_downgraded(entry, _read_all().get(item_key))
                    _write_one(item_key, entry)
                    if entry.get("digest"):
                        from zotero_summarizer.services.zotero.generated_notes import stage_current_digest
                        _set_job_progress(item_key, {"phase": "note", "phase_label": "Preparing note proposal…"})
                        stage_current_digest(item_key)
                _try_rebuild_render(item_key)
        _set_job(item_key, status="ready", completed=1, progress={}, error=None, diagnostic=None)
    except Exception as exc:  # noqa: BLE001 — per-item background boundary
        LOGGER.warning("deep_review failed item=%s: %s", item_key, exc)
        _set_job(item_key, status="error", completed=1, progress={},
                 **_deep_review_errors.failure_fields(
                     exc, ctx.get("_provider"), phase=status(item_key)['progress'].get('phase')))


def _drain(provider: Any) -> None:
    """Hand queued reviews to the pool while a slot is free (owner requests first)."""
    capacity = deep_review_fleet_concurrency(provider, _MAX_CONCURRENT)
    while (claimed := _QUEUE.claim(capacity)) is not None:
        lane, (item, ctx, focus_prompt) = claimed
        try:
            _ensure_pool(provider).submit(_run_slot, item, {**ctx, "_lane": lane}, focus_prompt)
        except Exception as exc:  # noqa: BLE001 — never leave the job 'running' without a worker
            _QUEUE.release(lane)
            LOGGER.warning("deep_review could not start item=%s: %s", item["item_key"], exc)
            _set_job(str(item["item_key"]), status="error", completed=1, progress={},
                     error=f"{type(exc).__name__}: {exc}")


def _run_slot(item: dict[str, Any], ctx: dict[str, Any], focus_prompt: str) -> None:
    """One pool slot: review the item, then free the slot for the next queued one."""
    try:
        _review_worker(item, ctx, focus_prompt)
    finally:
        _QUEUE.release(ctx["_lane"])
        _drain(ctx.get("_provider"))


def _submit(item: dict[str, Any], ctx: dict[str, Any], focus_prompt: str) -> None:
    """Per-item single-flight: queue a review for ``item`` unless one is already running
    for that key (an owner request promotes a queued background one), then fill free pool
    slots (local→queue, remote→concurrent). ``ctx['_background']`` marks non-owner work."""
    item_key = str(item["item_key"])
    background = bool(ctx.get("_background"))
    with _LOCK:
        existing = _JOBS.get(item_key)
        if existing is not None and existing.get("status") == "running":
            if not background:
                _QUEUE.promote(item_key)
            return  # already in flight for this paper — don't double-review
        _JOBS[item_key] = {"status": "running", "started_at": now_iso_z(), "completed": 0, "progress": {}, "error": None}
    _QUEUE.add(item_key, (item, ctx, focus_prompt), _review_queue.BACKGROUND if background else _review_queue.INTERACTIVE)
    _drain(ctx.get("_provider"))


def start(
    top_k: int = _DEFAULT_TOP_K,
    *,
    item_keys: list[str] | None = None,
    focus_prompt: str = "",
    pdf_overrides: dict[str, Any] | None = None,
    acquire_missing: bool = False,
    reader: Any = None,
    feed_contexts: dict[str, Any] | None = None,
    background: bool = False,
) -> dict[str, Any]:
    """Start explicit papers or top unread picks on the provider-aware per-item pool.

    Reader/PDF overrides preserve caller-selected input; optional acquisition stays
    worker-bound. Feed contexts must match the complete explicit key set.
    Duplicate keys retain first-seen order; an empty list changes no jobs.
    Running papers are not resubmitted or settled by another request's setup error;
    genuinely inactive targets still receive that failure. Local work queues while
    remote work may run concurrently. ``background=True`` (Today preparation, prewarm,
    fleet) queues behind owner-requested reviews and never takes the last pool slot.
    Return aggregate status plus ``accepted`` (False for an explicit empty list).
    """
    from zotero_summarizer.models.research_feed import FeedReviewContext
    contexts = {key: FeedReviewContext.model_validate(value).model_copy(deep=True)
                for key, value in (feed_contexts or {}).items()}
    if contexts and (set(contexts) != set(item_keys or []) or any(c.source_id != key for key, c in contexts.items())):
        raise ValueError("feed contexts require exact explicit item keys")
    if item_keys == []:
        return {**status(), "accepted": False}
    overrides = pdf_overrides or {}
    try:
        ctx = _build_ctx(reader=reader)
        ctx["_feed_contexts"] = contexts
        ctx["_acquire_missing"] = bool(acquire_missing)
        ctx["_background"] = background
        items = _resolve_items(top_k, item_keys, overrides)
    except Exception as exc:  # noqa: BLE001 — surface setup failure on the targeted jobs
        msg = f"{type(exc).__name__}: {exc}"
        LOGGER.warning("deep_review start failed: %s", msg)
        for key in item_keys or []:
            _set_job(str(key), preserve_running=True, status="error", completed=1, progress={}, error=msg,
                     started_at=now_iso_z())
        return {**status(), "accepted": True, "error": msg}
    for item in items:
        _submit(item, ctx, focus_prompt)
    return {**status(), "accepted": True}


__all__ = [
    "start", "status", "get_cached_review", "get_current_review",
    "review_is_current", "review_status", "get_usable_review", "usable_reviews",
    "cached_review_keys", "current_review_keys", "current_reviews", "copy_review",
]
