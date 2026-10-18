"""Saved/live news in the selected triage DB; transactions belong to the caller."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from zotero_summarizer.models.research_feed import (
    TelegramPage,
    TelegramPost,
    normalize_telegram_username,
    validate_live_telegram_channel_id,
)


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


def apply_live_schema(conn: sqlite3.Connection) -> None:
    """Invoked by the numbered migration, never by a collection call."""
    conn.execute("""ALTER TABLE research_feed_telegram_channels ADD COLUMN
        initial_window_committed INTEGER NOT NULL DEFAULT 0
        CHECK(initial_window_committed IN (0, 1))""")
    conn.execute("""CREATE TABLE research_feed_telegram_bindings (
        configured_username TEXT PRIMARY KEY,
        channel_id INTEGER NOT NULL REFERENCES research_feed_telegram_channels(channel_id)
    )""")
    conn.execute("""CREATE TABLE research_feed_telegram_account (
        slot INTEGER PRIMARY KEY CHECK(slot = 1),
        retry_at TEXT
    )""")


def _require_transaction(conn: sqlite3.Connection) -> None:
    if not conn.in_transaction:
        raise ValueError("Telegram writes require a caller-owned transaction")


def _validate_state(value: tuple[bool, int | None]) -> tuple[bool, int | None]:
    if type(value) is not tuple or len(value) != 2 or type(value[0]) is not bool:
        raise ValueError("invalid Telegram channel state")
    initial, cursor = value
    if cursor is not None:
        if type(cursor) is not int or not 1 <= cursor <= 2**63 - 1 or not initial:
            raise ValueError("invalid Telegram channel state")
    return initial, cursor


def _stored_state(channel_id: int, initial: int, cursor: int | None) -> dict:
    channel_id = validate_live_telegram_channel_id(channel_id)
    if type(initial) is not int or initial not in (0, 1):
        raise ValueError("invalid stored Telegram initialization state")
    committed, cursor = _validate_state((bool(initial), cursor))
    return {"channel_id": channel_id, "initial_window_committed": committed,
            "confirmed_message_id": cursor}


def _read_channel_state(conn: sqlite3.Connection, key: str) -> dict | None:
    row = conn.execute(
        "SELECT b.channel_id, c.initial_window_committed, c.confirmed_message_id "
        "FROM research_feed_telegram_bindings b "
        "LEFT JOIN research_feed_telegram_channels c ON c.channel_id=b.channel_id "
        "WHERE b.configured_username=?", (key,),
    ).fetchone()
    return None if row is None else _stored_state(*row)


def read_channel_state(conn: sqlite3.Connection, *, username: str) -> dict | None:
    """Read one canonical username binding; missing/corrupt parent is not unknown."""
    return _read_channel_state(conn, normalize_telegram_username(username))


def bind_channel(conn: sqlite3.Connection, *, username: str, channel_id: int) -> bool:
    """Caller BEGIN IMMEDIATE + rollback on error; commit binding before fetching.

    An active transaction is required, but in_transaction does not prove its mode.
    A mismatched binding is immutable and creates no new channel parent.
    """
    key = normalize_telegram_username(username)
    channel_id = validate_live_telegram_channel_id(channel_id)
    _require_transaction(conn)
    existing = _read_channel_state(conn, key)
    if existing is not None:
        return existing["channel_id"] == channel_id
    parent = conn.execute(
        "SELECT initial_window_committed, confirmed_message_id "
        "FROM research_feed_telegram_channels WHERE channel_id=?", (channel_id,),
    ).fetchone()
    if parent is not None:
        _stored_state(channel_id, *parent)
    conn.execute("INSERT INTO research_feed_telegram_channels(channel_id) VALUES (?) "
                 "ON CONFLICT(channel_id) DO NOTHING", (channel_id,))
    conn.execute("INSERT INTO research_feed_telegram_bindings(configured_username, channel_id) "
                 "VALUES (?, ?)", (key, channel_id))
    return True


def _validated_page(page: TelegramPage, channel_id: int, after: int) -> TelegramPage:
    if not isinstance(page, TelegramPage):
        raise ValueError("invalid Telegram page")
    try:
        # Revalidate mutable/constructor-bypassed nested posts through TelegramPost,
        # not Pydantic's trusted-instance fast path or a second canonical parser.
        checked = TelegramPage.model_validate(page.model_dump(mode="python", warnings=False))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("invalid Telegram page") from None
    if not checked.complete:
        raise ValueError("Telegram page is incomplete")
    previous = after
    for post in checked.posts:
        if post.channel_id != channel_id or post.message_id <= previous:
            raise ValueError("invalid Telegram page identity or order")
        previous = post.message_id
    return checked


def commit_page(
    conn: sqlite3.Connection, *, username: str, channel_id: int,
    expected_state: tuple[bool, int | None], page: TelegramPage,
) -> dict:
    """Caller BEGIN IMMEDIATE + rollback on ANY error makes posts/state atomic.

    Never commits, rolls back, or closes; in_transaction cannot verify IMMEDIATE.
    Counts include stored skipped observations; completeness is page-only.
    """
    key = normalize_telegram_username(username)
    channel_id = validate_live_telegram_channel_id(channel_id)
    expected_state = _validate_state(expected_state)
    checked = _validated_page(page, channel_id, expected_state[1] or 0)
    _require_transaction(conn)
    state = _read_channel_state(conn, key)
    if state is None or state["channel_id"] != channel_id:
        raise ValueError("Telegram channel binding mismatch")
    if (state["initial_window_committed"], state["confirmed_message_id"]) != expected_state:
        raise ValueError("stale Telegram channel state")
    counts = {"fetched": len(checked.posts), "new": 0, "updated": 0,
              "unchanged": 0, "skipped": 0}
    for post in checked.posts:
        counts[upsert_post(conn, post)] += 1
        counts["skipped"] += int(post.skip_reason is not None)
    cursor = checked.posts[-1].message_id if checked.posts else expected_state[1]
    conn.execute("UPDATE research_feed_telegram_channels "
                 "SET initial_window_committed=1, confirmed_message_id=? WHERE channel_id=?",
                 (cursor, channel_id))
    return counts


def _utc_deadline(value: datetime) -> str:
    try:
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("naive or non-datetime deadline")
        return value.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        raise ValueError("Telegram retry deadline requires an aware UTC-convertible datetime") from None


def read_retry_at(conn: sqlite3.Connection) -> str | None:
    """One logical account slot in the selected root; corrupt time fails closed."""
    row = conn.execute("SELECT retry_at FROM research_feed_telegram_account WHERE slot=1").fetchone()
    if row is None or row[0] is None:
        return None
    value = row[0]
    try:
        if type(value) is not str or _utc_deadline(datetime.fromisoformat(value)) != value:
            raise ValueError("noncanonical deadline")
    except (ValueError, TypeError, OverflowError):
        raise ValueError("invalid stored Telegram retry deadline") from None
    return value


def defer_account(conn: sqlite3.Connection, *, retry_at: datetime) -> str:
    """Caller BEGIN IMMEDIATE + rollback on error; preserve the later UTC deadline.

    Does not commit, sleep, clear expiry or coordinate external applications.
    """
    deadline = _utc_deadline(retry_at)
    _require_transaction(conn)
    previous = read_retry_at(conn)
    if previous is not None and datetime.fromisoformat(previous) > datetime.fromisoformat(deadline):
        deadline = previous
    conn.execute("INSERT INTO research_feed_telegram_account(slot, retry_at) VALUES (1, ?) "
                 "ON CONFLICT(slot) DO UPDATE SET retry_at=excluded.retry_at", (deadline,))
    return deadline


__all__ = [
    "apply_schema", "upsert_post", "list_posts", "save_evaluation",
    "apply_live_schema", "read_channel_state", "bind_channel", "commit_page",
    "read_retry_at", "defer_account",
]
