"""One caller-serialized async page per channel; no client lifecycle or activation."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timedelta, timezone

from zotero_summarizer.models.research_feed import (
    ResolvedTelegramChannel, TelegramTransportError, normalize_telegram_username,
)
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import migrations, repositories
from zotero_summarizer.storage import research_feed_telegram as store


def _channel_receipts(usernames: list[str]) -> list[dict]:
    if type(usernames) is not list:
        raise ValueError("Telegram usernames must be a list")
    channels = {}
    for supplied in usernames:
        key = normalize_telegram_username(supplied)
        if key not in channels:
            channels[key] = {"requested_username": supplied, "username": key,
                             "channel_id": None, "status": "not_attempted"}
    return list(channels.values())


def _utc_now(now: Callable[[], datetime]) -> datetime:
    value = now()
    try:
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("naive or invalid clock value")
        return value.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("Telegram collector clock requires an aware UTC-convertible datetime") from None


def _admit_request(conn, now: Callable[[], datetime], account: dict) -> bool:
    if conn.in_transaction:
        raise RuntimeError("Telegram request cannot run inside a write transaction")
    retry_at = store.read_retry_at(conn)
    current = _utc_now(now)
    deferred = retry_at is not None and datetime.fromisoformat(retry_at) > current
    account.update(status="deferred" if deferred else "not_deferred", retry_at=retry_at)
    return not deferred


def _validated_channel(value: ResolvedTelegramChannel) -> ResolvedTelegramChannel:
    if not isinstance(value, ResolvedTelegramChannel):
        raise ValueError("invalid resolved Telegram channel")
    try:
        # Instances can be mutated/bypassed; validate their current fields, not trust identity.
        return ResolvedTelegramChannel.model_validate(value.model_dump(mode="python", warnings=False))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("invalid resolved Telegram channel") from None


def _record_error(conn, error: TelegramTransportError, channel: dict, account: dict,
                  now: Callable[[], datetime]) -> bool:
    # Reuse the constructor to reject mutated error fields without exposing upstream args.
    checked = TelegramTransportError(error.code, retry_seconds=error.retry_seconds)
    channel["status"] = checked.code
    if checked.code in {"network_error", "channel_unavailable"}:
        return False
    if checked.code == "flood_wait":
        deadline = _utc_now(now) + timedelta(seconds=checked.retry_seconds)
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            retry_at = store.defer_account(conn, retry_at=deadline)
        account.update(status="deferred", retry_at=retry_at)
    else:
        account["status"] = checked.code
    return True


async def collect_pages(
    settings: Settings, usernames: list[str], client, *, now: Callable[[], datetime],
) -> dict:
    """Explicit caller owns content permission, serialization and client lifecycle.

    Uses short BEGIN IMMEDIATE transactions with rollback on error, never across
    awaits. not_deferred is only absence of an observed future durable deadline.
    """
    channels = _channel_receipts(usernames)
    result = {"channels": channels, "account": {"status": "not_checked", "retry_at": None},
              "counts": dict(fetched=0, new=0, updated=0, unchanged=0, skipped=0)}
    if not channels:
        return result
    _utc_now(now)
    migrations.run_migrations(settings.triage_db_path, "triage", migrations.TRIAGE_MIGRATIONS)
    with closing(repositories._connect_to(settings.triage_db_path)) as conn:
        attempted = set()
        for channel in channels:
            if not _admit_request(conn, now, result["account"]):
                break
            try:
                resolved = await client.resolve_channel(username=channel["username"])
            except TelegramTransportError as error:
                if _record_error(conn, error, channel, result["account"], now):
                    break
                continue
            resolved = _validated_channel(resolved)
            channel["channel_id"] = resolved.channel_id
            if not resolved.public_broadcast:
                channel["status"] = "channel_unavailable"
                continue
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                matched = store.bind_channel(conn, username=channel["username"],
                                             channel_id=resolved.channel_id)
            state = store.read_channel_state(conn, username=channel["username"])
            if state is None:
                raise ValueError("Telegram binding state disappeared")
            if not matched:
                channel["status"] = "binding_mismatch"
                continue
            if resolved.channel_id in attempted:
                channel["status"] = "same_channel_already_seen"
                continue
            if not _admit_request(conn, now, result["account"]):
                break
            after = (state["confirmed_message_id"] or 0) if state["initial_window_committed"] else None
            attempted.add(resolved.channel_id)
            try:
                page = await client.fetch_page(channel_id=resolved.channel_id, after_id=after, limit=100)
            except TelegramTransportError as error:
                if _record_error(conn, error, channel, result["account"], now):
                    break
                continue
            try:
                checked = store._validated_page(page, resolved.channel_id, after or 0)
            except ValueError:
                channel["status"] = "invalid_page"
                continue
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                counts = store.commit_page(
                    conn, username=channel["username"], channel_id=resolved.channel_id,
                    expected_state=(state["initial_window_committed"], state["confirmed_message_id"]),
                    page=checked,
                )
            channel.update(status="committed", counts=counts)
            for name, count in counts.items():
                result["counts"][name] += count
    return result


__all__ = ["collect_pages"]
