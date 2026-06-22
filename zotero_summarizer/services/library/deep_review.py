"""On-demand DEEP review of the top unread library picks (Stage-2 "Read next").

A heavier, opt-in companion to ``reading_queue`` (which only scores abstracts
with the gate). For each requested item this produces a single **condensed paper
digest** from the LOCAL PDF via ``services.quality_review.assess_digest`` — what
the paper is about + how to use it (read/skip + why, parts to read, relevance to
the user's goals, controversies, impact, unknown-unknowns, implementation) plus
a quality grade + 5 dimensions. No separate relevance re-score (it contradicted
the gate and confused users); the digest is the whole output.

Mirrors ``reading_queue``'s established single-flight + JSON-cache pattern:
results land in ``deep_reviews.json`` keyed by ``item_key`` and are surfaced by
``review_detail.build_library_detail``. The digest is also upserted to the Zotero
item as one short note (best-effort). The N papers fan out provider-aware
(``deep_review_fleet_concurrency``): serial for a local model, capped by the remote
provider's ``max_sub_concurrency`` (else all N) for a remote one.

Empty/partial results are valid: a paper with no local PDF is marked
``needs_pdf`` and nothing else runs — an honest "no full text", not a masked
error. Per-item failures are logged and skipped (background worker boundary);
only a job-level failure (reader/LLM unavailable) sets the status error.
"""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
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
# Single-flight background-job state (separate from reading_queue's).
# ---------------------------------------------------------------------------
_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "running": False,
    "total": 0,
    "completed": 0,
    "error": None,
    "started_at": None,
    # Live within-item progress (phase + sub-step) for the polled status, written
    # by ReviewReporter via _set_progress; {} between items / when idle.
    "progress": {},
}


def try_start() -> bool:
    """Claim the single-flight slot. ``False`` when a run is already in flight."""
    with _LOCK:
        if _STATE["running"]:
            return False
        _STATE["running"] = True
        _STATE["error"] = None
        _STATE["total"] = 0
        _STATE["completed"] = 0
        _STATE["started_at"] = now_iso_z()
        _STATE["progress"] = {}
        return True


def finish(error: str | None = None) -> None:
    with _LOCK:
        _STATE["running"] = False
        _STATE["error"] = error
        _STATE["progress"] = {}


def _set_progress(progress: dict[str, Any]) -> None:
    """Lock-guarded write of the live within-item progress (ReviewReporter sink)."""
    with _LOCK:
        _STATE["progress"] = progress


def status() -> dict[str, Any]:
    """Poll payload: ``{status, total, completed, error, started_at}``.

    ``status`` is ``running`` while in flight, ``error`` after a job-level
    failure, ``ready`` once at least one item has been reviewed, else ``idle``.
    """
    with _LOCK:
        running = bool(_STATE["running"])
        total = int(_STATE["total"])
        completed = int(_STATE["completed"])
        error = _STATE["error"]
        started_at = _STATE["started_at"]
        progress = dict(_STATE["progress"])
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
        "total": total,
        "completed": completed,
        "error": error,
        "started_at": started_at,
        "progress": progress,
    }


def run_in_background(target) -> None:
    threading.Thread(target=target, daemon=True).start()


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


def get_cached_review(item_key: str) -> dict[str, Any] | None:
    """The stored deep-review entry for an item (for ``review_detail``), or None."""
    if not item_key:
        return None
    return _read_all().get(item_key)


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
        reporter = _deep_review_progress.ReviewReporter(item_key, title, _set_progress)
        reporter.phase("extract")
        # A corrupt PDF raises out of extract_text and is handled by the per-item
        # boundary in _run_job.
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


def _run_job(items: list[dict[str, Any]], *, focus_prompt: str = "") -> None:
    """Background worker: deep-review ``items`` concurrently, writing the cache
    incrementally from this (single) thread so progress is visible. The LLM is
    the configured **deep_review** stage client (``goals.yaml:
    llm_routing.deep_review``); a missing key / unreachable provider surfaces as
    this job's error, never an app crash."""
    try:
        app = get_state()
        config = app.app_state.config
        cfg = config.quality_review
        extractor = getattr(app, "pdf_extractor", None)
        reader = getattr(app, "zotero_reader", None)
        if reader is None:
            raise RuntimeError("Zotero reader unavailable; cannot deep-review")
        quality_enabled = bool(cfg.enabled and extractor is not None)
        llm = app.resolve_stage_client("deep_review")
        # DIGEST reasons (quality); trivial calls keep the fast thinking-off default
        # (a thinking-off digest goes empty/hallucinates — see README). No-op sans flag.
        llm_digest = app.resolve_stage_client("deep_review", enable_thinking=True)
        provider = app.resolve_stage_provider("deep_review")
        # `lean_deep_review` providers (ollama, prefill-bound) use the cheaper tier
        # (smaller text, 1 rubric run, batched goals); keyed on the flag, NOT
        # is_local (MLX is loopback but fast). See services/library/README.md.
        lean_tier = bool(getattr(provider, "lean_deep_review", False))
        # Sub-call concurrency: how many rubric samples / goal calls run in parallel
        # WITHIN a single paper review (local→serial for RAM safety, remote→capped).
        # Shared with verify-deep-review so the CLI receipt matches production.
        sub_concurrency = deep_review_sub_concurrency(provider)

        cache = _read_all()
        # Library-wide prestige floor for the quality HIGHLIGHT bar (asymmetric —
        # never demotes uncited papers; inert when there's no OpenAlex coverage).
        prestige_scores, prestige_floor_value = _load_prestige_context()
        # Serial for a local model (protect RAM); a remote one fans out across papers —
        # capped by its own max_sub_concurrency, else all N (NOT the local-RAM triage knob).
        max_workers = deep_review_fleet_concurrency(provider, len(items) or 1)
        wrote = 0
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    _review_one,
                    it,
                    reader=reader,
                    config=config,
                    extractor=extractor,
                    llm=llm,
                    llm_digest=llm_digest,
                    quality_enabled=quality_enabled,
                    focus_prompt=focus_prompt,
                    prestige_scores=prestige_scores,
                    prestige_floor_value=prestige_floor_value,
                    lean_tier=lean_tier,
                    sub_concurrency=sub_concurrency,
                ): it
                for it in items
            }
            for future in as_completed(futures):
                it = futures[future]
                try:
                    entry = future.result()
                    if entry is not None:
                        cache[str(it["item_key"])] = entry
                        _write_all(cache)
                        _try_rebuild_render(str(it["item_key"]))
                        wrote += 1
                except Exception as exc:  # noqa: BLE001 — per-item background boundary
                    LOGGER.warning("deep_review failed item=%s: %s", it.get("item_key"), exc)
                    errors.append(f"{type(exc).__name__}: {exc}")
                with _LOCK:
                    _STATE["completed"] += 1
        # Fail loud when the run produced NOTHING and items errored (e.g. the
        # deep_review LLM endpoint is unreachable): otherwise the job reports a
        # clean 'ready' with an empty cache and the UI silently shows no digest.
        # A partial run (≥1 cached entry) stays 'ready' — empty results are valid.
        finish(error=_deep_review_errors.summarize_errors(errors, provider) if errors and wrote == 0 else None)
    except Exception as exc:  # noqa: BLE001 — background worker boundary
        LOGGER.exception("deep_review job crashed")
        finish(error=f"{type(exc).__name__}: {exc}")


def start(
    top_k: int = _DEFAULT_TOP_K,
    *,
    item_keys: list[str] | None = None,
    focus_prompt: str = "",
    pdf_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Kick off a deep review (single-flight). With ``item_keys`` set, reviews
    exactly those papers (the per-paper button); otherwise the top-``top_k``
    unread picks. ``focus_prompt`` shapes the LLM's emphasis for this run.

    ``pdf_overrides`` maps ``item_key -> local PDF path``: the review-fleet passes a
    cache download it acquired via the university-browser/OA chain so the paper is
    reviewed from that path instead of a (missing) Zotero attachment. Only honored
    for the ``item_keys`` branch.

    Returns ``status()`` + ``accepted`` (did THIS call claim the single-flight
    slot?) — the review-fleet checks it to tell its own job from a foreign one.
    """
    if not try_start():
        return {**status(), "accepted": False}
    overrides = pdf_overrides or {}
    try:
        if item_keys:
            # Per-paper: title is re-read inside _review_one; gate_relevance is
            # display-only and pulled from the queue's score cache when present.
            items = [
                {
                    "item_key": key,
                    "title": "",
                    "gate_relevance": (reading_queue.get_cached_scoring(key) or {}).get("composite_score"),
                    "pdf_path": overrides.get(key, ""),
                }
                for key in item_keys
            ]
        else:
            queue = reading_queue.build_reading_queue(limit=max(1, top_k))
            items = [
                {
                    "item_key": row["item_key"],
                    "title": row.get("title") or "",
                    "gate_relevance": row.get("relevance_score"),
                }
                for row in (queue.get("items") or [])[:top_k]
            ]
    except Exception as exc:  # noqa: BLE001 — surface queue-build failure as job error
        finish(error=f"{type(exc).__name__}: {exc}")
        return {**status(), "accepted": True}

    with _LOCK:
        _STATE["total"] = len(items)
        _STATE["completed"] = 0

    if not items:
        finish(error=None)
        return {**status(), "accepted": True}

    run_in_background(lambda: _run_job(items, focus_prompt=focus_prompt))
    return {**status(), "accepted": True}


__all__ = ["start", "status", "get_cached_review", "try_start", "finish"]
