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
item as one short note (best-effort). Concurrency reuses
``settings().triage_job_concurrency``.

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

from zotero_summarizer.services._common import now_iso_z

from zotero_summarizer.services.library import quality_review, reading_queue
from zotero_summarizer.services._adapters import build_triage_llm
from zotero_summarizer.services._common import settings as get_settings
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
        return True


def finish(error: str | None = None) -> None:
    with _LOCK:
        _STATE["running"] = False
        _STATE["error"] = error


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
    }


def run_in_background(target) -> None:
    threading.Thread(target=target, daemon=True).start()


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
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"updated_at": now_iso_z(), "reviews": reviews}, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(path)


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
) -> dict[str, Any] | None:
    """Condensed paper DIGEST (what it's about + how to use it + quality) for one
    library item, from its LOCAL Zotero PDF only. ``None`` when the item vanished
    from Zotero (caller skips it).

    No app-side download: the user's sources (e.g. bioRxiv) sit behind a
    Cloudflare challenge a headless client can't pass, so PDFs come from Zotero's
    "Find Available PDF". When the item has no local PDF we mark ``needs_pdf`` and
    do nothing else — honest, and cheap (no doomed fetch). The digest is also
    written to Zotero as one short note (best-effort; the in-app digest is
    unaffected by a note-write failure).
    """
    item_key = str(item["item_key"])
    detail = reader.get_item_detail(item_key)
    if detail is None:
        return None

    title = str(detail.get("title") or item.get("title") or "") or "Untitled"
    pdf_path = str(detail.get("pdf_path") or "")

    digest_dump: dict[str, Any] | None = None
    note_written = False
    note_error: str | None = None

    if pdf_path and quality_enabled:
        # A corrupt PDF raises out of extract_text and is handled by the per-item
        # boundary in _run_job.
        text = extractor.extract_text(pdf_path).strip()
        if text:
            digest = quality_review.assess_digest(
                title=title, full_text=text, config=config, llm=llm,
            )
            digest_dump = digest.model_dump()
            try:
                from zotero_summarizer.services.zotero.zotero import zotero_upsert_digest_note
                zotero_upsert_digest_note(item_key, digest)
                note_written = True
            except Exception as exc:  # noqa: BLE001 — note write must not fail the digest
                note_error = f"{type(exc).__name__}: {exc}"
                LOGGER.warning("digest note write for %s failed: %s", item_key, exc)

    return {
        "digest": digest_dump,
        "needs_pdf": not bool(pdf_path),
        "gate_relevance": item.get("gate_relevance"),
        "reviewed_at": now_iso_z(),
        "zotero_note_written": note_written,
        "zotero_note_error": note_error,
    }


def _run_job(items: list[dict[str, Any]], *, model: str) -> None:
    """Background worker: deep-review ``items`` concurrently, writing the cache
    incrementally from this (single) thread so progress is visible."""
    try:
        app = get_state()
        config = app.app_state.config
        cfg = config.quality_review
        extractor = getattr(app, "pdf_extractor", None)
        reader = getattr(app, "zotero_reader", None)
        if reader is None:
            raise RuntimeError("Zotero reader unavailable; cannot deep-review")
        quality_enabled = bool(cfg.enabled and extractor is not None)
        llm = build_triage_llm(model)

        cache = _read_all()
        max_workers = max(1, min(get_settings().triage_job_concurrency, len(items) or 1))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    _review_one,
                    it,
                    reader=reader,
                    config=config,
                    extractor=extractor,
                    llm=llm,
                    quality_enabled=quality_enabled,
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
                except Exception as exc:  # noqa: BLE001 — per-item background boundary
                    LOGGER.warning("deep_review failed item=%s: %s", it.get("item_key"), exc)
                with _LOCK:
                    _STATE["completed"] += 1
        finish(error=None)
    except Exception as exc:  # noqa: BLE001 — background worker boundary
        LOGGER.exception("deep_review job crashed")
        finish(error=f"{type(exc).__name__}: {exc}")


def start(
    top_k: int = _DEFAULT_TOP_K, *, item_keys: list[str] | None = None, model: str = "sota",
) -> dict[str, Any]:
    """Kick off a deep review (single-flight). With ``item_keys`` set, reviews
    exactly those papers (the per-paper button); otherwise the top-``top_k``
    unread picks.

    Returns the current ``status()``. A no-op (returns the in-flight status)
    when a run is already going.
    """
    if not try_start():
        return status()
    try:
        if item_keys:
            # Per-paper: title is re-read inside _review_one; gate_relevance is
            # display-only and pulled from the queue's score cache when present.
            items = [
                {
                    "item_key": key,
                    "title": "",
                    "gate_relevance": (reading_queue.get_cached_scoring(key) or {}).get("composite_score"),
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
        return status()

    with _LOCK:
        _STATE["total"] = len(items)
        _STATE["completed"] = 0

    if not items:
        finish(error=None)
        return status()

    run_in_background(lambda: _run_job(items, model=model))
    return status()


__all__ = ["start", "status", "get_cached_review", "try_start", "finish"]
