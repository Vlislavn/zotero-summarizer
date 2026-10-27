"""Append-only conversation snapshots. Publisher bundles retain historical reviews."""
from __future__ import annotations

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
