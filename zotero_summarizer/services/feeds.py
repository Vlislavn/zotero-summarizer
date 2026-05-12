"""RSS-feed batch processor — Phase 1 one-shot + Phase 1.5 background daemon.

Phase 1 (kept for backwards compat + `feeds preview`):
    Zotero RSS feedItems
      -> dedup against user library (DOI / arXiv ID)
      -> dedup against prior runs (storage.feeds.processed_feed_items)
      -> triage each on title+abstract (no PDF)
      -> plateau-select top-N
      -> queue pending changes (review/apply via web UI)

Phase 1.5 (daemon, primary user workflow):
    Every N minutes:
      `run_daemon_tick`:
          - pick K unread feed items (round-robin across feeds)
          - triage each -> record as `triaged_pending`
          - mark them read in Zotero (feedItems.readTime = now)
          - opportunistically resolve M due outcomes -> write to user_feedback

    Once per day (when ticks notice 24h since last selection):
      `run_daily_selection`:
          - gather `triaged_pending` rows from rolling 24h
          - plateau-select top 1-2 (hard_min=daily_target_min, hard_max=daily_target_max)
          - allocate 0-1 black-swan slot
          - materialize selected items DIRECTLY into Zotero (Inbox + matched
            collections + tags + v3 note) — bypasses pending-changes queue
            because feed-sourced creates are low-blast-radius
          - update rows to `selected` / `black_swan` / `rejected_daily_cutoff`
          - schedule outcome detection N days out

User goal (captured this session): "1-2 good papers daily to read from my
feeds (best)". The daily selection's hard_min defaults to 1 and hard_max to 2.
"""
from __future__ import annotations

import asyncio
import logging
import random
import signal
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from zotero_summarizer.contracts import PendingChange
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.models import SummarizeRequest, SummarizeResponse
from zotero_summarizer.services import pending as pending_service
from zotero_summarizer.services import select as select_service
from zotero_summarizer.services import surprise as surprise_service
from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.services._common import state as get_state
from zotero_summarizer.services.summarization import run_abstract_pipeline
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.storage import repositories as triage_db

LOGGER = logging.getLogger("zotero_summarizer.services.feeds")

# 8-char Zotero key alphabet — must match ZoteroWriter._KEY_ALPHABET.
_ZOTERO_KEY_ALPHABET = "23456789ABCDEFGHIJKLMNPQRSTUVWXYZ"

_DEFAULT_BLACK_SWAN_TAG = "🦢 black-swan"


@dataclass
class TriagedCandidate:
    """A feed item that has gone through abstract-only triage."""

    feed_item: dict[str, Any]
    summary: SummarizeResponse
    composite_score: float
    surprise_score: float
    is_black_swan: bool = False
    planned_zotero_key: str | None = None

    @property
    def key(self) -> str:
        return f"{int(self.feed_item.get('feed_library_id') or 0)}:{int(self.feed_item.get('item_id') or 0)}"


@dataclass
class FeedRunReport:
    """Summary of one `feeds run` invocation (Phase 1 one-shot)."""

    run_id: str
    total_feed_items: int
    deduped_against_processed: int
    deduped_against_library: int
    triaged: int
    selected_by_plateau: int
    black_swans: int
    rejected: int
    cutoff: int
    cutoff_reason: str
    knee_index: int | None
    safety_cap: int
    queued_change_count: int
    errors: list[dict[str, Any]] = field(default_factory=list)
    dry_run: bool = False
    elapsed_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "total_feed_items": self.total_feed_items,
            "deduped_against_processed": self.deduped_against_processed,
            "deduped_against_library": self.deduped_against_library,
            "triaged": self.triaged,
            "selected_by_plateau": self.selected_by_plateau,
            "black_swans": self.black_swans,
            "rejected": self.rejected,
            "cutoff": self.cutoff,
            "cutoff_reason": self.cutoff_reason,
            "knee_index": self.knee_index,
            "safety_cap": self.safety_cap,
            "queued_change_count": self.queued_change_count,
            "errors": self.errors,
            "dry_run": self.dry_run,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
        }


@dataclass
class DaemonTickReport:
    """One daemon tick's accounting."""

    tick_id: str
    fetched: int
    skipped_already_processed: int
    skipped_library_dedup: int
    triaged: int
    fast_rejected: int
    errors: int
    marked_read: int
    outcomes_resolved: int
    daily_selection_ran: bool = False
    daily_materialized: int = 0
    daily_rejected: int = 0
    elapsed_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "tick_id": self.tick_id,
            "fetched": self.fetched,
            "skipped_already_processed": self.skipped_already_processed,
            "skipped_library_dedup": self.skipped_library_dedup,
            "triaged": self.triaged,
            "fast_rejected": self.fast_rejected,
            "errors": self.errors,
            "marked_read": self.marked_read,
            "outcomes_resolved": self.outcomes_resolved,
            "daily_selection_ran": self.daily_selection_ran,
            "daily_materialized": self.daily_materialized,
            "daily_rejected": self.daily_rejected,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
        }


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _generate_zotero_key(seen: set[str]) -> str:
    for _ in range(64):
        candidate = "".join(random.choice(_ZOTERO_KEY_ALPHABET) for _ in range(8))
        if candidate not in seen:
            seen.add(candidate)
            return candidate
    raise RuntimeError("Failed to generate unique Zotero key after 64 attempts")


def _since_iso(default_days: int, override_days: int | None) -> str:
    days = override_days if override_days is not None else default_days
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, int(days)))
    return cutoff.strftime("%Y-%m-%d %H:%M:%S")


def _infer_item_type(feed_item: dict[str, Any]) -> str:
    url = (feed_item.get("url") or "").lower()
    if "arxiv.org" in url:
        return "preprint"
    if "biorxiv" in url or "medrxiv" in url or "chemrxiv" in url:
        return "preprint"
    feed_name = (feed_item.get("feed_name") or "").lower()
    if "arxiv" in feed_name or "preprint" in feed_name:
        return "preprint"
    return "journalArticle"


def _safe_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return dict(value)
    return {}


def _dim_value(summary: SummarizeResponse, name: str) -> float:
    dims = summary.triage_dimensions
    if dims is None:
        return 0.0
    if hasattr(dims, name):
        try:
            return float(getattr(dims, name) or 0)
        except (TypeError, ValueError):
            return 0.0
    if isinstance(dims, dict):
        try:
            return float(dims.get(name) or 0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


_FATAL_LLM_ERROR_SIGNALS = (
    "401",
    "403",
    "invalid_api_key",
    "incorrect api key",
    "authentication",
    "permission",
    "quota",
    "insufficient_quota",
    "rate_limit_exceeded",
    "connection refused",
    "connection error",
    "cannot connect",
)


def _is_fatal_llm_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(signal in msg for signal in _FATAL_LLM_ERROR_SIGNALS)


@contextmanager
def _triage_conn() -> Iterator[sqlite3.Connection]:
    settings = get_settings()
    path = settings.triage_db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass
        feeds_storage.init_feeds_schema(conn)
        yield conn
    finally:
        conn.close()


def _load_config() -> dict[str, Any]:
    config = get_state().app_state.config
    raw = getattr(config, "raw", {}) or {}
    feeds_cfg = _safe_dict(getattr(config, "feeds", None)) or _safe_dict(raw.get("feeds"))
    selection_cfg = _safe_dict(getattr(config, "selection", None)) or _safe_dict(raw.get("selection"))
    surprise_cfg = _safe_dict(getattr(config, "surprise", None)) or _safe_dict(raw.get("surprise"))
    return {
        "feeds": feeds_cfg,
        "selection": selection_cfg,
        "surprise": surprise_cfg,
    }


# ---------------------------------------------------------------------------
# Triage primitive — shared between Phase 1 batch + Phase 1.5 daemon tick
# ---------------------------------------------------------------------------


def _triage_one(
    item: dict[str, Any],
    *,
    log_prefix: str,
) -> tuple[TriagedCandidate | None, str | None, bool]:
    """Triage one feed item. Returns (candidate, error_msg, is_fatal).

    `is_fatal` is True for endpoint/auth errors that will recur on every
    subsequent call (401, connection error, etc.) — caller should abort.
    """
    try:
        req = SummarizeRequest(
            title=item.get("title") or "Untitled",
            doi=(item.get("doi") or "").strip() or None,
            abstract=item.get("abstract") or "",
            pdf_path="",
        )
        summary = run_abstract_pipeline(req, log_prefix=log_prefix)
        surprise = surprise_service.compute_surprise_score(
            methodological_rigor=_dim_value(summary, "methodological_rigor"),
            novelty_for_goals=_dim_value(summary, "novelty_for_goals"),
            corpus_affinity=float(summary.corpus_affinity_score),
        )
        cand = TriagedCandidate(
            feed_item=item,
            summary=summary,
            composite_score=float(summary.composite_relevance_score),
            surprise_score=surprise,
        )
        return cand, None, False
    except Exception as exc:
        fatal = _is_fatal_llm_error(exc)
        return None, str(exc), fatal


# ---------------------------------------------------------------------------
# Daemon tick — triage K unread items + mark read + opportunistic outcomes
# ---------------------------------------------------------------------------


def _pick_unread_batch_round_robin(
    reader: ZoteroReader,
    *,
    batch_size: int,
    feed_library_ids: list[int] | None,
) -> list[dict[str, Any]]:
    """Pick up to `batch_size` unread items, round-robin across feeds.

    Round-robin prevents one prolific feed (e.g. bioRxiv: 405 items) from
    starving smaller feeds. We pull ceil(batch_size/feed_count) per feed
    and trim — for batch_size=5 and 48 feeds this fetches 1 per feed.
    """
    if not feed_library_ids:
        feed_groups = reader.get_feed_groups()
        feed_library_ids = [int(f["library_id"]) for f in feed_groups]
    if not feed_library_ids:
        return []

    # Probe each feed for unread items; tile round-robin until we hit batch_size.
    per_feed_pool: dict[int, list[dict[str, Any]]] = {}
    for lib_id in feed_library_ids:
        try:
            items = reader.get_feed_items(
                feed_library_id=int(lib_id),
                unread_only=True,
                order="oldest_first",
                limit=batch_size * 2,  # small headroom for dedup losses
            )
        except Exception as exc:
            LOGGER.warning("get_feed_items failed for feed_library_id=%s: %s", lib_id, exc)
            items = []
        per_feed_pool[int(lib_id)] = items

    # Round-robin pull until we've collected batch_size or every pool is empty.
    selected: list[dict[str, Any]] = []
    feed_order = list(feed_library_ids)
    random.shuffle(feed_order)  # avoid feed_id ordering bias across ticks
    cursor = 0
    while len(selected) < batch_size:
        progressed_this_round = False
        for _ in range(len(feed_order)):
            lib_id = feed_order[cursor % len(feed_order)]
            cursor += 1
            pool = per_feed_pool.get(lib_id, [])
            if pool:
                selected.append(pool.pop(0))
                progressed_this_round = True
                if len(selected) >= batch_size:
                    break
        if not progressed_this_round:
            break
    return selected


def run_daemon_tick(
    *,
    reader: ZoteroReader | None = None,
    writer: ZoteroWriter | None = None,
    feed_library_ids: list[int] | None = None,
    batch_size: int | None = None,
    force_daily_selection: bool = False,
) -> DaemonTickReport:
    """One daemon iteration: triage K unread items + opportunistic side-work.

    Each tick:
      1. Pick K unread items round-robin across feeds (default K=5).
      2. Dedup against `processed_feed_items` (resumability) + library DOI.
      3. Triage each (corpus fast-reject -> LLM if not pre-rejected).
      4. Insert as `triaged_pending` (or `rejected_low_score` / etc).
      5. Mark all processed items read in Zotero (feedItems.readTime).
      6. Resolve up to `outcome_check_per_tick` due outcomes -> user_feedback.
      7. If 24h has elapsed since last daily-selection, run it now.

    Returns the tick report (for logging / CLI display).
    """
    start_ts = time.perf_counter()
    tick_id = feeds_storage.new_run_id(prefix="tick")
    config = _load_config()
    feeds_cfg = config["feeds"]

    reader = reader or ZoteroReader(get_settings().zotero_data_dir)
    writer = writer or ZoteroWriter(get_settings().zotero_data_dir)

    effective_batch = int(batch_size or feeds_cfg.get("daemon_batch_size") or 5)
    dedup_against_library = bool(feeds_cfg.get("dedup_against_library", True))
    mark_processed_as_read = bool(feeds_cfg.get("mark_processed_as_read", True))
    outcome_check_per_tick = int(feeds_cfg.get("outcome_check_per_tick") or 3)

    LOGGER.info("[%s] tick start batch=%d", tick_id, effective_batch)

    # 1. Pick K unread items round-robin.
    raw = _pick_unread_batch_round_robin(
        reader,
        batch_size=effective_batch,
        feed_library_ids=feed_library_ids,
    )
    fetched = len(raw)

    # 2. Dedup against processed_feed_items + library.
    with _triage_conn() as conn:
        unprocessed, skipped_processed = feeds_storage.filter_unprocessed(conn, raw)

    library_skipped: list[dict[str, Any]] = []
    to_triage: list[dict[str, Any]] = []
    if dedup_against_library:
        for item in unprocessed:
            doi = (item.get("doi") or "").strip()
            arxiv = (item.get("arxiv_id") or "").strip()
            if not doi and not arxiv:
                to_triage.append(item)
                continue
            try:
                existing = reader.find_by_external_id(doi=doi or None, arxiv_id=arxiv or None)
            except Exception:
                existing = None
            if existing:
                library_skipped.append(item)
            else:
                to_triage.append(item)
    else:
        to_triage = list(unprocessed)

    # 3. Triage.
    triaged_results: list[tuple[dict[str, Any], TriagedCandidate]] = []
    fast_rejected_results: list[tuple[dict[str, Any], TriagedCandidate]] = []
    errors_results: list[tuple[dict[str, Any], str]] = []
    fatal_seen = False
    for idx, item in enumerate(to_triage, start=1):
        if fatal_seen:
            errors_results.append((item, "skipped_after_fatal_llm_error"))
            continue
        cand, err, fatal = _triage_one(item, log_prefix=f"daemon:{tick_id}:{idx}")
        if cand is None:
            errors_results.append((item, err or "unknown_error"))
            if fatal:
                fatal_seen = True
                LOGGER.error("[%s] FATAL LLM error — aborting tick: %s", tick_id, err)
            continue
        # Detect corpus fast-reject by tag presence (set by summarization fast path).
        is_fast_reject = any(
            "prefilter_low_corpus_affinity" in (t or "").lower() for t in (cand.summary.tags or [])
        )
        if is_fast_reject:
            fast_rejected_results.append((item, cand))
        else:
            triaged_results.append((item, cand))

    # 4. Record decisions.
    with _triage_conn() as conn:
        for item, cand in triaged_results:
            feeds_storage.record_decision(
                conn,
                run_id=tick_id,
                feed_item=item,
                decision=feeds_storage.DECISION_TRIAGED_PENDING,
                decision_reason="pending_daily_selection",
                composite_score=cand.composite_score,
                surprise_score=cand.surprise_score,
                corpus_affinity=float(cand.summary.corpus_affinity_score),
                reading_priority=cand.summary.reading_priority,
                matched_collections=list(cand.summary.suggested_collections or []),
            )
        for item, cand in fast_rejected_results:
            feeds_storage.record_decision(
                conn,
                run_id=tick_id,
                feed_item=item,
                decision=feeds_storage.DECISION_REJECTED_LOW_SCORE,
                decision_reason="corpus_fast_reject",
                composite_score=cand.composite_score,
                surprise_score=cand.surprise_score,
                corpus_affinity=float(cand.summary.corpus_affinity_score),
                reading_priority=cand.summary.reading_priority,
            )
        for item in library_skipped:
            feeds_storage.record_decision(
                conn,
                run_id=tick_id,
                feed_item=item,
                decision=feeds_storage.DECISION_REJECTED_DEDUP_LIBRARY,
                decision_reason="already_in_library",
            )
        for item, err_msg in errors_results:
            feeds_storage.record_decision(
                conn,
                run_id=tick_id,
                feed_item=item,
                decision=feeds_storage.DECISION_SKIPPED_ERROR,
                decision_reason="triage_exception",
                error=err_msg,
            )
        conn.commit()

    # 5. Mark all processed items read in Zotero (skipping fatal-error rows).
    marked = 0
    if mark_processed_as_read:
        processed_ids: list[int] = []
        for item, _cand in triaged_results + fast_rejected_results:
            processed_ids.append(int(item.get("item_id") or 0))
        for item in library_skipped:
            processed_ids.append(int(item.get("item_id") or 0))
        # Don't mark items as read if the LLM never saw them (fatal-error
        # items deserve another chance on the next tick).
        processed_ids = [i for i in processed_ids if i > 0]
        if processed_ids:
            try:
                marked = writer.mark_feed_items_read(processed_ids)
                with _triage_conn() as conn:
                    for item, _ in triaged_results + fast_rejected_results:
                        feeds_storage.record_read_marked(
                            conn,
                            feed_library_id=int(item.get("feed_library_id") or 0),
                            feed_item_id=int(item.get("item_id") or 0),
                        )
                    for item in library_skipped:
                        feeds_storage.record_read_marked(
                            conn,
                            feed_library_id=int(item.get("feed_library_id") or 0),
                            feed_item_id=int(item.get("item_id") or 0),
                        )
                    conn.commit()
            except Exception as exc:
                LOGGER.warning("[%s] mark_feed_items_read failed: %s", tick_id, exc)

    # 6. Resolve up to N due outcomes.
    outcomes = 0
    if outcome_check_per_tick > 0:
        try:
            outcomes = _resolve_due_outcomes(
                reader=reader,
                limit=outcome_check_per_tick,
            )
        except Exception:
            LOGGER.exception("[%s] outcome resolution failed", tick_id)

    # 7. Daily selection trigger.
    daily_ran = False
    daily_materialized = 0
    daily_rejected = 0
    if force_daily_selection or _should_run_daily_selection(feeds_cfg):
        try:
            sel = run_daily_selection(reader=reader, writer=writer)
            daily_ran = True
            daily_materialized = sel.get("materialized", 0)
            daily_rejected = sel.get("rejected", 0)
        except Exception:
            LOGGER.exception("[%s] daily selection failed", tick_id)

    elapsed = time.perf_counter() - start_ts
    report = DaemonTickReport(
        tick_id=tick_id,
        fetched=fetched,
        skipped_already_processed=skipped_processed,
        skipped_library_dedup=len(library_skipped),
        triaged=len(triaged_results),
        fast_rejected=len(fast_rejected_results),
        errors=len(errors_results),
        marked_read=marked,
        outcomes_resolved=outcomes,
        daily_selection_ran=daily_ran,
        daily_materialized=daily_materialized,
        daily_rejected=daily_rejected,
        elapsed_seconds=elapsed,
    )
    LOGGER.info(
        "[%s] tick done in %.2fs fetched=%d triaged=%d fast=%d err=%d marked=%d outcomes=%d daily=%s",
        tick_id,
        elapsed,
        fetched,
        len(triaged_results),
        len(fast_rejected_results),
        len(errors_results),
        marked,
        outcomes,
        daily_ran,
    )
    return report


# ---------------------------------------------------------------------------
# Daily selection — plateau-select from rolling 24h of triaged_pending rows
# ---------------------------------------------------------------------------


def _should_run_daily_selection(feeds_cfg: dict[str, Any]) -> bool:
    """Return True if it's been >= daily_selection_interval_hours since last run."""
    interval_raw = feeds_cfg.get("daily_selection_interval_hours")
    interval_h = int(interval_raw if interval_raw is not None else 24)
    with _triage_conn() as conn:
        row = conn.execute(
            "SELECT MAX(updated_at) AS ts FROM processed_feed_items WHERE decision IN (?, ?, ?)",
            (
                feeds_storage.DECISION_SELECTED,
                feeds_storage.DECISION_BLACK_SWAN,
                feeds_storage.DECISION_REJECTED_DAILY_CUTOFF,
            ),
        ).fetchone()
    last_ts = row["ts"] if row is not None else None
    if not last_ts:
        return True
    try:
        last_dt = datetime.strptime(str(last_ts), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    # interval_h <= 0 means "always run" (useful for tests and the manual
    # `feeds select-daily` CLI override).
    if interval_h <= 0:
        return True
    return datetime.now(timezone.utc) - last_dt >= timedelta(hours=interval_h)


@dataclass
class _PendingScoredRow:
    """Lightweight scored row used by plateau_select (compatible interface)."""

    composite_score: float
    surprise_score: float
    is_black_swan: bool
    row: dict[str, Any]
    key: str


def run_daily_selection(
    *,
    reader: ZoteroReader | None = None,
    writer: ZoteroWriter | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Plateau-select 1-2 best from rolling 24h of `triaged_pending` rows.

    Reads `processed_feed_items` WHERE decision='triaged_pending'
    AND created_at >= now - daily_window_hours, plateau-selects with
    hard_min=daily_target_min (default 1) and hard_max=daily_target_max
    (default 2), allocates 0-1 black-swan from the rejected pool, and
    materializes selected items directly into Zotero (Inbox + matched
    collections + tags + v3 note). All other rows flip to
    `rejected_daily_cutoff`.

    Returns a summary dict {materialized, rejected, black_swans, errors}.
    """
    config = _load_config()
    feeds_cfg = config["feeds"]
    selection_cfg = config["selection"]
    surprise_cfg = config["surprise"]

    daily_min = int(feeds_cfg.get("daily_target_min") or 1)
    daily_max = int(feeds_cfg.get("daily_target_max") or 2)
    daily_window_h = int(feeds_cfg.get("daily_window_hours") or 24)
    kneedle_S = float(selection_cfg.get("kneedle_sensitivity") or 1.0)
    inbox_collection_name = str(feeds_cfg.get("inbox_collection_name") or "Inbox")
    outcome_window_days = int(feeds_cfg.get("outcome_window_days") or 7)
    bs_min_score = float(surprise_cfg.get("min_score") or 0.30)
    black_swan_tag = str(surprise_cfg.get("black_swan_tag") or _DEFAULT_BLACK_SWAN_TAG)
    daily_force_black_swan = bool(feeds_cfg.get("daily_force_black_swan_every_run", False))

    reader = reader or ZoteroReader(get_settings().zotero_data_dir)
    writer = writer or ZoteroWriter(get_settings().zotero_data_dir)
    run_id = feeds_storage.new_run_id(prefix="daily")

    # 1. Gather candidates.
    with _triage_conn() as conn:
        candidate_rows = feeds_storage.select_pending_triaged(
            conn,
            since_hours=daily_window_h,
            limit=1000,
        )

    if not candidate_rows:
        LOGGER.info("[%s] no triaged_pending rows in last %dh — skipping daily selection", run_id, daily_window_h)
        return {"run_id": run_id, "materialized": 0, "rejected": 0, "black_swans": 0, "errors": []}

    scored = [
        _PendingScoredRow(
            composite_score=float(r.get("composite_score") or 0.0),
            surprise_score=float(r.get("surprise_score") or 0.0),
            is_black_swan=False,
            row=r,
            key=f"{int(r.get('feed_library_id') or 0)}:{int(r.get('feed_item_id') or 0)}",
        )
        for r in candidate_rows
    ]

    # 2. Plateau-select top 1-2.
    selection = select_service.plateau_select(
        scored,
        target_fraction=max(0.01, daily_max / max(1, len(scored))),
        hard_min=min(daily_min, len(scored)),
        hard_max=min(daily_max, len(scored)),
        kneedle_sensitivity=kneedle_S,
    )
    selected: list[_PendingScoredRow] = list(selection.selected)
    rejected_pool: list[_PendingScoredRow] = list(selection.rejected)

    # 3. Black-swan allocation. With daily_max=2, the 10% rule yields 0 slots;
    # `daily_force_black_swan_every_run` flips one in unconditionally if a
    # rejected candidate exceeds bs_min_score.
    bs_picks: list[_PendingScoredRow] = []
    if daily_force_black_swan:
        viable = [r for r in rejected_pool if r.surprise_score >= bs_min_score]
        if viable:
            viable.sort(key=lambda r: r.surprise_score, reverse=True)
            bs_picks = [viable[0]]
            for p in bs_picks:
                p.is_black_swan = True

    final_inbox: list[_PendingScoredRow] = list(selected) + list(bs_picks)
    LOGGER.info(
        "[%s] daily selection: %d candidates -> %d selected + %d black-swan (cutoff=%s reason=%s)",
        run_id,
        len(scored),
        len(selected),
        len(bs_picks),
        selection.cutoff,
        selection.reason,
    )

    # 4. Materialize selected items directly.
    materialized_count = 0
    errors: list[dict[str, Any]] = []
    used_keys: set[str] = set()
    if not dry_run:
        for pick in final_inbox:
            try:
                new_key = _generate_zotero_key(used_keys)
                pick.row["planned_zotero_key"] = new_key
                summary = _summary_from_row(pick.row)
                feed_payload = _feed_payload_from_row(pick.row)
                matched = _matched_collections_from_row(pick.row)
                tags = _tags_from_row(pick.row, is_black_swan=pick.is_black_swan, black_swan_tag=black_swan_tag)
                note_html = pending_service.build_triage_note_html(
                    title=str(pick.row.get("title") or ""),
                    summary=summary,
                    is_black_swan=pick.is_black_swan,
                    surprise_score=pick.surprise_score if pick.is_black_swan else None,
                    run_id=run_id,
                )
                writer.apply_feed_materialization(
                    new_item_key=new_key,
                    feed_payload=feed_payload,
                    inbox_collection_name=inbox_collection_name,
                    matched_collections=matched,
                    tags=tags,
                    note_title=f"Triage: {str(pick.row.get('title') or '')[:80]}",
                    note_html=note_html,
                    provenance_tag=pending_service.SYSTEM_TAG_FEEDS_V3,
                    create_backup=False,
                )
                # Update processed_feed_items row.
                decision = (
                    feeds_storage.DECISION_BLACK_SWAN
                    if pick.is_black_swan
                    else feeds_storage.DECISION_SELECTED
                )
                with _triage_conn() as conn:
                    feeds_storage.update_to_decision(
                        conn,
                        feed_library_id=int(pick.row.get("feed_library_id") or 0),
                        feed_item_id=int(pick.row.get("feed_item_id") or 0),
                        decision=decision,
                        decision_reason=selection.reason if not pick.is_black_swan else "surprise_pick",
                        is_black_swan=pick.is_black_swan,
                        planned_zotero_key=new_key,
                    )
                    feeds_storage.record_materialization(
                        conn,
                        feed_library_id=int(pick.row.get("feed_library_id") or 0),
                        feed_item_id=int(pick.row.get("feed_item_id") or 0),
                        materialized_zotero_key=new_key,
                        outcome_window_days=outcome_window_days,
                    )
                    conn.commit()
                materialized_count += 1
            except Exception as exc:
                LOGGER.exception("[%s] materialization failed for key %s", run_id, pick.key)
                errors.append({"key": pick.key, "error": str(exc)})

    # 5. Flip all the rest to rejected_daily_cutoff.
    rejected_count = 0
    if not dry_run:
        selected_keys = {p.key for p in final_inbox}
        with _triage_conn() as conn:
            for pick in scored:
                if pick.key in selected_keys:
                    continue
                if feeds_storage.update_to_decision(
                    conn,
                    feed_library_id=int(pick.row.get("feed_library_id") or 0),
                    feed_item_id=int(pick.row.get("feed_item_id") or 0),
                    decision=feeds_storage.DECISION_REJECTED_DAILY_CUTOFF,
                    decision_reason=selection.reason,
                ):
                    rejected_count += 1
            conn.commit()

    return {
        "run_id": run_id,
        "materialized": materialized_count,
        "rejected": rejected_count,
        "black_swans": len(bs_picks),
        "errors": errors,
        "cutoff": selection.cutoff,
        "cutoff_reason": selection.reason,
    }


def _summary_from_row(row: dict[str, Any]) -> SummarizeResponse:
    """Reconstruct a minimal SummarizeResponse from a processed_feed_items row.

    Phase 1.5 daily-selection happens hours after the triage tick that
    scored the item, so we don't have the full SummarizeResponse in memory
    any more. The row only stores the score + a few fields; we rebuild a
    sparse SummarizeResponse so the note builder has something to render.
    """
    import json as _json
    from zotero_summarizer.models import SummarizeResponse as SR

    matched = _json.loads(row.get("matched_collections_json") or "[]")
    return SR(
        title=str(row.get("title") or ""),
        doi=str(row.get("doi") or ""),
        summary="",
        relevance_score=int(round(float(row.get("composite_score") or 0))),
        composite_relevance_score=float(row.get("composite_score") or 0.0),
        reading_priority=str(row.get("reading_priority") or "could_read"),
        tags=[],
        triage_rationale="",
        triage_confidence=0.0,
        executive_summary="",
        should_deep_read="",
        key_sections_to_read=[],
        relevance_to_research="",
        controversial_points="",
        industry_academy_impact="",
        unknown_unknowns="",
        implementation_quickstart="",
        key_findings=[],
        methods="",
        limitations="",
        suggested_collections=list(matched),
        corpus_affinity_score=float(row.get("corpus_affinity") or 0.0),
        matched_goal="",
    )


def _feed_payload_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Build the create_item_from_feed payload from a stored row.

    The original Zotero feed item still exists in Zotero's `feedItems` table —
    we re-query it here for fresh metadata rather than storing the full
    abstract in `processed_feed_items` (saves storage; lets users update feed
    metadata between triage and materialization).
    """
    feed_library_id = int(row.get("feed_library_id") or 0)
    feed_item_id = int(row.get("feed_item_id") or 0)
    reader = ZoteroReader(get_settings().zotero_data_dir)
    items = reader.get_feed_items(feed_library_id=feed_library_id, limit=5000)
    match = next((i for i in items if int(i.get("item_id") or 0) == feed_item_id), None)
    if not match:
        # Item disappeared from Zotero (manually deleted from the feed?).
        # Materialize with what's in our DB instead.
        return {
            "title": str(row.get("title") or "Untitled"),
            "abstract": "",
            "url": "",
            "doi": str(row.get("doi") or ""),
            "publication_date": "",
            "publication_title": "",
            "item_type": "journalArticle",
        }
    return {
        "title": match.get("title") or row.get("title") or "Untitled",
        "abstract": match.get("abstract") or "",
        "url": match.get("url") or "",
        "doi": match.get("doi") or row.get("doi") or "",
        "publication_date": match.get("publication_date") or "",
        "publication_title": match.get("publication_title") or "",
        "authors": match.get("authors") or "",
        "item_type": _infer_item_type(match),
    }


def _matched_collections_from_row(row: dict[str, Any]) -> list[str]:
    import json as _json

    try:
        return _json.loads(row.get("matched_collections_json") or "[]")
    except Exception:
        return []


def _tags_from_row(
    row: dict[str, Any],
    *,
    is_black_swan: bool,
    black_swan_tag: str,
) -> list[str]:
    """Build the tag list for a materialized item.

    Includes the reading-priority `zs:<priority>` tag (Phase 1 convention),
    and the black-swan tag if applicable. The provenance tag `/zs/feeds-v3`
    is appended separately by `apply_feed_materialization` so it's
    distinguishable in the dispatch layer.
    """
    priority = str(row.get("reading_priority") or "could_read")
    tags = [f"zs:{priority}"]
    if is_black_swan and black_swan_tag:
        tags.append(black_swan_tag)
    return tags


# ---------------------------------------------------------------------------
# Outcome detection — flow user actions back into feedback weights
# ---------------------------------------------------------------------------


def _resolve_due_outcomes(
    *,
    reader: ZoteroReader,
    limit: int,
) -> int:
    """Resolve up to `limit` due outcomes. Returns count resolved.

    For each due row (outcome_eligible_at <= now, outcome_detected_at IS NULL,
    materialized_zotero_key NOT NULL):
      - Query Zotero for the item's collections + trash + engagement tags.
      - Compute the outcome label per the OUTCOME_* constants.
      - Write `user_feedback` row with the asymmetric weight.
      - Update `processed_feed_items` with final_outcome + signal_weight.
    """
    with _triage_conn() as conn:
        due = feeds_storage.due_outcome_checks(conn, limit=limit)
    if not due:
        return 0

    resolved = 0
    for row in due:
        item_key = str(row.get("materialized_zotero_key") or "").strip()
        if not item_key:
            continue
        try:
            membership = reader.get_item_membership(item_key)
        except Exception as exc:
            LOGGER.warning("get_item_membership failed for %s: %s", item_key, exc)
            continue
        outcome = _compute_outcome_from_membership(membership)
        weight = feeds_storage.OUTCOME_WEIGHT.get(outcome, 0.0)
        with _triage_conn() as conn:
            feeds_storage.record_outcome(
                conn,
                feed_library_id=int(row.get("feed_library_id") or 0),
                feed_item_id=int(row.get("feed_item_id") or 0),
                final_outcome=outcome,
                signal_weight=weight,
            )
            conn.commit()
        # Push to user_feedback so corpus.py's engagement weighting can pick
        # it up on the next refresh. (Done outside the feeds-storage conn
        # because insert_feedback_events uses its own connection via _get_conn.)
        try:
            triage_db.insert_feedback_events(
                [
                    {
                        "item_id": item_key,
                        "feedback_type": _feedback_type_from_outcome(outcome),
                        "signal": f"feed_outcome:{outcome}",
                        "original_priority": str(row.get("reading_priority") or ""),
                        "inferred_relevance": _relevance_from_weight(weight),
                    }
                ]
            )
        except Exception:
            LOGGER.exception("insert_feedback_events failed for %s", item_key)
        resolved += 1
    return resolved


def _compute_outcome_from_membership(membership: dict[str, Any]) -> str:
    """Reduce a ZoteroReader membership dict to one of the OUTCOME_* labels.

    Precedence (strongest signal first):
      1. has_engagement_tag (🧠/👀) -> OUTCOME_ENGAGED (+3)
      2. is_trashed                  -> OUTCOME_TRASHED (-3)
      3. !exists                     -> OUTCOME_UNKNOWN (-1, hard-delete)
      4. zero collections            -> OUTCOME_DELETED_ALL (-3)
      5. has collections, !is_in_inbox -> OUTCOME_MOVED_COLLECTION (+1)
      6. only Inbox membership       -> OUTCOME_KEPT_INBOX (-0.5)

    The engagement check wins over trash (a user who tagged 🧠 then trashed
    later still gave a strong positive signal earlier — we surface the
    positive). The corpus engagement signal handles the trash separately.
    """
    if membership.get("has_engagement_tag"):
        return feeds_storage.OUTCOME_ENGAGED
    if not membership.get("exists"):
        return feeds_storage.OUTCOME_UNKNOWN
    if membership.get("is_trashed"):
        return feeds_storage.OUTCOME_TRASHED
    collection_keys = membership.get("collection_keys") or []
    if not collection_keys:
        return feeds_storage.OUTCOME_DELETED_ALL
    if membership.get("is_in_inbox") and len(collection_keys) == 1:
        return feeds_storage.OUTCOME_KEPT_INBOX
    return feeds_storage.OUTCOME_MOVED_COLLECTION


def _feedback_type_from_outcome(outcome: str) -> str:
    """Map outcome -> existing user_feedback type vocabulary."""
    if outcome in (feeds_storage.OUTCOME_ENGAGED, feeds_storage.OUTCOME_MOVED_COLLECTION):
        return "implicit_engagement"
    if outcome in (feeds_storage.OUTCOME_DELETED_ALL, feeds_storage.OUTCOME_TRASHED, feeds_storage.OUTCOME_UNKNOWN):
        return "implicit_negative_strong"
    return "implicit_weak_negative"


def _relevance_from_weight(weight: float) -> float:
    """Map signal_weight (-3..+3) to inferred_relevance scale (1..5)."""
    # Linear: weight=-3 -> 1, weight=0 -> 3, weight=+3 -> 5
    val = 3.0 + (weight / 1.5)
    return max(1.0, min(5.0, val))


# ---------------------------------------------------------------------------
# Daemon loop — long-running asyncio service
# ---------------------------------------------------------------------------


async def run_daemon_loop(
    *,
    reader: ZoteroReader | None = None,
    writer: ZoteroWriter | None = None,
    feed_library_ids: list[int] | None = None,
    max_ticks: int | None = None,
) -> None:
    """Long-running daemon: tick every N seconds until shutdown.

    SIGINT / SIGTERM finish the current tick (in flight) and then exit
    cleanly — no half-applied state because each tick's DB writes are
    committed before sleeping.

    `max_ticks=None` runs forever; set a finite value for testing.
    """
    config = _load_config()
    feeds_cfg = config["feeds"]
    tick_seconds = int(feeds_cfg.get("daemon_tick_seconds") or 300)
    LOGGER.info("daemon starting tick_interval=%ds", tick_seconds)

    stop_event = asyncio.Event()

    def _on_signal(*_args: Any) -> None:
        LOGGER.info("daemon received shutdown signal — finishing current tick")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                loop.add_signal_handler(sig, _on_signal)
            except (NotImplementedError, RuntimeError):
                # Windows doesn't support add_signal_handler for SIGTERM.
                pass

    tick_count = 0
    while not stop_event.is_set():
        try:
            report = await asyncio.to_thread(
                run_daemon_tick,
                reader=reader,
                writer=writer,
                feed_library_ids=feed_library_ids,
            )
            LOGGER.info("tick %d: %s", tick_count + 1, report.as_dict())
        except Exception:
            LOGGER.exception("daemon tick raised; sleeping then retrying")
        tick_count += 1
        if max_ticks is not None and tick_count >= max_ticks:
            break
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=tick_seconds)
            break  # stop_event set during the wait
        except asyncio.TimeoutError:
            continue

    LOGGER.info("daemon exiting after %d ticks", tick_count)


# ---------------------------------------------------------------------------
# Phase 1 one-shot path (unchanged; kept for backwards compat / preview CLI)
# ---------------------------------------------------------------------------


def _build_pending_changes_for_selected(
    cand: TriagedCandidate,
    inbox_collection_name: str,
    black_swan_tag: str,
    run_id: str,
) -> list[PendingChange]:
    feed_item = cand.feed_item
    summary = cand.summary
    new_key = cand.planned_zotero_key
    assert new_key, "planned_zotero_key must be set before queueing"
    title = str(feed_item.get("title") or "Untitled feed item")

    create_payload: dict[str, Any] = {
        "title": title,
        "abstract": str(feed_item.get("abstract") or ""),
        "url": str(feed_item.get("url") or ""),
        "doi": str(feed_item.get("doi") or ""),
        "publication_date": str(feed_item.get("publication_date") or ""),
        "publication_title": str(feed_item.get("publication_title") or ""),
        "item_type": _infer_item_type(feed_item),
    }
    authors_raw = feed_item.get("authors")
    if authors_raw:
        create_payload["authors"] = authors_raw

    changes: list[PendingChange] = [
        PendingChange(
            item_key=new_key,
            item_title=title,
            change_type="create_item_from_feed",
            payload=create_payload,
        ),
        PendingChange(
            item_key=new_key,
            item_title=title,
            change_type="add_to_collection",
            payload={"collection_path": inbox_collection_name},
        ),
    ]

    suggested = summary.suggested_collections or []
    seen_paths = {inbox_collection_name.casefold()}
    for collection_path in suggested:
        path = str(collection_path or "").strip()
        if not path or path.casefold() in seen_paths:
            continue
        seen_paths.add(path.casefold())
        changes.append(
            PendingChange(
                item_key=new_key,
                item_title=title,
                change_type="add_to_collection",
                payload={"collection_path": path},
            )
        )

    raw_tags = list(summary.tags or [])[:3]
    if cand.is_black_swan and black_swan_tag and black_swan_tag not in raw_tags:
        raw_tags.append(black_swan_tag)
    # Phase 1.5: include the /zs/feeds-v3 provenance tag (system-managed,
    # auto-tag type via ZoteroWriter._ensure_tag's slash-prefix detection).
    if pending_service.SYSTEM_TAG_FEEDS_V3 not in raw_tags:
        raw_tags.append(pending_service.SYSTEM_TAG_FEEDS_V3)
    normalized_tags = pending_service.normalize_change_tags(raw_tags, summary.reading_priority)
    changes.append(
        PendingChange(
            item_key=new_key,
            item_title=title,
            change_type="tag_changes",
            payload={"add_tags": normalized_tags, "remove_tags": []},
        )
    )

    note_html = pending_service.build_triage_note_html(
        title=title,
        summary=summary,
        is_black_swan=cand.is_black_swan,
        surprise_score=cand.surprise_score if cand.is_black_swan else None,
        run_id=run_id,
    )
    if note_html:
        changes.append(
            PendingChange(
                item_key=new_key,
                item_title=title,
                change_type="add_note",
                payload={
                    "note_title": f"Triage: {title[:80]}",
                    "note_html": note_html,
                },
            )
        )

    return changes


def run_feed_batch(
    *,
    since_days: int | None = None,
    feed_library_ids: list[int] | None = None,
    dry_run: bool = False,
    reader: ZoteroReader | None = None,
) -> FeedRunReport:
    """Phase 1 one-shot batch — kept for `feeds run` CLI compatibility.

    Phase 1.5 daemon (`feeds serve`) is the recommended workflow now, but
    `feeds run` still works for ad-hoc bulk processing (e.g., catching up
    after a long downtime or testing).
    """
    start_ts = time.perf_counter()
    run_id = feeds_storage.new_run_id()
    app_settings = get_settings()
    config = _load_config()
    feeds_cfg = config["feeds"]
    selection_cfg = config["selection"]
    surprise_cfg = config["surprise"]

    inbox_collection_name = str(feeds_cfg.get("inbox_collection_name") or "Inbox")
    default_since_days = int(feeds_cfg.get("default_since_days") or 7)
    dedup_against_library = bool(feeds_cfg.get("dedup_against_library", True))
    target_fraction = float(selection_cfg.get("target_fraction") or 0.05)
    hard_min = int(selection_cfg.get("hard_min") or 10)
    hard_max = int(selection_cfg.get("hard_max") or 15)
    kneedle_S = float(selection_cfg.get("kneedle_sensitivity") or 1.0)
    bs_fraction = float(surprise_cfg.get("black_swan_fraction") or 0.10)
    bs_min_score = float(surprise_cfg.get("min_score") or 0.30)
    black_swan_tag = str(surprise_cfg.get("black_swan_tag") or _DEFAULT_BLACK_SWAN_TAG)

    reader = reader or ZoteroReader(app_settings.zotero_data_dir)
    since = _since_iso(default_since_days, since_days)

    raw_items: list[dict[str, Any]] = []
    if feed_library_ids:
        for lib_id in feed_library_ids:
            raw_items.extend(
                reader.get_feed_items(
                    feed_library_id=int(lib_id),
                    since=since,
                    limit=2000,
                )
            )
    else:
        raw_items = reader.get_feed_items(since=since, limit=5000)
    total_feed_items = len(raw_items)

    with _triage_conn() as conn:
        unprocessed_items, deduped_processed = feeds_storage.filter_unprocessed(conn, raw_items)

    library_skipped: list[dict[str, Any]] = []
    candidates_for_triage: list[dict[str, Any]] = []
    if dedup_against_library:
        for item in unprocessed_items:
            doi = (item.get("doi") or "").strip()
            arxiv = (item.get("arxiv_id") or "").strip()
            if not doi and not arxiv:
                candidates_for_triage.append(item)
                continue
            try:
                existing_key = reader.find_by_external_id(doi=doi or None, arxiv_id=arxiv or None)
            except Exception:
                existing_key = None
            if existing_key:
                library_skipped.append(item)
            else:
                candidates_for_triage.append(item)
    else:
        candidates_for_triage = list(unprocessed_items)

    triaged: list[TriagedCandidate] = []
    triage_errors: list[dict[str, Any]] = []
    fatal_seen = False
    for idx, item in enumerate(candidates_for_triage, start=1):
        if fatal_seen:
            triage_errors.append(
                {
                    "feed_library_id": item.get("feed_library_id"),
                    "feed_item_id": item.get("item_id"),
                    "title": item.get("title"),
                    "error": "skipped due to earlier fatal LLM error",
                }
            )
            continue
        cand, err, fatal = _triage_one(item, log_prefix=f"feeds:{run_id}:{idx}")
        if cand is None:
            triage_errors.append(
                {
                    "feed_library_id": item.get("feed_library_id"),
                    "feed_item_id": item.get("item_id"),
                    "title": item.get("title"),
                    "error": err or "unknown",
                }
            )
            if fatal:
                fatal_seen = True
            continue
        triaged.append(cand)

    selection = select_service.plateau_select(
        triaged,
        target_fraction=target_fraction,
        hard_min=hard_min,
        hard_max=hard_max,
        kneedle_sensitivity=kneedle_S,
    )
    selected = list(selection.selected)
    rejected_pool = list(selection.rejected)

    selected_keys = {c.key for c in selected}
    bs_result = surprise_service.allocate_black_swan_slots(
        inbox_size=len(selected),
        rejected_pool=rejected_pool,
        already_selected_keys=selected_keys,
        fraction=bs_fraction,
        min_score=bs_min_score,
        surprise_attr="surprise_score",
        key_attr="key",
    )
    for cand in bs_result.black_swan_selected:
        cand.is_black_swan = True
    final_inbox: list[TriagedCandidate] = list(selected) + list(bs_result.black_swan_selected)

    used_keys: set[str] = set()
    queued_count = 0
    queue_errors: list[dict[str, Any]] = []
    if not dry_run and final_inbox:
        for cand in final_inbox:
            cand.planned_zotero_key = _generate_zotero_key(used_keys)
        for cand in final_inbox:
            try:
                changes = _build_pending_changes_for_selected(
                    cand,
                    inbox_collection_name=inbox_collection_name,
                    black_swan_tag=black_swan_tag,
                    run_id=run_id,
                )
                planner = pending_service.PendingChangePlanner()
                rows = planner.to_repository_rows(changes)
                triage_db.insert_pending_changes(
                    item_key=cand.planned_zotero_key or "",
                    item_title=str(cand.feed_item.get("title") or "Untitled"),
                    changes=rows,
                )
                queued_count += len(rows)
            except Exception as exc:
                LOGGER.exception("[%s] failed to queue changes", run_id)
                queue_errors.append({"feed_item_id": cand.feed_item.get("item_id"), "error": str(exc)})

    if not dry_run:
        with _triage_conn() as conn:
            selected_key_set = {c.key for c in selected}
            black_swan_key_set = {c.key for c in bs_result.black_swan_selected}
            for cand in triaged:
                if cand.key in selected_key_set:
                    decision = feeds_storage.DECISION_SELECTED
                    reason = selection.reason
                    is_bs = False
                elif cand.key in black_swan_key_set:
                    decision = feeds_storage.DECISION_BLACK_SWAN
                    reason = "surprise_pick"
                    is_bs = True
                else:
                    decision = feeds_storage.DECISION_REJECTED_ELBOW
                    reason = selection.reason
                    is_bs = False
                feeds_storage.record_decision(
                    conn,
                    run_id=run_id,
                    feed_item=cand.feed_item,
                    decision=decision,
                    decision_reason=reason,
                    composite_score=cand.composite_score,
                    surprise_score=cand.surprise_score,
                    corpus_affinity=float(cand.summary.corpus_affinity_score),
                    reading_priority=cand.summary.reading_priority,
                    is_black_swan=is_bs,
                    planned_zotero_key=cand.planned_zotero_key,
                    matched_collections=list(cand.summary.suggested_collections or []),
                )
            for item in library_skipped:
                feeds_storage.record_decision(
                    conn,
                    run_id=run_id,
                    feed_item=item,
                    decision=feeds_storage.DECISION_REJECTED_DEDUP_LIBRARY,
                    decision_reason="already_in_library",
                )
            for err in triage_errors:
                feeds_storage.record_decision(
                    conn,
                    run_id=run_id,
                    feed_item={
                        "feed_library_id": err["feed_library_id"],
                        "item_id": err["feed_item_id"],
                        "title": err.get("title"),
                    },
                    decision=feeds_storage.DECISION_SKIPPED_ERROR,
                    decision_reason="triage_exception",
                    error=err["error"],
                )
            conn.commit()

    elapsed = time.perf_counter() - start_ts
    return FeedRunReport(
        run_id=run_id,
        total_feed_items=total_feed_items,
        deduped_against_processed=deduped_processed,
        deduped_against_library=len(library_skipped),
        triaged=len(triaged),
        selected_by_plateau=len(selected),
        black_swans=len(bs_result.black_swan_selected),
        rejected=max(0, len(triaged) - len(final_inbox)),
        cutoff=selection.cutoff,
        cutoff_reason=selection.reason,
        knee_index=selection.knee_index,
        safety_cap=selection.safety_cap,
        queued_change_count=queued_count,
        errors=triage_errors + queue_errors,
        dry_run=dry_run,
        elapsed_seconds=elapsed,
    )


def list_feed_groups(reader: ZoteroReader | None = None) -> list[dict[str, Any]]:
    """Convenience pass-through for the CLI `feeds list` subcommand."""
    app_settings = get_settings()
    reader = reader or ZoteroReader(app_settings.zotero_data_dir)
    return reader.get_feed_groups()


def preview_feed(
    feed_library_id: int,
    *,
    since_days: int = 7,
    limit: int = 50,
    reader: ZoteroReader | None = None,
    unread_only: bool = False,
) -> list[dict[str, Any]]:
    """Peek at recent feed items for one feed (CLI `feeds preview`)."""
    app_settings = get_settings()
    reader = reader or ZoteroReader(app_settings.zotero_data_dir)
    since = _since_iso(since_days, None)
    return reader.get_feed_items(
        feed_library_id=feed_library_id,
        since=since,
        limit=limit,
        unread_only=unread_only,
    )
