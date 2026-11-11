"""Persist and read the 'Сегодня в каналах' digest for the Weekly → Telegram page.

The one pipeline (``collect_pages`` + ``telegram_digest``) returns gate/summaries in
memory; this module joins them with the stored post bodies and writes a single
latest-wins JSON under the owner's data dir so a read-only API can serve posts +
discussion summaries without re-running collection, a model, or any network call.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from datetime import datetime
import logging
import time

from zotero_summarizer.services._common import now_iso_z, write_json_atomic
from zotero_summarizer.services.research_feed.telegram_collect import collect_pages
from zotero_summarizer.services.research_feed import telegram_history
from zotero_summarizer.services.research_feed.telegram_selection import output_dir as _output_dir
from zotero_summarizer.storage import repositories
from zotero_summarizer.storage import research_feed_telegram as store

LOG = logging.getLogger(__name__)
TODAY_FILENAME = "telegram-today.json"
TRENDS_CACHE = "telegram-trends.json"
WINDOW_LIMIT = 500


def _today_path(settings):
    return _output_dir(settings) / TODAY_FILENAME


def _trends_cache(settings):
    path = _output_dir(settings) / TRENDS_CACHE
    return None if path.is_symlink() else path


def _two_lines(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines[:2]) if lines else text.strip()


def _discussion(summary: dict | None) -> dict | None:
    if summary is None:
        return None
    return {"points": summary.get("points", []), "comment_count": summary.get("comment_count", 0),
            "label": summary.get("label"), "summary": summary.get("summary"),
            "top_comment_ids": summary.get("top_comment_ids", summary.get("comment_ids", [])),
            "important": summary.get("important", [])}


def _also_in(trends: dict) -> dict[str, dict]:
    """source_id -> the weekly topic the post belongs to (label, channels, trend), for the card's 'Also in' line."""
    return {source_id: {"label": topic["label"], "channels": topic["channel_count"], "trend": topic["trend"]}
            for topic in reversed(trends.get("topics", [])) for source_id in topic.get("source_ids", [])}


def _continues(row: dict | None, posts: dict) -> dict | None:
    parent = posts.get(row.get("continues")) if row else None
    if parent is None:
        return None
    return {"source_id": parent.source_id, "channel": parent.username, "permalink": parent.permalink,
            "text": _two_lines(parent.text)}


def _trends(settings, conn, now: Callable[[], datetime], *, scorer, model, gists) -> dict:
    from zotero_summarizer.services.research_feed import telegram_trends

    try:
        return telegram_trends.weekly_trends(conn, now(), scorer=scorer, model=model, gists=gists,
                                             cache_path=_trends_cache(settings))
    except Exception as error:
        LOG.warning("telegram trends failed: %s", type(error).__name__)
        return {"topics": [], "posts": {"week": 0, "baseline": 0, "channels": 0}}


def build_today(settings, result: dict, goals: list[str], now: Callable[[], datetime],
                *, scorer=None, trends_model=None) -> dict:
    """Join kept gate posts with stored bodies and discussion summaries (most discussed first)."""
    digest = result.get("digest") or {}
    kept = {row["source_id"]: row for row in digest.get("gate", []) if row.get("kept")}
    summaries = {row["source_id"]: row for row in digest.get("summaries", [])}
    posts: list[dict] = []
    with closing(repositories._connect_to(settings.triage_db_path)) as conn:
        rows = store.list_posts(conn, limit=WINDOW_LIMIT)
        gists = {source_id: row["summary"] for source_id, row in summaries.items() if row.get("summary")}
        started = time.monotonic()
        trends = _trends(settings, conn, now, scorer=scorer, model=trends_model, gists=gists)
        trends_s = round(time.monotonic() - started, 1)
    stored = {row["post"].source_id: row["post"] for row in rows}
    topics = _also_in(trends)
    for row in rows:
        post = row["post"]
        gate = kept.get(post.source_id)
        if gate is None:
            continue
        posts.append({
            "source_id": post.source_id, "channel": post.username,
            "published_at": post.published_at, "permalink": post.permalink,
            "text": _two_lines(post.text), "why_goal": gate.get("goal"),
            "discussion": _discussion(summaries.get(post.source_id)),
            "continues": _continues(summaries.get(post.source_id), stored), "also_in": topics.get(post.source_id)})
    order = {source_id: index for index, source_id in enumerate(kept)}  # gate rows arrive ranked
    posts.sort(key=lambda value: order[value["source_id"]])
    return {"schema_version": 1, "generated_at": now_iso_z(), "goals": list(goals),
            "posts": posts, "counts": {**result.get("counts", {}), "trends_s": trends_s},
            "account": result.get("account", {}), "trends": trends}


def persist_today(settings, payload: dict) -> None:
    path = _today_path(settings)
    if path.is_symlink():
        raise ValueError("telegram today output cannot be a symlink")
    previous = read_today(settings) if path.is_file() else None
    if previous and previous.get("posts"):
        telegram_history.archive(settings, previous)  # no-op when that run is already archived
    write_json_atomic(path, payload)


async def collect_and_persist(settings, usernames, post_client, *, reply_client=None, scorer=None,
                              goals=None, model=None, now=None,
                              summary_top=8, trends_model=None) -> dict:
    """Run the one pipeline with the digest seam, then persist the today payload."""
    goals = goals or []
    result = await collect_pages(
        settings, usernames, post_client, now=now, reply_client=reply_client,
        scorer=scorer, goals=goals, model=model,
        summary_top=summary_top)
    payload = build_today(settings, result, goals, now, scorer=scorer, trends_model=trends_model)
    persist_today(settings, payload)
    telegram_history.archive(settings, payload)
    return result


def thread(settings, source_id: str) -> list[dict]:
    """All stored comments of one discussion (loaded when the card is expanded, not shipped in the digest)."""
    from zotero_summarizer.services.research_feed.telegram_gist import thread_rows
    from zotero_summarizer.storage import research_feed_conversations

    with closing(repositories._connect_to(settings.triage_db_path)) as conn:
        return thread_rows(research_feed_conversations.latest_messages(conn, source_id))


def _empty(goals=None) -> dict:
    return {"schema_version": 1, "generated_at": None, "goals": goals or [], "posts": [],
            "counts": {}, "account": {}, "trends": {"topics": []}}


def read_today(settings) -> dict:
    """Read the latest persisted today digest; missing/corrupt returns an empty skeleton."""
    import json

    path = _today_path(settings)
    if path.is_symlink() or not path.is_file():
        return _empty()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(value, dict) or not isinstance(value.get("posts"), list):
        return _empty()
    for post in value["posts"]:  # payloads written before the lazy thread endpoint still carry the comments
        (post.get("discussion") or {}).pop("thread", None)
    telegram_history.overlay(settings, value["posts"])  # owner state lives on the post rows / done.json, not in this snapshot
    return value


__all__ = ["build_today", "persist_today", "collect_and_persist", "read_today", "thread"]
