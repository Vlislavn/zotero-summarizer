"""The active-learning border-suggestions endpoint, split out of ``golden.py``
to keep that file under the 500-LOC limit. Self-contained: only the golden
helpers + lazily-imported scoring/cache services. Mounted on ``golden.router``.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.api.routes._golden_helpers import (
    _compute_border_into_cache,
    _golden_csv_path,
)

router = APIRouter()


@router.get("/api/golden/border-suggestions")
async def border_suggestions(top_k: int = 20, refresh: bool = False) -> dict[str, Any]:
    """Active-learning endpoint: library rows whose re-labelling would most
    help the model, ranked by distance to the nearest priority threshold.

    Cached + background-computed (see ``services.border_cache``). Scoring
    every library row is ~1 s/row, so a synchronous compute took >10 min.
    Now:
      * ``status="ready"`` + items — cache hit for the current golden sha.
      * ``status="computing"`` — a background scoring pass is in flight;
        the client should poll.
      * ``status="error"`` — the last background pass failed (message set).

    ``refresh=true`` forces a recompute even when a fresh cache exists.
    """
    from zotero_summarizer.services.library import border_cache
    from zotero_summarizer.services import run_log
    from zotero_summarizer.services._common import settings

    if not (1 <= int(top_k) <= 2000):
        raise APIError(
            error="validation_error",
            message=f"top_k must be between 1 and 2000; got {top_k}",
            status_code=422,
        )

    csv_path = _golden_csv_path()
    if not csv_path.exists():
        raise APIError(
            error="not_found",
            message=f"golden CSV missing at {csv_path}",
            status_code=404,
        )

    golden_sha = run_log.file_sha256(csv_path, prefix_len=64)
    cached = None if refresh else border_cache.read_cache(settings().model_dir, golden_sha)
    if cached is not None:
        items = cached["items"][: int(top_k)]
        return {
            "status": "ready",
            "items": items,
            "total": len(items),
            "cached_total": cached.get("total", len(items)),
            "computed_at": cached.get("computed_at"),
        }

    # No fresh cache — ensure a background compute is running.
    if border_cache.try_start():
        border_cache.run_in_background(
            lambda: _compute_border_into_cache(golden_sha, int(top_k))
        )
        return {"status": "computing", "items": [], "total": 0}

    err = border_cache.last_error()
    if err is not None and not border_cache.is_running():
        return {"status": "error", "items": [], "total": 0, "message": err}
    return {"status": "computing", "items": [], "total": 0}
