"""feeds: daily plateau selection from the rolling 24h of triaged_pending rows.

Includes the two-stage full-text refine and the helpers that rebuild a
materialization payload from a stored `processed_feed_items` row hours after
the original triage tick.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.models import SummarizeRequest, SummarizeResponse
from zotero_summarizer.services.zotero import pending as pending_service
from zotero_summarizer.services.triage import select as select_service
from zotero_summarizer.services.triage.summarization import run_pipeline
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.services.triage.feeds._common import (
    LOGGER,
    _DEFAULT_BLACK_SWAN_TAG,
    _generate_zotero_key,
    _infer_item_type,
    _load_config,
    _triage_conn,
    get_settings,
    get_state,
)
from zotero_summarizer.services.triage.feeds._triage import _apply_prestige


def _should_run_daily_selection(feeds_cfg: dict[str, Any]) -> bool:
    """Return True when daily selection should fire.

    Two modes:
    - ``daily_selection_at: "HH:MM"`` (preferred) — fires once per calendar day
      after that local clock time, regardless of when the daemon started.
    - ``daily_selection_interval_hours`` (legacy fallback) — fires when >= N hours
      have elapsed since the last selection run.  ``0`` means "always run".
    """
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

    target_time_str = feeds_cfg.get("daily_selection_at")
    if target_time_str:
        # Time-of-day mode: fire once per calendar day after the target local time.
        try:
            target_h, target_m = (int(x) for x in str(target_time_str).split(":"))
        except (ValueError, AttributeError):
            target_h, target_m = 8, 0
        now_local = datetime.now()  # local wall clock
        today_target = now_local.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
        if now_local < today_target:
            return False  # too early today
        if not last_ts:
            return True
        try:
            last_dt_utc = datetime.strptime(str(last_ts), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            last_dt_local = last_dt_utc.astimezone().replace(tzinfo=None)
        except ValueError:
            return True
        # Already ran today at or after the target window.
        return last_dt_local < today_target

    # Legacy interval mode.
    interval_raw = feeds_cfg.get("daily_selection_interval_hours")
    interval_h = int(interval_raw if interval_raw is not None else 24)
    # interval_h <= 0 means "always run" (useful for tests and `feeds select-daily`).
    if interval_h <= 0:
        return True
    if not last_ts:
        return True
    try:
        last_dt = datetime.strptime(str(last_ts), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
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
    # Optional full-text-refined summary (Part 1.8 two-stage triage). When set,
    # the materialization loop uses this in place of `_summary_from_row(...)`.
    refined_summary: SummarizeResponse | None = None


def _refine_with_full_text(
    final_inbox: list[_PendingScoredRow],
    *,
    run_id: str,
) -> None:
    """Two-stage triage: fetch PDF + re-score top plateau picks with full text.

    No-op when full_text_refine is disabled or no PDF source is resolvable. On
    success, mutates ``pick.composite_score`` and stashes the refined
    :class:`SummarizeResponse` on ``pick.refined_summary``.

    Any failure (no DOI/arXiv, no OA PDF, fetch timeout, %PDF magic fail,
    re-triage error) is swallowed — the pick keeps its abstract-derived score.
    """
    from zotero_summarizer.integrations.pdf_fetch import fetch_pdf, resolve_pdf_url

    app_state = get_state()
    cfg_root = getattr(app_state.app_state, "config", None) if hasattr(app_state, "app_state") else None
    ftr = getattr(cfg_root, "full_text_refine", None) if cfg_root is not None else None
    if ftr is None or not ftr.enabled:
        return
    unpaywall = getattr(app_state, "unpaywall_client", None)
    if not final_inbox:
        return
    top_k = max(1, int(ftr.top_k))
    for pick in final_inbox[:top_k]:
        row = pick.row
        title = str(row.get("title") or "")
        doi = (row.get("doi") or "").strip() or None
        arxiv_id = (row.get("arxiv_id") or "").strip() or None
        item_url = (row.get("url") or "").strip() or None
        pdf_url = resolve_pdf_url(
            doi=doi,
            arxiv_id=arxiv_id,
            url=item_url,
            unpaywall=unpaywall,
        )
        if not pdf_url:
            LOGGER.info("[%s] no OA PDF for %r — keeping abstract-derived score", run_id, title[:60])
            continue
        pdf_path = fetch_pdf(
            pdf_url,
            max_bytes=int(ftr.max_pdf_bytes),
            timeout=float(ftr.fetch_timeout_secs),
        )
        if pdf_path is None:
            LOGGER.info("[%s] PDF fetch failed for %r (url=%s)", run_id, title[:60], pdf_url)
            continue
        try:
            req = SummarizeRequest(
                title=title or "Untitled",
                doi=doi,
                abstract=str(row.get("abstract") or ""),
                pdf_path=str(pdf_path),
            )
            old_score = pick.composite_score
            new_summary = run_pipeline(req, log_prefix=run_id)
            _apply_prestige(new_summary, row, log_prefix=run_id)
            pick.refined_summary = new_summary
            pick.composite_score = float(new_summary.composite_relevance_score)
            row["composite_score"] = pick.composite_score
            LOGGER.info(
                "[%s] full-text refine: %r  composite %.2f -> %.2f",
                run_id, title[:60], old_score, pick.composite_score,
            )
        except Exception as exc:
            LOGGER.warning(
                "[%s] full-text refine error for %r: %s — keeping abstract score",
                run_id, title[:60], exc,
            )


def run_daily_selection(
    *,
    reader: ZoteroReader | None = None,
    writer: ZoteroWriter | None = None,
    dry_run: bool = False,
    feed_library_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Plateau-select 1-2 best from rolling 24h of `triaged_pending` rows.

    Reads `processed_feed_items` WHERE decision='triaged_pending'
    AND created_at >= now - daily_window_hours, plateau-selects with
    hard_min=daily_target_min (default 1) and hard_max=daily_target_max
    (default 2), allocates 0-1 black-swan from the rejected pool, and
    materializes selected items directly into Zotero (Inbox + matched
    collections + tags + v3 note). All other rows flip to
    `rejected_daily_cutoff`.

    When ``feed_library_ids`` is provided, the candidate pool is restricted
    to those feeds — used by ``feeds run --feeds <name>`` so selection stays
    scoped to the feed(s) the user asked to process.

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

    # 1. Gather candidates (optionally scoped to specific feeds).
    with _triage_conn() as conn:
        candidate_rows = feeds_storage.select_pending_triaged(
            conn,
            since_hours=daily_window_h,
            limit=1000,
            feed_library_ids=feed_library_ids,
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

    # 3.5. Two-stage refine: fetch PDF + re-score top picks with full text.
    _refine_with_full_text(final_inbox, run_id=run_id)
    # Re-sort in case full-text scoring changed the ranking.
    final_inbox.sort(key=lambda p: p.composite_score, reverse=True)

    # 4. Materialize selected items directly.
    materialized_count = 0
    errors: list[dict[str, Any]] = []
    used_keys: set[str] = set()
    if not dry_run:
        for pick in final_inbox:
            LOGGER.info(
                "[%s] → inbox: %r  composite=%.2f%s",
                run_id, str(pick.row.get("title") or "")[:60], pick.composite_score,
                "  [black-swan]" if pick.is_black_swan else "",
            )
            try:
                new_key = _generate_zotero_key(used_keys)
                pick.row["planned_zotero_key"] = new_key
                summary = pick.refined_summary or _summary_from_row(pick.row)
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
                LOGGER.info(
                    "[%s] materialized: %r  key=%s",
                    run_id, str(pick.row.get("title") or "")[:60], new_key,
                )
                materialized_count += 1
            except Exception as exc:
                _exc_str = str(exc)
                if "triaged_pending" in _exc_str or "database is locked" in _exc_str.lower():
                    LOGGER.warning(
                        "[%s] materialization deferred for key %s (DB locked — item queued for next selection run): %s",
                        run_id, pick.key, exc,
                    )
                else:
                    LOGGER.exception("[%s] materialization failed for key %s", run_id, pick.key)
                errors.append({"key": pick.key, "error": _exc_str})

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
                    LOGGER.debug(
                        "[%s] ✗ rejected: %r  composite=%.2f  reason=%s",
                        run_id, str(pick.row.get("title") or "")[:60],
                        pick.composite_score, selection.reason,
                    )
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
