"""Launch-time PREWARM of deep reviews for the top-scored unread picks.

Deep review (``services.library.deep_review``) is on-demand: the user opens a paper
and waits a few minutes while the local LLM produces the digest + quality verdict +
goal board. The result is cached in ``deep_reviews.json``, so the SECOND open is
instant — but the first always pays the full cost. This module closes that gap: on
startup it background-computes the top-``prewarm_on_startup_k`` not-yet-cached reviews
so the first open is instant too.

It is a thin ORCHESTRATOR over existing primitives — no new ranking, no new review
engine: it reuses ``reading_queue.build_reading_queue`` for the ranked picks,
``deep_review.get_cached_review`` to skip what is already done, and
``deep_review.start(item_keys=…)`` (the per-paper entry, single-flight) to run the
missing ones. Concurrency + RAM-safety are INHERITED from ``deep_review`` (serial on a
local provider, parallel on a remote one) — "parallel on launch" where it is safe.

Configured by ``quality_review.prewarm_on_startup_k`` (goals.yaml), overridable by the
``ZS_DEEP_REVIEW_PREWARM_K`` env var; ``0`` disables. Best-effort: every failure is
logged and swallowed at the background-worker boundary, so prewarm never blocks or
crashes startup. Skip-if-cached makes re-launches cheap (only genuinely-new top picks
run; the same top-K never recompute).
"""
from __future__ import annotations

import threading
import time
from typing import Any

from zotero_summarizer.services._common import LOGGER
from zotero_summarizer.services.library import _flight, deep_review, reading_queue

_ENV_PREWARM_K = "ZS_DEEP_REVIEW_PREWARM_K"

# Safety-net cap (not a per-call magic timeout): a cold first launch trains the
# relevance gate in the background (lifecycle._init_classifier_gate), so we poll a
# cheap readiness flag up to this bound, then proceed with whatever ranking exists.
PREWARM_GATE_WAIT_SECS = 120.0
PREWARM_POLL_INTERVAL_SECS = 3.0


def resolve_prewarm_k(config: Any) -> int:
    """Top-N to prewarm, from config + the ``ZS_DEEP_REVIEW_PREWARM_K`` env override.
    Thin wrapper over the shared resolver — see ``_flight.resolve_prewarm_k``."""
    return _flight.resolve_prewarm_k(config, env_var=_ENV_PREWARM_K)


def _select_uncached_top(k: int) -> list[str]:
    """The top-``k`` unread picks (by the queue's relevance × goal × prestige blend)
    that do NOT yet have a cached deep review — the only ones worth computing."""
    queue = reading_queue.build_reading_queue(limit=max(1, k))
    rows = (queue.get("items") or [])[:k]
    return [
        key
        for row in rows
        if (key := str(row.get("item_key") or "")) and deep_review.get_cached_review(key) is None
    ]


def _wait_for_gate_ready(config: Any, app_state: Any) -> None:
    """Block (bounded) until the relevance gate is loaded, so "top" reflects real
    scores instead of the cold-start recency fallback. No-op when the gate is
    disabled (fallback ranking is then final) or already loaded."""
    if not config.classifier_gate.enabled:
        return
    waited = 0.0
    while waited < PREWARM_GATE_WAIT_SECS:
        if getattr(app_state, "classifier_gate", None) is not None:
            return
        time.sleep(PREWARM_POLL_INTERVAL_SECS)
        waited += PREWARM_POLL_INTERVAL_SECS


def _prewarm_worker(k: int, config: Any, app_state: Any) -> None:
    """Background entry: wait for the gate, pick the uncached top-``k``, deep-review
    them via the single-flight ``deep_review`` job. Single broad-except boundary —
    prewarm must never crash the app."""
    try:
        _wait_for_gate_ready(config, app_state)
        keys = _select_uncached_top(k)
        if not keys:
            LOGGER.info("deep-review prewarm: top-%d already cached, nothing to do", k)
            return
        LOGGER.info("deep-review prewarm: warming %d uncached pick(s): %s", len(keys), keys)
        deep_review.start(item_keys=keys)
    except Exception as exc:  # noqa: BLE001 — background-worker boundary; prewarm is best-effort
        LOGGER.warning("deep-review prewarm failed: %s", exc)


def run_in_background(target) -> None:
    threading.Thread(target=target, name="deep-review-prewarm", daemon=True).start()


def schedule_on_startup(config: Any, app_state: Any) -> bool:
    """Spawn the prewarm worker when enabled; return whether it was scheduled (for
    the startup log). Skipped when ``prewarm_on_startup_k`` is 0, deep review is
    disabled, or Zotero is unavailable (no local PDFs to review)."""
    k = resolve_prewarm_k(config)
    if k <= 0 or not config.quality_review.enabled or getattr(app_state, "zotero_reader", None) is None:
        return False
    run_in_background(lambda: _prewarm_worker(k, config, app_state))
    return True


__all__ = ["schedule_on_startup", "resolve_prewarm_k"]
