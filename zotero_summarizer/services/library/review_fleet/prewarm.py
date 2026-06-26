"""Launch-time PREWARM of the review fleet: pre-decide the top-N reading verdicts.

Sibling of ``deep_review_prewarm`` — modeled 1:1 on its ``schedule_on_startup``
pattern. On startup it spawns the single-flight ``fleet`` job in the background so
the Read-next top picks already carry a ``proposed_verdict`` the first time the
user opens the library (Confirm/Override, not decide-from-scratch).

It is a thin orchestrator: the ranking, the deep-review reuse, the RAM-safe serial
loop, and the proposal math all live in ``fleet``/``propose``. This module only
resolves the top-N knob and the enable gate, then hands off.

Configured by ``quality_review.prewarm_on_startup_k`` (the SAME knob the
deep-review prewarm uses — the fleet pre-decides exactly the picks deep review
warms), overridable by ``ZS_REVIEW_FLEET_PREWARM_K``; ``0`` disables. Best-effort
at the daemon-thread boundary: a failure is logged and swallowed so prewarm never
blocks or crashes startup. Skipped when deep review is disabled or Zotero is
unavailable (no local PDFs → no reviews → nothing to pre-decide).
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.services._common import LOGGER
from zotero_summarizer.services.library import _flight
from zotero_summarizer.services.library.review_fleet import fleet

_ENV_PREWARM_K = "ZS_REVIEW_FLEET_PREWARM_K"


def resolve_prewarm_k(config: Any) -> int:
    """Top-N to pre-decide, from config + the ``ZS_REVIEW_FLEET_PREWARM_K`` env
    override. Thin wrapper over the shared resolver — see ``_flight.resolve_prewarm_k``."""
    return _flight.resolve_prewarm_k(config, env_var=_ENV_PREWARM_K)


def _prewarm_worker(k: int) -> None:
    """Background entry: kick off the single-flight fleet for the top-``k`` picks.
    Single broad-except boundary — prewarm must never crash the app."""
    try:
        LOGGER.info("review-fleet prewarm: pre-deciding the top-%d Read-next pick(s)", k)
        fleet.start(top_k=k)
    except Exception as exc:  # noqa: BLE001 — background-worker boundary; prewarm is best-effort
        LOGGER.warning("review-fleet prewarm failed: %s", exc)


def schedule_on_startup(config: Any, app_state: Any) -> bool:
    """Spawn the prewarm worker when enabled; return whether it was scheduled (for
    the startup log). Skipped when ``prewarm_on_startup_k`` is 0, deep review is
    disabled, or Zotero is unavailable (no local PDFs to review)."""
    k = resolve_prewarm_k(config)
    if k <= 0 or not config.quality_review.enabled or getattr(app_state, "zotero_reader", None) is None:
        return False
    _flight.run_in_background(lambda: _prewarm_worker(k))
    return True


__all__ = ["schedule_on_startup", "resolve_prewarm_k"]
