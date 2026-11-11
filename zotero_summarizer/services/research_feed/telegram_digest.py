"""Post-commit stage of the one pipeline: goal scores -> comments -> discussion-first ranking -> summaries.

Posts are ranked by what people say under them (``telegram_ranking``), goal match is a soft
boost. Comments are fetched only for the most active candidates. Injected clients/model keep
this offline-testable. Comment fetch degrades to posts-only on FloodWait and never drops a
channel; summaries (2-3 terse lines in the thread's language, citing comment ids) fall back to the top comments when no model
is routed.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from zotero_summarizer.models.research_feed import TelegramTransportError
from zotero_summarizer.models.telegram_discussion import (
    TelegramCollectionStopped, TelegramConversation, TelegramTarget)
from zotero_summarizer.services._common import now_iso_z
from zotero_summarizer.services.research_feed import telegram_gist as gist
from zotero_summarizer.services.research_feed import telegram_nli
from zotero_summarizer.services.research_feed import telegram_progress as progress
from zotero_summarizer.services.research_feed import telegram_ranking as ranking
from zotero_summarizer.services.research_feed import telegram_relevance as relevance
from zotero_summarizer.services.research_feed import telegram_series as series
from zotero_summarizer.storage import research_feed_conversations as conversations
from zotero_summarizer.storage import research_feed_telegram as store

WINDOW_LIMIT = 500
LOG = logging.getLogger(__name__)
COMMENT_CONCURRENCY = 1  # whole threads are paged: serial reads stay under Telegram's request flood limit
FETCH_FACTOR = 3  # comments are fetched for at most FETCH_FACTOR * summary_top candidates
POOL_FACTOR = 6   # only the POOL_FACTOR * summary_top most-replied posts get a goal score (embedding is the slow part)


@dataclass(frozen=True)
class DigestRequest:
    """Injected clients/model and caps for one post-commit digest pass."""

    reply_client: object
    scorer: object
    goals: list[str]
    model: object
    now: Callable[[], datetime]
    summary_top: int
    tau: float
    limits: object
    probe: object = None
    nli_dir: object = None  # local NLI gate folder (Settings.nli_model_dir); None = lines are not checked
    noul: object = None  # Jev engine for the disagreement check (integrations.jev.noul); None = no disagreement block


async def _fetch_comments(reply_client, gated, *, limits, stop):
    """Fetch replies for gated posts (serial); stop after a FloodWait."""
    sem = asyncio.Semaphore(COMMENT_CONCURRENCY)
    limit = getattr(limits, "replies_per_thread", 300)
    targets = [g for g in gated if g.post.replies_count > 0]
    started = []

    async def one(result):
        async with sem:
            progress.report("comments", len(started), len(targets))
            started.append(result)
            if stop["flood"]:
                return ("skipped", result, None)
            post = result.post
            try:
                messages = await reply_client.get_replies(
                    channel_id=post.channel_id, post_id=post.message_id,
                    limit=limit, min_id=stop["cursors"].get(post.message_id))
                return ("ok", result, list(messages))
            except (TelegramTransportError, TelegramCollectionStopped) as stopped:
                # One post whose discussion cannot be resolved skips that post, not the run.
                error = getattr(stopped, "failure", stopped)
                if isinstance(error, TelegramTransportError) and error.code == "flood_wait":
                    stop["flood"] = True
                    stop["retry_seconds"] = max(stop["retry_seconds"], error.retry_seconds or 0)
                return ("error", result, error)

    return await asyncio.gather(*(one(g) for g in targets)) if targets else []


def _conversation(result, messages):
    peer_id = messages[0].peer_id
    root_id = messages[0].thread_id
    return TelegramConversation(
        target=TelegramTarget(kind="broadcast", username=result.post.username or "unavailable"),
        origin=result.post, peer_id=peer_id, root_id=root_id, messages=messages,
        status="fetched" if messages else "empty", observed_at=now_iso_z())


async def run(conn, result, request: DigestRequest):
    """Score the committed window, fetch comments for active candidates, rank discussion-first, summarise."""
    started = time.monotonic()
    progress.report("comments")
    evaluation_rows = {(row["post"].channel_id, row["post"].message_id): row
                       for row in store.list_posts(conn, limit=WINDOW_LIMIT)}
    window = [row["post"] for row in evaluation_rows.values()]
    kept_posts, dedup_info = relevance.dedup(window)
    pool = sorted((p for p in kept_posts if p.replies_count > 0), key=lambda p: p.replies_count,
                  reverse=True)[:POOL_FACTOR * request.summary_top]
    # SPECTER2 cosine on non-Latin text is noise (0.86-0.93 for every post), so those posts use the
    # keyword probe: an honest "no goal match" beats a made-up 'why for you'.
    probe = request.probe or relevance.keyword_probe
    # tau=-1 keeps every post: the gate only supplies goal scores, ranking decides what is shown.
    # Posts nobody replied to are not scored or shown: the digest is about discussions.
    gate_results = relevance.gate(pool, request.goals, scorer=request.scorer, min_keep=0,
                                  top_n=len(pool), tau=-1.0, probe=probe)
    fit = {g.post.source_id: ranking.goal_fit(g.score, request.tau) for g in gate_results}
    candidates = sorted(gate_results, reverse=True,
                        key=lambda g: ranking.prior(g.post.replies_count, fit[g.post.source_id]))
    candidates = candidates[:FETCH_FACTOR * request.summary_top]
    cursors = {row["post"].message_id: (row["evaluation"] or {}).get("last_comment_id")
               for row in evaluation_rows.values()}
    stop = {"flood": False, "retry_seconds": 0, "cursors": cursors}
    scored = time.monotonic()
    fetched = await _fetch_comments(request.reply_client, candidates, limits=request.limits, stop=stop)
    fetch_done = time.monotonic()

    comments_by_post: dict[tuple[int, int], list] = {}
    flood_degraded = write_failures = 0
    for status, gate_result, payload in fetched:
        post = gate_result.post
        if status == "ok" and payload:
            comments_by_post[(post.channel_id, post.message_id)] = payload
            try:
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    conversations.save_snapshot(conn, _conversation(gate_result, payload))
                row = evaluation_rows.get((post.channel_id, post.message_id))
                if row is not None:
                    evaluation = dict(row["evaluation"] or {})
                    evaluation["last_comment_id"] = max(c.message_id for c in payload)
                    with conn:
                        conn.execute("BEGIN IMMEDIATE")
                        store.save_evaluation(conn, row, evaluation)
            except Exception as error:  # never drop the channel over a snapshot/cursor write
                write_failures += 1
                LOG.warning("telegram snapshot/cursor write failed for %s: %s", post.source_id, type(error).__name__)
        elif status == "error":
            flood_degraded += int(getattr(payload, "code", None) == "flood_wait")

    if stop["flood"] and stop["retry_seconds"] > 0:
        deadline = request.now() + timedelta(seconds=stop["retry_seconds"])
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                retry_at = store.defer_account(conn, retry_at=deadline)
            result["account"].update(status="deferred", retry_at=retry_at)
        except Exception as error:
            write_failures += 1
            LOG.warning("telegram account deferral write failed: %s", type(error).__name__)

    ranked = sorted(((g, ranking.score(comments_by_post.get((g.post.channel_id, g.post.message_id), []),
                                       g.post.replies_count, fit[g.post.source_id])) for g in gate_results),
                    key=lambda pair: pair[1], reverse=True)
    shown = [pair for pair in ranked if pair[1] >= ranking.MIN_SCORE][:request.summary_top]
    shown_ids = {g.post.source_id for g, _ in shown}
    entail = (lambda premise, hypothesis: telegram_nli.entail(request.nli_dir, premise, hypothesis)) if request.nli_dir else None
    summaries, fallback = gist.summaries(shown, comments_by_post, model=request.model, goals=request.goals,
                                         probe=probe, parents=series.parents(window), entail=entail,
                                         noul=request.noul)
    result["counts"].update(
        score_s=round(scored - started, 1), fetch_s=round(fetch_done - scored, 1),
        summarise_s=round(time.monotonic() - fetch_done, 1), deduped=dedup_info["collapsed"], gated=len(shown),
        comments_fetched=sum(len(v) for v in comments_by_post.values()),
        flood_degraded=flood_degraded, write_failures=write_failures, summaries=len(summaries), fallback=fallback)
    result["digest"] = {
        "gist_version": gist.GIST_VERSION, "dedup": dedup_info, "gated": len(shown),
        "gate": [{"source_id": g.post.source_id, "score": round(value, 4),
                  "kept": g.post.source_id in shown_ids,
                  "goal": g.matched_goal if fit[g.post.source_id] > 0 else None, "route": g.route}
                 for g, value in ranked],
        "comments_posts": len(comments_by_post), "flood_degraded": flood_degraded,
        "summaries": summaries}
    return result


__all__ = ["run", "DigestRequest"]
