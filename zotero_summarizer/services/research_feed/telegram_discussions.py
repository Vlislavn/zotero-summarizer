"""«Load discussions» of one Weekly trend: read the comments of its most replied posts on demand.

Tokens 0: no model, no scoring, no summaries. The stored ``replies_count`` picks at most ``MAX_POSTS``
posts of the trend (most replies first, replies > 0); their comments land in ``research_feed_conversations``
through the same append-only ``save_snapshot`` as «Check channels», so the existing
``GET /telegram/thread/{source_id}`` serves them. ``collect`` runs only inside the owned Telegram child,
which wires the flood-wait admission in front of EVERY request (``before_request``).
"""
from __future__ import annotations

from contextlib import closing
import logging
import sqlite3

from zotero_summarizer.models.research_feed import TelegramTransportError
from zotero_summarizer.models.telegram_discussion import TelegramCollectionStopped, TelegramTarget
from zotero_summarizer.services.research_feed import telegram_collect
from zotero_summarizer.services.research_feed import telegram_progress as progress
from zotero_summarizer.storage import repositories, research_feed_conversations as conversations
from zotero_summarizer.storage import research_feed_telegram as store

MAX_POSTS = 12  # one press reads at most this many threads: Telegram's request limit is shared with «Check channels»
WINDOW_LIMIT = 500
LOG = logging.getLogger(__name__)


def pick(rows: list[dict], source_ids) -> list:
    """The stored posts among ``source_ids`` that had replies when last checked, most replies first (≤ MAX_POSTS)."""
    wanted = set(source_ids)
    posts = [row["post"] for row in rows
             if row["post"].source_id in wanted and row["post"].replies_count > 0 and row["post"].username]
    return sorted(posts, key=lambda post: post.replies_count, reverse=True)[:MAX_POSTS]


def overlay(settings, trends: dict) -> None:
    """Add ``discussion`` {loadable, loaded[]} to every topic: what the button would read, what is stored already."""
    topics = trends.get("topics") or []
    if not topics:
        return
    try:
        with closing(repositories._connect_to(settings.triage_db_path)) as conn:
            rows = store.list_posts(conn, limit=WINDOW_LIMIT)
            counts = conversations.comment_counts(conn)
    except sqlite3.Error as error:
        LOG.warning("telegram discussions overlay failed: %s", type(error).__name__)
        return
    by_id = {row["post"].source_id: row["post"] for row in rows}
    for topic in topics:
        ids = topic.get("source_ids") or []
        loaded = sorted((by_id[i] for i in ids if i in by_id and counts.get(i)), key=lambda post: -counts[post.source_id])
        topic["discussion"] = {
            "loadable": len(pick(rows, ids)),
            "loaded": [{"source_id": post.source_id, "channel": post.username, "permalink": post.permalink,
                        "text": post.text[:300], "comments": counts[post.source_id]} for post in loaded]}


async def collect(settings, sdk, source_ids, *, admit, now) -> dict:
    """Read the comments of ``source_ids`` (re-picked from the store, same ceiling) and save them; never raises on one bad post."""
    from zotero_summarizer.integrations.telegram_reply_adapter import ThreadReplyClient
    from zotero_summarizer.integrations.telegram_threads import TelethonConversationClient
    from zotero_summarizer.services.research_feed.telegram_digest import conversation

    def before_request() -> bool:
        return admit()  # the account's flood-wait window is checked before every Telegram request, not only at start

    leaf = TelethonConversationClient(sdk, before_request=before_request)
    limit = telegram_collect.settings_limits(settings).replies_per_thread
    account = {"status": "not_checked", "retry_at": None}
    peers: dict[int, tuple] = {}
    receipt = {"posts": 0, "loaded": 0, "comments": 0, "account": account}
    with closing(repositories._connect_to(settings.triage_db_path)) as conn:
        posts = pick(store.list_posts(conn, limit=WINDOW_LIMIT), source_ids)
        names = {post.channel_id: post.username for post in posts}

        async def resolve_peer(channel_id):
            if channel_id not in peers:  # no reliance on the previous check's entity cache: this process starts cold
                peer, name = await leaf._resolve(TelegramTarget(kind="broadcast", username=names[channel_id]))
                if channel_id != -(10**12) - peer.channel_id:
                    raise TelegramTransportError("channel_unavailable")  # the name now belongs to another channel
                peers[channel_id] = (peer, name)
            return peers[channel_id]

        replies = ThreadReplyClient(leaf, resolve_peer=resolve_peer)
        receipt["posts"] = len(posts)
        for index, post in enumerate(posts):
            progress.report("comments", index + 1, len(posts))
            try:
                messages = list(await replies.get_replies(channel_id=post.channel_id, post_id=post.message_id, limit=limit))
            except (TelegramTransportError, TelegramCollectionStopped, PermissionError) as stopped:
                error = getattr(stopped, "failure", stopped)
                if isinstance(error, PermissionError):
                    break  # admit refused: the account is deferred until retry_at
                if isinstance(error, TelegramTransportError) and telegram_collect._record_error(conn, error, {}, account, now):
                    break  # flood wait (deferral stored) or a lost session: stop, keep what is saved
                continue  # this post's discussion cannot be read: skip it, not the run
            if messages:
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    conversations.save_snapshot(conn, conversation(post, messages))
                receipt["loaded"] += 1
                receipt["comments"] += len(messages)
    return receipt


__all__ = ["MAX_POSTS", "pick", "overlay", "collect"]
