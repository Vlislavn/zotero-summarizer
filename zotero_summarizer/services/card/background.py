"""Background AI-card generation for Today's top papers (Todo #92).

Fired by the existing feed tick (alongside ``today_preparation``): generate the AI
card for Today's top-ranked feed papers ahead of the owner's visit, at most 4 in
flight, NEVER blocking a request. One run at a time (single-flight latch); each
paper goes through :func:`services.card.card.generate_card`, so consent and every
spend guard (call cap, daily token ledger, circuit breaker) still apply. No consent
→ nothing to do. A per-paper failure is logged and the run continues.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from zotero_summarizer.services._common import LOGGER
from zotero_summarizer.services.card import _consent, _store, card
from zotero_summarizer.services.library._flight import FlightLatch, run_in_background

TOP_K = 15
MAX_IN_FLIGHT = 4
_SCAN_LIMIT = 60

_latch = FlightLatch()


def top_keys(db_path: Path, limit: int) -> list[str]:
    """Stable feed keys of the top-ranked un-handled Today candidates, then the
    Today shelf's unreviewed papers (``limit`` each; duplicates dropped)."""
    from zotero_summarizer.services.library import deep_review
    from zotero_summarizer.services.triage.daily_select import build_analyzed_slate, ranked_unhandled_candidates

    keys: list[str] = []
    ranked = ranked_unhandled_candidates(db_path, lookback_hours=168)
    for cand in ranked[:_SCAN_LIMIT]:
        key = (cand.get("stable_feed_key") or "").strip()
        if key and not (cand.get("materialized_zotero_key") or "").strip():
            keys.append(key)
        if len(keys) >= limit:
            break
    shelf = build_analyzed_slate(ranked, deep_review.usable_reviews(), K=limit, lookback_hours=168).unreviewed
    keys.extend(dict.fromkeys(p.stable_feed_key for p in shelf if p.stable_feed_key and p.stable_feed_key not in keys))
    return keys


def start(db_path: Path, *, limit: int = TOP_K) -> bool:
    """Start one background run; False if one is already running, AI is unavailable,
    or spend is already blocked."""
    if limit <= 0 or not _consent.has_consent() or _store.spend_blocked():
        return False
    if not _latch.try_start():
        return False
    run_in_background(lambda: _run(db_path, limit), name="card-generation")
    return True


def _generate_one(key: str) -> None:
    try:
        if _store.spend_blocked():  # stop spending as soon as a cap/breaker trips
            return
        card.generate_card(key)
    except Exception:  # noqa: BLE001 — per-paper boundary; the run continues
        LOGGER.exception("card generation failed key=%s", key)


def _run(db_path: Path, limit: int) -> None:
    error = None
    try:
        keys = top_keys(db_path, limit)
        with ThreadPoolExecutor(max_workers=MAX_IN_FLIGHT, thread_name_prefix="card-gen") as pool:
            list(pool.map(_generate_one, keys))
        LOGGER.info("card generation: processed %d Today paper(s)", len(keys))
    except Exception as exc:  # noqa: BLE001 — background-thread boundary
        LOGGER.exception("card generation run failed")
        error = f"{type(exc).__name__}: {exc}"
    finally:
        _latch.finish(error)


__all__ = ["MAX_IN_FLIGHT", "TOP_K", "start", "top_keys"]
