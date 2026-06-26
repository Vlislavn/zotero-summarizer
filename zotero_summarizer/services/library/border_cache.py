"""Disk cache + background-job state for active-learning border suggestions.

Scoring every library row for border-distance is inherently expensive
(~1 s/row: OpenAlex enrichment + library features), so computing it
synchronously on each request made the endpoint take >10 minutes. This
module turns it into a *cached, background-computed* resource:

* The result is persisted to ``{model_dir}/border_suggestions.json``
  keyed by the golden CSV sha. A GET returns the cached list instantly
  when the sha matches.
* When the cache is stale/absent, the route starts a background thread
  and returns ``status="computing"``; the frontend polls until
  ``status="ready"``.

Single responsibility: cache I/O + job-state bookkeeping. The actual
scoring lives in ``services.active_learning.suggest_border_labels``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zotero_summarizer.services._common import now_iso_z
from zotero_summarizer.services.library import _flight


_CACHE_FILENAME = "border_suggestions.json"

# In-process job state. Only one border computation runs at a time; a
# second request while one is in flight just reports "computing".
_LATCH = _flight.FlightLatch()


def cache_path(model_dir: Path) -> Path:
    return model_dir / _CACHE_FILENAME


def read_cache(model_dir: Path, golden_sha: str) -> dict[str, Any] | None:
    """Return the cached payload iff it matches ``golden_sha``.

    Returns ``None`` when the cache file is absent or was computed for a
    different golden CSV (stale). A corrupt cache file is a real
    data-integrity problem and is allowed to raise.
    """
    path = cache_path(model_dir)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("golden_sha") != golden_sha:
        return None
    return payload


def write_cache(
    model_dir: Path,
    golden_sha: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Persist border suggestions for ``golden_sha``; return the payload."""
    model_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "golden_sha": golden_sha,
        "computed_at": now_iso_z(),
        "total": len(items),
        "items": items,
    }
    tmp = cache_path(model_dir).with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(cache_path(model_dir))
    return payload


def is_running() -> bool:
    return _LATCH.is_running()


def last_error() -> str | None:
    return _LATCH.last_error()


def try_start() -> bool:
    """Claim the single compute slot. Returns False if one already runs."""
    return _LATCH.try_start()


def finish(error: str | None = None) -> None:
    _LATCH.finish(error)


run_in_background = _flight.run_in_background
