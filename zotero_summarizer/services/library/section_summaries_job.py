"""On-open Paper-map one-liners: "what each section covers", built when a paper is
opened instead of at review time (Today never reads them).

The review stores the overlay with titles + pages only. ``request`` is the idempotent
entry point the paper page polls: it returns the finished sidecar, or starts ONE
background job (generation + support check, ``_paper_section_summaries``) and reports
``running``. The sidecar lives under ``Settings.paper_render_dir`` and is stamped with the
review's identity and Paper-map outline, so a re-review or a different outline invalidates it. Failures are remembered for a short
time so polling cannot hammer the provider; a local provider runs one job at a time and
waits while a deep review runs; the full tier only (the lean local path keeps
its minimal call count and shows titles + pages).
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from zotero_summarizer.services._common import settings, state
from zotero_summarizer.models.providers import resolve_stage
from zotero_summarizer.services._common import now_iso_z
from zotero_summarizer.services.library import _paper_body, _paper_read_pdf, _paper_section_summaries, _review_attempt, deep_review
from zotero_summarizer.services.library.review_eligibility import same_pdf_entry
from zotero_summarizer.services.library._review_identity import _source_sha
from zotero_summarizer.services.llm import factory

LOGGER = logging.getLogger(__name__)

_RETRY_AFTER_SECONDS = 300.0
_LOCK = threading.Lock()
_POOL_LOCK = threading.Lock()
_RUNNING: set[str] = set()
_FAILED: dict[str, tuple[str, float]] = {}  # item_key -> (stamp, monotonic time)
_POOLS: dict[int, ThreadPoolExecutor] = {}  # worker count -> pool (1 for a local provider)


def _pool(workers: int) -> ThreadPoolExecutor:
    with _POOL_LOCK:
        if workers not in _POOLS:
            _POOLS[workers] = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="section-summaries")
        return _POOLS[workers]


def _answer(status: str, summaries: dict[str, str] | None = None) -> dict[str, Any]:
    return {"status": status, "summaries": summaries or {}}


def _sidecar(item_key: str) -> Path:
    digest = hashlib.sha256(item_key.encode()).hexdigest()[:32]
    return settings().paper_render_dir / "_section_summaries" / f"{digest}.json"


def _outline(sections: list[dict[str, Any]]) -> list[tuple[Any, Any, Any]]:
    return [(s.get("id"), s.get("title"), s.get("page")) for s in sections]


def _stamp(entry: dict[str, Any]) -> str:
    """Review identity + the outline the one-liners attach to (ids are positional)."""
    identity = entry.get("review_identity") or {}
    outline = hashlib.sha256(json.dumps(_outline(entry["section_overlay"]["sections"])).encode()).hexdigest()[:16]
    return f"{identity.get('generation_sha256')}:{identity.get('source_sha256')}:{outline}"


def _read(item_key: str, stamp: str) -> dict[str, Any] | None:
    try:
        stored = json.loads(_sidecar(item_key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return stored if isinstance(stored, dict) and stored.get("stamp") == stamp else None


def _write(item_key: str, stamp: str, summaries: dict[str, str], timing: dict[str, Any]) -> None:
    path = _sidecar(item_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"stamp": stamp, "summaries": summaries, "timing": timing}), encoding="utf-8")
    tmp.replace(path)


def _generate(item_key: str, entry: dict[str, Any], stamp: str) -> None:
    try:
        identity = entry["review_identity"]
        pdf_path = str(identity.get("source_path") or "")
        if not pdf_path or _source_sha(pdf_path) != identity.get("source_sha256"):
            raise ValueError("review source PDF changed or is missing")
        app = state()
        config = app.app_state.config
        content = _paper_read_pdf.extract_pdf_content(Path(pdf_path), use_docling=config.quality_review.use_docling)
        body, _stats = _paper_body.extract_body(app.pdf_extractor, pdf_path)
        cap = _paper_body.text_cap(config.quality_review, resolve_stage(config.llm_routing, "deep_review").provider, lean_tier=False).chars
        fresh = content.get("sections") or []
        stored = entry["section_overlay"]["sections"]
        if [(x.get("id"), x.get("title")) for x in fresh] != [(x.get("id"), x.get("title")) for x in stored]:
            raise ValueError("section outline differs from the review's (ids are positional)")
        # Same cheap-by-identity client the review used for this layer (feed under map-reduce).
        stage = "feed" if config.quality_review.chunk_strategy == "map_reduce" else "deep_review"
        llm = factory.resolve_stage_client(app, stage, enable_thinking=False)
        started = time.perf_counter()
        summaries = _paper_section_summaries.summarize_sections(
            fresh, _review_attempt.observed_client(llm, "sections"), body=body if len(body) <= cap else None)
        seconds = time.perf_counter() - started
        LOGGER.info("[sections %s] %d one-liners in %.1fs", item_key, len(summaries), seconds)
        _write(item_key, stamp, summaries, {
            "model": resolve_stage(config.llm_routing, stage).model, "seconds": round(seconds, 1),
            "at": now_iso_z()})
    except Exception as exc:  # noqa: BLE001 — optional enrichment: the map stays titles + pages
        LOGGER.warning("on-open section summaries for %s failed: %s", item_key, exc)
        with _LOCK:
            _FAILED[item_key] = (stamp, time.monotonic())
    finally:
        with _LOCK:
            _RUNNING.discard(item_key)


def request(item_key: str) -> dict[str, Any]:
    """``{status: ready|running|unavailable, summaries: {section_id: sentence}}``."""
    entry = deep_review.get_usable_review(item_key)  # current, or outdated but of the same PDF
    overlay = (entry or {}).get("section_overlay") or {}
    provider = state().resolve_stage_provider("deep_review")
    if (not same_pdf_entry(entry) or not overlay.get("sections") or overlay.get("degraded")
            or getattr(provider, "lean_deep_review", False)):
        return _answer("unavailable")
    stamp = _stamp(entry)
    stored = _read(item_key, stamp)
    if stored is not None:
        return _answer("ready", stored.get("summaries"))
    with _LOCK:
        failed = _FAILED.get(item_key)
        if failed and failed[0] == stamp and time.monotonic() - failed[1] < _RETRY_AFTER_SECONDS:
            return _answer("unavailable")
        local = bool(getattr(provider, "is_local", False))
        if local and deep_review.status()["status"] == "running":
            return _answer("running")  # the page keeps polling; start once the review is done
        if item_key not in _RUNNING:
            _RUNNING.add(item_key)
            _pool(1 if local else 2).submit(_generate, item_key, entry, stamp)
    return _answer("running")


__all__ = ["request"]
