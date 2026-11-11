"""Which PDF this Mac already holds for a paper, found without the network.

Rungs: Zotero's attachment → the PDF a paper-read build acquired → the PDF a stored
full-text review was made from. ``pdf_for_item`` can then acquire one, if allowed.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services._common import settings
from zotero_summarizer.services.library import _review_cache


def item_detail(item_key: str) -> dict[str, Any]:
    # Key-aware: a stable_feed_key (un-materialized Today paper) resolves via the app
    # library reader even when live Zotero is present; a Zotero key uses the live reader.
    from zotero_summarizer.services.zotero.zotero import resolve_reader_for_key

    detail = resolve_reader_for_key(item_key).get_item_detail(item_key)
    if detail is None:
        raise APIError(error="not_found", message=f"Item {item_key} not found", status_code=404)
    return detail


def allowed_path(path: str | Path | None) -> Path:
    if not path:
        raise APIError(error="not_found", message="Paper artifact path is missing", status_code=404)
    resolved = Path(path).expanduser().resolve()
    roots = (settings().pdf_root, settings().pdf_cache_dir)
    if not any(resolved.is_relative_to(root.expanduser().resolve()) for root in roots):
        raise APIError(error="path_not_allowed", message="Paper path is outside allowed roots", status_code=403)
    return resolved


def _pdf_from_detail(item_key: str, detail: dict[str, Any]) -> Path:
    pdf_path = Path(str(detail.get("pdf_path") or ""))
    if not str(pdf_path) or not pdf_path.is_file():
        raise APIError(error="needs_pdf", message=f"No local PDF for item {item_key}", status_code=404)
    return allowed_path(pdf_path)


def _review_pdf(item_key: str) -> Path | None:
    """The file a stored full-text review (current or outdated) was made from, if still held;
    legacy entries ``get_cached_review`` no longer serves (None) are skipped."""
    recorded = ((_review_cache.get_cached_review(item_key) or {}).get("acquired_pdf") or {}).get("path")
    if not recorded:
        return None
    try:
        path = allowed_path(recorded)
    except APIError:
        return None
    return path if path.is_file() else None


def pdf_for_item(
    item_key: str, *, read_state: Callable[[], dict[str, Any] | None], allow_acquire_missing: bool,
) -> tuple[Path, dict[str, Any]]:
    """Return a local PDF path (``read_state``: the paper-read state, called only when Zotero holds
    no attachment), else acquire one into the fetch cache when allowed (never written to Zotero)."""
    detail = item_detail(item_key)
    try:
        return _pdf_from_detail(item_key, detail), detail
    except APIError as exc:
        if exc.error != "needs_pdf":
            raise
        saved = read_state() or {}
        held = allowed_path(saved["pdf_path"]) if saved.get("pdf_path") and saved.get("acquired_pdf") else None
        held = held if held is not None and held.is_file() else _review_pdf(item_key)
        if held is not None:
            return held, {**detail, "pdf_path": str(held), "has_pdf": True, "acquired_pdf": True}
        if not allow_acquire_missing:
            raise
    return _acquire(item_key, detail)


def _acquire(item_key: str, detail: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    from zotero_summarizer.services.library import _pdf_acquire  # lazy: browser stack is optional

    acquired = _pdf_acquire.acquire_pdf_for(item_key, detail, allow_headed_fallback=True)
    if acquired.path is not None:
        path = allowed_path(acquired.path)
        if not path.is_file():
            message = f"PDF acquisition for item {item_key} returned a missing file"
            raise APIError(error="needs_pdf", message=message, status_code=404)
        return path, {**detail, "pdf_path": str(path), "has_pdf": True, "acquired_pdf": True}
    if acquired.needs_login:
        raise APIError(
            error="needs_library_login",
            message=(
                "The paper has no local Zotero PDF, and the browser/university "
                "session could not fetch it. Open the publisher page in Chrome or "
                "refresh University access, then retry."
            ),
            status_code=424,
            details={"login_url": acquired.login_url},
        )
    if acquired.outcome == "browser_extra_unavailable":
        raise APIError(
            error="browser_extra_unavailable",
            message="Browser support is not installed; open Settings → University access.",
            status_code=424,
        )
    raise APIError(
        error="needs_pdf",
        message=f"No local PDF or fetchable full-text source for item {item_key}.",
        status_code=404,
    )
