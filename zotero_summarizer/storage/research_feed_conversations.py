"""Append-only conversation snapshots. Publisher bundles retain historical reviews."""
from __future__ import annotations

import json

from zotero_summarizer.models.telegram_discussion import TelegramConversation


def apply_schema(conn) -> None:
    conn.execute("""CREATE TABLE research_feed_conversations (
        snapshot_hash TEXT PRIMARY KEY, target_key TEXT NOT NULL,
        observed_at TEXT NOT NULL, payload_json TEXT NOT NULL)""")
    for action in ("UPDATE", "DELETE"):
        conn.execute(f"""CREATE TRIGGER telegram_snapshot_no_{action.lower()}
            BEFORE {action} ON research_feed_conversations
            BEGIN SELECT RAISE(ABORT, 'Telegram snapshots are immutable'); END""")


def save_snapshot(conn, snapshot: TelegramConversation) -> None:
    # Content-addressed immutable observations: same content is reusable only as
    # raw evidence, never as a claim of new collection or a cached review.
    conn.execute("""INSERT OR IGNORE INTO research_feed_conversations
        (snapshot_hash, target_key, observed_at, payload_json) VALUES (?, ?, ?, ?)""",
        (snapshot.content_hash, snapshot.target.key, snapshot.observed_at,
         snapshot.model_dump_json()))


def latest_messages(conn, source_id: str) -> list[dict]:
    """Comments of one post (``telegram:<channel>:<message>``) merged over all its snapshots, newest wins.

    A later snapshot can be empty or cover only new comments (``min_id`` cursor), so the newest
    one alone would lose comments already shown or cited.
    """
    _, channel, message = source_id.split(":")
    rows = conn.execute("""SELECT json_extract(payload_json, '$.messages') FROM research_feed_conversations
        WHERE json_extract(payload_json, '$.origin.channel_id') = ?
              AND json_extract(payload_json, '$.origin.message_id') = ?
        ORDER BY observed_at ASC""", (int(channel), int(message))).fetchall()
    merged: dict[int, dict] = {}
    for (value,) in rows:
        merged.update({m["message_id"]: m for m in json.loads(value or "[]")})
    return list(merged.values())
