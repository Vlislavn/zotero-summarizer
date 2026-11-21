"""One background build per paper: whole body (no references) -> one model call -> verified card.

``request`` is the API's single entry (``start=False`` reads, ``start=True`` also launches the job).
The job runs on its OWN single-thread pool: it never enters the review queue, so it cannot delay the
interactive review lane. Failures are remembered per stamp for ``RETRY_AFTER_SECONDS`` (polling and
double clicks never repeat a model call); a local provider waits while a deep review runs."""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from zotero_summarizer.models.providers import resolve_stage
from zotero_summarizer.services._common import now_iso_z, state
from zotero_summarizer.services.library import _paper_body, _paper_read_pdf, _review_attempt, deep_review
from zotero_summarizer.services.library._review_identity import _source_sha
from zotero_summarizer.services.library.method_card import _prompt, _store, _verify
from zotero_summarizer.services.library.review_eligibility import same_pdf_entry
from zotero_summarizer.services.llm import factory

LOGGER = logging.getLogger(__name__)

RETRY_AFTER_SECONDS = 300.0
_LOCK = threading.Lock()
_RUNNING: set[str] = set()
_FAILED: dict[str, dict[str, Any]] = {}  # item_key -> {stamp, at (monotonic), reason}
_PERMANENT = {"too_long", "no_text"}  # retrying cannot help until the paper/model/window changes
_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="method-card")


class _Unbuildable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _answer(card_state: str, *, reason: str = "", can_build: bool = False, retry_in: int = 0,
            card: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"state": card_state, "reason": reason, "can_build": can_build,
            "retry_in_seconds": retry_in, "card": card}


def _build(item_key: str, entry: dict[str, Any], source_sha: str, stamp: str, model: str) -> None:
    app = state()
    config = app.app_state.config
    pdf_path = str((entry.get("review_identity") or {}).get("source_path") or "")
    if not pdf_path or _source_sha(pdf_path) != source_sha:
        raise _Unbuildable("source_changed")
    sections = _paper_read_pdf.extract_pdf_content(Path(pdf_path), use_docling=config.quality_review.use_docling).get("sections") or []
    body, window = _paper_body.read_body(app.pdf_extractor, pdf_path, config, False)
    if not body.strip():
        raise _Unbuildable("no_text")
    if len(body) > int(window["cap"]):
        raise _Unbuildable("too_long")  # never truncate: a card from part of the paper would claim more than it read
    llm = factory.resolve_stage_client(app, "deep_review", enable_thinking=False)
    with _review_attempt.Attempt() as attempt:
        raw = _review_attempt.observed_client(llm, "method_card").pydantic_prompt(
            prompt=_prompt.build_prompt(body), pydantic_model=_prompt.CardResponse)
    timing = attempt.timings()
    try:
        fields = _verify.verify_card(raw, sections, body)
    except _verify.CardNotGrounded as exc:
        raise _Unbuildable("not_grounded") from exc
    stage = timing["stages"].get("method_card", {})
    _store.write(item_key, source_sha, {
        "state": "ready", "card_version": _prompt.CARD_VERSION, "stamp": stamp, "source_sha256": source_sha,
        "prompt_sha256": _prompt.prompt_sha(), "model": model, "generated_at": now_iso_z(), **fields,
        "timing": {"seconds": timing["total_seconds"], "prompt_tokens": stage.get("prompt_tokens"),
                   "completion_tokens": stage.get("completion_tokens"), "chars": len(body)},
    })
    LOGGER.info("[method-card %s] built: %s", item_key, timing["stages"])


def _run(item_key: str, entry: dict[str, Any], source_sha: str, stamp: str, model: str) -> None:
    try:
        _build(item_key, entry, source_sha, stamp, model)
        with _LOCK:
            _FAILED.pop(item_key, None)
    except Exception as exc:  # noqa: BLE001 — optional enrichment: the paper page works without the card
        reason = exc.reason if isinstance(exc, _Unbuildable) else "model_error"
        LOGGER.warning("method card for %s failed (%s): %s", item_key, reason, exc)
        with _LOCK:
            _FAILED[item_key] = {"stamp": stamp, "at": time.monotonic(), "reason": reason}
    finally:
        with _LOCK:
            _RUNNING.discard(item_key)


def _failure_answer(failed: dict[str, Any]) -> dict[str, Any]:
    """Answer for a remembered failure still inside its retry window (or a permanent one)."""
    if failed["reason"] in _PERMANENT:
        return _answer("unavailable", reason=failed["reason"])
    left = int(RETRY_AFTER_SECONDS - (time.monotonic() - failed["at"])) + 1
    return _answer("failed", reason=failed["reason"], retry_in=left)


def request(item_key: str, *, start: bool) -> dict[str, Any]:
    """``{state: building|ready|failed|unavailable, reason, can_build, retry_in_seconds, card}``."""
    entry = deep_review.get_usable_review(item_key)  # current, or outdated but of the same PDF
    if not same_pdf_entry(entry):
        return _answer("unavailable", reason="no_review")
    app = state()
    provider = app.resolve_stage_provider("deep_review")
    if getattr(provider, "lean_deep_review", False):
        return _answer("unavailable", reason="lean_provider")
    source_sha = str((entry.get("review_identity") or {}).get("source_sha256") or "")
    model = resolve_stage(app.app_state.config.llm_routing, "deep_review").model
    stamp = _store.make_stamp(source_sha, _prompt.prompt_sha(), model)
    card = _store.read(item_key, source_sha, stamp)
    if card is not None:
        return _answer("ready", card=card)
    with _LOCK:
        if item_key in _RUNNING:
            return _answer("building")
        failed = _FAILED.get(item_key)
        failed = failed if failed and failed["stamp"] == stamp else None
        if failed and (failed["reason"] in _PERMANENT or time.monotonic() - failed["at"] < RETRY_AFTER_SECONDS):
            return _failure_answer(failed)
        if not start:
            return _answer("failed", reason=failed["reason"], can_build=True) if failed else _answer(
                "unavailable", reason="not_built", can_build=True)
        if getattr(provider, "is_local", False) and deep_review.status()["status"] == "running":
            return _answer("unavailable", reason="review_running", can_build=True)
        _RUNNING.add(item_key)
        _POOL.submit(_run, item_key, entry, source_sha, stamp, model)
    return _answer("building")
