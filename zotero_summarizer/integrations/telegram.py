"""Explicit injected Telethon leaf; no session, lifecycle, retry or store ownership."""
from __future__ import annotations

from asyncio import TimeoutError as AsyncTimeoutError
from datetime import datetime, timezone
from urllib.parse import urlsplit

from telethon import helpers, utils
from telethon.errors import rpcbaseerrors, rpcerrorlist
from telethon.tl import functions, types

from zotero_summarizer.models.research_feed import (
    ResolvedTelegramChannel, TelegramPage, TelegramPost, TelegramTransportError,
    normalize_telegram_username, validate_live_telegram_channel_id,
)


TL_INT32_MAX = 2**31 - 1
TL_INT64_MIN = -(2**63)
TL_INT64_MAX = 2**63 - 1
PAGE_LIMIT = 100


def _integer(value, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid Telegram integer")
    return value


def _flag(value) -> bool | None:
    if value is not None and type(value) is not bool:
        raise ValueError("invalid Telegram flag")
    return value


def _date(value) -> str | None:
    if value is None:
        return None
    try:
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("invalid date")
        return value.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        raise ValueError("invalid Telegram date") from None


def _public_name(channel, username: str) -> bool:
    names = []
    if channel.username is not None:
        names.append(normalize_telegram_username(channel.username))
    if channel.usernames is not None:
        if type(channel.usernames) is not list:
            raise ValueError("invalid Telegram aliases")
        for alias in channel.usernames:
            if not isinstance(alias, types.Username):
                raise ValueError("invalid Telegram alias")
            name = normalize_telegram_username(alias.username)
            if _flag(alias.active) is True:
                names.append(name)
    return username in names


def _channel(result, username: str):
    if (not isinstance(result, types.contacts.ResolvedPeer)
            or type(result.chats) is not list or type(result.users) is not list):
        raise ValueError("invalid Telegram resolution")
    if isinstance(result.peer, types.PeerUser):
        _integer(result.peer.user_id, 1, TL_INT64_MAX)
        raise TelegramTransportError("channel_unavailable")
    if isinstance(result.peer, types.PeerChat):
        _integer(result.peer.chat_id, 1, TL_INT64_MAX)
        raise TelegramTransportError("channel_unavailable")
    if not isinstance(result.peer, types.PeerChannel):
        raise ValueError("invalid Telegram resolution")
    native = _integer(result.peer.channel_id, 1, TL_INT64_MAX)
    marked = validate_live_telegram_channel_id(utils.get_peer_id(result.peer))
    matches = []
    for candidate in result.chats:
        if not isinstance(candidate, (types.Channel, types.ChannelForbidden)):
            raise ValueError("invalid Telegram channel")
        candidate_id = _integer(candidate.id, 1, TL_INT64_MAX)
        if candidate_id == native:
            matches.append(candidate)
    if len(matches) != 1:
        raise ValueError("invalid Telegram channel match")
    channel = matches[0]
    if isinstance(channel, types.ChannelForbidden):
        raise TelegramTransportError("channel_unavailable")
    flags = [_flag(channel.broadcast), _flag(channel.megagroup),
             _flag(channel.gigagroup), _flag(channel.min)]
    public_name = _public_name(channel, username)
    if channel.access_hash is not None:
        _integer(channel.access_hash, TL_INT64_MIN, TL_INT64_MAX)
    if (flags[0] is not True or any(flag is True for flag in flags[1:])
            or channel.access_hash is None or not public_name):
        raise TelegramTransportError("channel_unavailable")
    return marked, types.InputPeerChannel(native, channel.access_hash)


def _text(value) -> str:
    if type(value) is not str:
        raise ValueError("invalid Telegram original text")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError("invalid Telegram original Unicode") from None
    return value


def _urls(raw_text: str, entities) -> list[str]:
    if entities is None:
        return []
    if type(entities) is not list:
        raise ValueError("invalid Telegram entities")
    units = helpers.add_surrogate(raw_text)
    selected = []
    for entity in entities:
        if getattr(entity, "SUBCLASS_OF_ID", None) != types.MessageEntityUrl.SUBCLASS_OF_ID:
            raise ValueError("invalid Telegram entity")
        start = _integer(entity.offset, 0, len(units))
        length = _integer(entity.length, 1, TL_INT32_MAX)
        end = start + length
        if (end > len(units) or (start < len(units) and 0xDC00 <= ord(units[start]) <= 0xDFFF)
                or (end < len(units) and 0xDC00 <= ord(units[end]) <= 0xDFFF)):
            raise ValueError("invalid Telegram entity span")
        if isinstance(entity, (types.MessageEntityUrl, types.MessageEntityTextUrl)):
            selected.append(entity)
    try:
        visible = utils.get_inner_text(raw_text, selected)
    except UnicodeError:
        raise ValueError("invalid Telegram entity Unicode") from None
    return [_text(entity.url if isinstance(entity, types.MessageEntityTextUrl) else text)
            for entity, text in zip(selected, visible)]


def _compatible_url(value: str) -> bool:
    # Recognition only: TelegramPost remains the final canonical validator.
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        return False


def _post(message, *, channel_id: int, username: str) -> TelegramPost | None:
    if isinstance(message, types.MessageEmpty):
        return None
    published = _date(message.date)
    if isinstance(message, types.MessageService):
        if getattr(message.action, "SUBCLASS_OF_ID", None) != types.MessageActionEmpty.SUBCLASS_OF_ID:
            raise ValueError("invalid Telegram service action")
        text, urls, edited, reason = "", [], None, "service_message"
    else:
        text = _text(message.message)
        edited = _date(message.edit_date)
        urls = _urls(text, message.entities)
        media = message.media
        if media is not None and getattr(media, "SUBCLASS_OF_ID", None) != types.MessageMediaEmpty.SUBCLASS_OF_ID:
            raise ValueError("invalid Telegram media")
        rich = message.rich_message
        if rich is not None and not isinstance(rich, types.RichMessage):
            raise ValueError("invalid Telegram rich original")
        reason = None
        if rich is not None:
            reason = "unsupported_original"
        elif not text.strip():
            reason = "media_only" if media is not None else "empty_message"
    if published is None or not all(_compatible_url(url) for url in urls):
        return None
    return TelegramPost(schema_version=1, channel_id=channel_id, message_id=message.id,
                        username=username, permalink=f"https://t.me/{username}/{message.id}",
                        text=text, published_at=published, updated_at=edited,
                        urls=urls, skip_reason=reason)


def _page(result, *, native_channel_id: int, channel_id: int, username: str, after: int) -> TelegramPage:
    if not isinstance(result, (types.messages.Messages, types.messages.MessagesSlice,
                               types.messages.ChannelMessages)):
        raise ValueError("invalid Telegram history response")
    if type(result.messages) is not list or len(result.messages) > PAGE_LIMIT:
        raise ValueError("invalid Telegram history size")
    previous = TL_INT32_MAX + 1
    posts = []
    for message in result.messages:
        if not isinstance(message, (types.Message, types.MessageService, types.MessageEmpty)):
            raise ValueError("invalid Telegram message family")
        number = _integer(message.id, 1, TL_INT32_MAX)
        if not after < number < previous:
            raise ValueError("invalid Telegram history order")
        previous = number
        # The actual empty constructor permits no peer and no date.
        if not (message.peer_id is None and isinstance(message, types.MessageEmpty)):
            if (not isinstance(message.peer_id, types.PeerChannel)
                    or _integer(message.peer_id.channel_id, 1, TL_INT64_MAX) != native_channel_id):
                raise ValueError("invalid Telegram message peer")
        posts.append(_post(message, channel_id=channel_id, username=username))
    # Inspect every member before recognizing a known representation limitation.
    if any(post is None for post in posts):
        return TelegramPage(posts=(), complete=False)
    return TelegramPage(posts=tuple(reversed(posts)), complete=True)


class TelethonPageClient:
    """One explicit request per method; caller owns SDK/account lifecycle and consent."""

    def __init__(self, sdk_client):
        self._sdk = sdk_client
        self._pending = {}

    async def _request(self, request):
        try:
            return await self._sdk(request)
        except rpcerrorlist.SessionRevokedError:
            raise TelegramTransportError("revoked_session") from None
        except (rpcbaseerrors.UnauthorizedError, rpcerrorlist.AuthKeyDuplicatedError):
            raise TelegramTransportError("session_unauthorized") from None
        except rpcerrorlist.FloodWaitError as error:
            raise TelegramTransportError("flood_wait", retry_seconds=error.seconds) from None
        except (rpcerrorlist.UsernameInvalidError, rpcerrorlist.UsernameNotOccupiedError,
                rpcerrorlist.ChannelPrivateError, rpcerrorlist.ChannelPublicGroupNaError):
            raise TelegramTransportError("channel_unavailable") from None
        except (ConnectionError, TimeoutError, AsyncTimeoutError):
            raise TelegramTransportError("network_error") from None
        except rpcbaseerrors.RPCError:
            raise RuntimeError("unmapped Telegram RPC failure") from None

    async def resolve_channel(self, *, username) -> ResolvedTelegramChannel:
        self._pending.clear()
        if normalize_telegram_username(username) != username:
            raise ValueError("Telegram resolver requires a canonical username")
        result = await self._request(functions.contacts.ResolveUsernameRequest(username=username))
        marked, peer = _channel(result, username)
        self._pending[marked] = (peer, username)
        return ResolvedTelegramChannel(channel_id=marked, public_broadcast=True)

    async def fetch_page(self, *, channel_id, after_id, limit) -> TelegramPage:
        channel_id = validate_live_telegram_channel_id(channel_id)
        _integer(limit, PAGE_LIMIT, PAGE_LIMIT)
        if after_id is not None:
            _integer(after_id, 0, TL_INT32_MAX - 1)
        pending = self._pending.pop(channel_id, None)
        if pending is None:
            raise ValueError("Telegram fetch requires fresh resolution")
        peer, username = pending
        native_channel_id = _integer(peer.channel_id, 1, TL_INT64_MAX)
        access_hash = _integer(peer.access_hash, TL_INT64_MIN, TL_INT64_MAX)
        request = functions.messages.GetHistoryRequest(
            peer=peer, offset_id=0 if after_id is None else after_id + 1,
            offset_date=None, add_offset=0 if after_id is None else -PAGE_LIMIT,
            limit=PAGE_LIMIT, max_id=0, min_id=0, hash=0,
        )
        result = await self._request(request)
        returned_peer = request.peer
        if not isinstance(returned_peer, types.InputPeerChannel):
            raise ValueError("Telegram request route changed")
        if (_integer(returned_peer.channel_id, 1, TL_INT64_MAX) != native_channel_id
                or _integer(returned_peer.access_hash, TL_INT64_MIN, TL_INT64_MAX) != access_hash):
            raise ValueError("Telegram request route changed")
        return _page(result, native_channel_id=native_channel_id, channel_id=channel_id,
                     username=username, after=0 if after_id is None else after_id)


__all__ = ["TelethonPageClient"]
