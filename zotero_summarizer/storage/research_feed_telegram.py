"""Saved news in the selected triage DB; transactions belong to the caller."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from zotero_summarizer.models.research_feed import TelegramPost


def apply_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE research_feed_telegram_channels (
        channel_id INTEGER PRIMARY KEY CHECK(channel_id != 0),
        confirmed_message_id INTEGER CHECK(confirmed_message_id > 0)
    )""")
    conn.execute("""CREATE TABLE research_feed_telegram_posts (
        channel_id INTEGER NOT NULL REFERENCES research_feed_telegram_channels(channel_id),
        message_id INTEGER NOT NULL CHECK(message_id > 0),
        published_at TEXT NOT NULL,
        post_json TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        evaluation_json TEXT,
        PRIMARY KEY(channel_id, message_id)
    )""")


def upsert_post(conn: sqlite3.Connection, post: TelegramPost) -> str:
    payload = json.dumps(post.model_dump(mode="json"), ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"), allow_nan=False)
    content_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    previous = conn.execute(
        "SELECT content_hash FROM research_feed_telegram_posts "
        "WHERE channel_id=? AND message_id=?", (post.channel_id, post.message_id),
    ).fetchone()
    published = datetime.fromisoformat(post.published_at.replace("Z", "+00:00"))
    conn.execute("INSERT INTO research_feed_telegram_channels(channel_id) VALUES (?) "
                 "ON CONFLICT(channel_id) DO NOTHING", (post.channel_id,))
    conn.execute("""INSERT INTO research_feed_telegram_posts
        (channel_id, message_id, published_at, post_json, content_hash)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(channel_id, message_id) DO UPDATE SET
            published_at=excluded.published_at, post_json=excluded.post_json,
            evaluation_json=CASE WHEN content_hash=excluded.content_hash
                                 THEN evaluation_json ELSE NULL END,
            content_hash=excluded.content_hash""",
        (post.channel_id, post.message_id, published.astimezone(timezone.utc).isoformat(),
         payload, content_hash))
    if previous is None:
        return "new"
    return "unchanged" if previous[0] == content_hash else "updated"


def _nonfinite(value):
    raise ValueError(f"nonfinite stored evaluation value: {value}")


def list_posts(conn: sqlite3.Connection, *, limit: int) -> list[dict]:
    rows = conn.execute(
        "SELECT post_json, content_hash, evaluation_json FROM research_feed_telegram_posts "
        "ORDER BY published_at DESC, channel_id DESC, message_id DESC LIMIT ?", (limit,),
    ).fetchall()
    result = []
    for row in reversed(rows):
        evaluation = (json.loads(row["evaluation_json"], parse_constant=_nonfinite)
                      if row["evaluation_json"] else None)
        if evaluation is not None and not isinstance(evaluation, dict):
            raise ValueError("stored Telegram evaluation must be an object")
        result.append({"post": TelegramPost.model_validate_json(row["post_json"]),
                       "content_hash": row["content_hash"], "evaluation": evaluation})
    return result


def save_evaluation(conn: sqlite3.Connection, row: dict, evaluation: dict) -> bool:
    post = row["post"]
    payload = json.dumps(evaluation, ensure_ascii=False, sort_keys=True, allow_nan=False)
    changed = conn.execute(
        "UPDATE research_feed_telegram_posts SET evaluation_json=? "
        "WHERE channel_id=? AND message_id=? AND content_hash=?",
        (payload, post.channel_id, post.message_id, row["content_hash"]),
    )
    return changed.rowcount == 1


__all__ = ["apply_schema", "upsert_post", "list_posts", "save_evaluation"]
