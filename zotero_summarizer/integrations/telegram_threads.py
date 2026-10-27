"""Bounded read-only conversation leaf; reuse the Telethon request/error boundary."""
from __future__ import annotations

from datetime import datetime, timezone

from telethon import utils
from telethon.tl import functions, types

from zotero_summarizer.integrations.telegram import (
    TelethonPageClient, _channel, _public_name, _post, _integer, TL_INT32_MAX, TL_INT64_MAX,
)
from zotero_summarizer.models.research_feed import TelegramTransportError, validate_live_telegram_channel_id
from zotero_summarizer.models.telegram_discussion import (
    TelegramConversation, TelegramMessage, TelegramTarget, TelegramLimits,
    TelegramPostWindow, TelegramCollectionStopped,
)


def _group(channel):
    if (not isinstance(channel, types.Channel) or channel.megagroup is not True
            or channel.broadcast is True or channel.min is True or channel.access_hash is None):
        raise TelegramTransportError("channel_unavailable")
    native = _integer(channel.id, 1, TL_INT64_MAX)
    access = _integer(channel.access_hash, -(2**63), 2**63 - 1)
    return types.InputPeerChannel(native, access)


def _messages(result, peer, limit):
    if not isinstance(result, (types.messages.Messages, types.messages.MessagesSlice, types.messages.ChannelMessages)):
        raise ValueError("invalid Telegram conversation response")
    if type(result.messages) is not list or len(result.messages) > limit:
        raise ValueError("invalid Telegram conversation page size")
    previous = TL_INT32_MAX + 1
    for message in result.messages:
        if not isinstance(message, (types.Message, types.MessageService)):
            raise ValueError("unavailable Telegram message representation")
        number = _integer(message.id, 1, TL_INT32_MAX)
        if number >= previous or not isinstance(message.peer_id, types.PeerChannel) or message.peer_id.channel_id != peer.channel_id:
            raise ValueError("invalid Telegram conversation peer/order")
        previous = number
    return result.messages


def _original(message, peer, username, root=None):
    marked = validate_live_telegram_channel_id(utils.get_peer_id(peer))
    original = _post(message, channel_id=marked, username=username or "unavailable")
    if original is None:
        raise ValueError("unrepresentable Telegram original")
    header = getattr(message, "reply_to", None)
    reply = getattr(header, "reply_to_msg_id", None)
    if header is not None and not isinstance(header, types.MessageReplyHeader):
        raise ValueError("invalid Telegram reply header")
    if root is not None and message.id != root:
        top = getattr(header, "reply_to_top_id", None) or reply
        reply_peer = getattr(header, "reply_to_peer_id", None)
        if top != root or (reply_peer is not None and (
                not isinstance(reply_peer, types.PeerChannel) or reply_peer.channel_id != peer.channel_id)):
            raise ValueError("Telegram reply outside selected thread")
    reply_peer = getattr(header, "reply_to_peer_id", None)
    current_peer = reply_peer is None or (isinstance(reply_peer, types.PeerChannel) and reply_peer.channel_id == peer.channel_id)
    return TelegramMessage(peer_id=marked, message_id=message.id, thread_id=root,
        reply_to_id=reply, reply_to_top_id=getattr(header, "reply_to_top_id", None),
        reply_peer_is_current=current_peer,
        text=original.text, published_at=original.published_at,
        updated_at=original.updated_at, urls=original.urls, skip_reason=original.skip_reason,
        permalink=f"https://t.me/{username}/{message.id}" if username else None)


def _origin_matches(forward, peer, number):
    if not isinstance(forward, types.MessageFwdHeader):
        return False
    pairs = ((forward.from_id, forward.channel_post),
             (forward.saved_from_peer, forward.saved_from_msg_id))
    return any(isinstance(source, types.PeerChannel) and source.channel_id == peer.channel_id
               and type(message_id) is int and message_id == number for source, message_id in pairs)


def _stop_reason(error):
    if isinstance(error, TelegramTransportError):
        return error.code
    return "deferred" if isinstance(error, PermissionError) else "invalid_source_response"


class TelethonConversationClient(TelethonPageClient):
    """No joins, sends, convenience iterators, SDK state persistence or lifecycle."""

    def __init__(self, sdk_client, *, before_request=None):
        super().__init__(sdk_client)
        self._before_request = before_request

    async def _routed(self, request):
        if self._before_request is not None and not self._before_request():
            raise PermissionError("telegram_retry_deferred")
        before = bytes(request)
        result = await self._request(request)
        if bytes(request) != before:
            raise ValueError("Telegram conversation request route changed")
        return result

    async def _resolve(self, target):
        result = await self._routed(functions.contacts.ResolveUsernameRequest(username=target.username))
        if target.kind == "broadcast":
            _marked, peer = _channel(result, target.username)
            return peer, target.username
        if not isinstance(result, types.contacts.ResolvedPeer) or not isinstance(result.peer, types.PeerChannel):
            raise TelegramTransportError("channel_unavailable")
        matches = [chat for chat in result.chats if isinstance(chat, types.Channel) and chat.id == result.peer.channel_id]
        if len(matches) != 1 or not _public_name(matches[0], target.username):
            raise TelegramTransportError("channel_unavailable")
        if target.kind == "topic" and matches[0].forum is not True:
            raise TelegramTransportError("channel_unavailable")
        return _group(matches[0]), target.username

    async def _history(self, peer, limit):
        request = functions.messages.GetHistoryRequest(peer=peer, offset_id=0, offset_date=None,
            add_offset=0, limit=limit, max_id=0, min_id=0, hash=0)
        result = await self._routed(request)
        return _messages(result, peer, limit)

    async def _one(self, peer, number):
        result = await self._routed(functions.channels.GetMessagesRequest(
            channel=types.InputChannel(peer.channel_id, peer.access_hash), id=[types.InputMessageID(number)]))
        values = _messages(result, peer, 1)
        if len(values) != 1 or values[0].id != number:
            raise TelegramTransportError("channel_unavailable")
        return values[0]

    async def _replies(self, target, peer, username, root, limits, *, origin=None, root_message=None):
        base = dict(target=target, origin=origin, peer_id=utils.get_peer_id(peer), root_id=root,
                    root=root_message, observed_at=datetime.now(timezone.utc).isoformat())
        try:
            result = await self._routed(functions.messages.GetRepliesRequest(
                peer=peer, msg_id=root, offset_id=0, offset_date=None, add_offset=0,
                limit=limits.replies_per_thread + 1, max_id=0, min_id=0, hash=0))
            raw = _messages(result, peer, limits.replies_per_thread + 1)
            rows = [_original(item, peer, username, root) for item in raw]
            # Some servers include the root; it is retained separately, not counted as a reply.
            rows = [row for row in rows if row.message_id != root]
            truncated = len(rows) > limits.replies_per_thread
            total = getattr(result, "count", None)
            if total is not None:
                _integer(total, 0, TL_INT32_MAX)
                truncated = truncated or total > len(rows)
            return TelegramConversation(**base, messages=list(reversed(rows[:limits.replies_per_thread])),
                scanned_count=len(raw), skipped_count=sum(bool(row.skip_reason) for row in rows),
                reported_reply_count=total,
                status="truncated" if truncated else "fetched" if rows else "empty")
        except (TelegramTransportError, ValueError, PermissionError) as error:
            observation = TelegramConversation(**base, status="unavailable", reason=_stop_reason(error), attempted=True)
            if isinstance(error, TelegramTransportError) and error.code in {"channel_unavailable", "network_error"}:
                return observation
            raise TelegramCollectionStopped(error, [observation]) from None

    async def _linked(self, target, peer, post, limits):
        base = dict(target=target, origin=post, observed_at=datetime.now(timezone.utc).isoformat())
        try:
            result = await self._routed(functions.messages.GetDiscussionMessageRequest(peer=peer, msg_id=post.message_id))
            if not isinstance(result, types.messages.DiscussionMessage):
                raise ValueError("invalid Telegram discussion resolution")
            roots = []
            for item in result.messages:
                forward = getattr(item, "fwd_from", None)
                if (isinstance(item, types.Message) and isinstance(item.peer_id, types.PeerChannel)
                        and item.peer_id.channel_id != peer.channel_id
                        and _origin_matches(forward, peer, post.message_id)):
                    roots.append(item)
            if not roots and not result.messages:
                return TelegramConversation(**base, status="empty", reason="no_linked_discussion")
            if len(roots) != 1:
                raise ValueError("Telegram discussion root is not bound to selected post")
            root = roots[0]
            matches = [chat for chat in result.chats if isinstance(chat, types.Channel) and chat.id == root.peer_id.channel_id]
            if len(matches) != 1:
                raise ValueError("Telegram discussion peer missing")
            discussion_peer = _group(matches[0])
            name = matches[0].username
            return await self._replies(target, discussion_peer, name, root.id, limits,
                origin=post, root_message=_original(root, discussion_peer, name, root.id))
        except TelegramTransportError as error:
            if error.code not in {"channel_unavailable", "network_error"}:
                raise
            return TelegramConversation(**base, status="unavailable", reason=error.code)

    async def collect(self, target: TelegramTarget, limits: TelegramLimits) -> list[TelegramConversation]:
        peer, username = await self._resolve(target)
        if target.kind == "topic":
            root = await self._one(peer, target.message_id)
            return [await self._replies(target, peer, username, target.message_id, limits,
                root_message=_original(root, peer, username, target.message_id))]
        if target.kind == "group":
            raw = await self._history(peer, limits.replies_per_thread + 1)
            values = [_original(item, peer, username) for item in raw]
            return [TelegramConversation(target=target, peer_id=utils.get_peer_id(peer),
                messages=list(reversed(values[:limits.replies_per_thread])),
                status="truncated" if len(values) > limits.replies_per_thread else "fetched" if values else "empty",
                scanned_count=len(raw), skipped_count=sum(bool(row.skip_reason) for row in values),
                observed_at=datetime.now(timezone.utc).isoformat())]
        return await self._broadcast(target, peer, username, limits)

    async def _broadcast(self, target, peer, username, limits):
        limit = 1 if target.message_id else limits.posts_per_channel
        raw = ([await self._one(peer, target.message_id)] if target.message_id else
               await self._history(peer, limit + 1))
        posts = [_post(item, channel_id=utils.get_peer_id(peer), username=username)
                 for item in reversed(raw[:limit])]
        if any(post is None for post in posts):
            raise ValueError("unrepresentable Telegram broadcast post")
        window = TelegramPostWindow(limit=limit, scanned=len(raw), retained=len(posts),
                                    lookahead=len(raw) - len(posts), truncated=len(raw) > limit)
        if not posts:
            return [TelegramConversation(target=target, status="empty", reason="no_posts_in_window",
                post_window=window, attempted=True, observed_at=datetime.now(timezone.utc).isoformat())]
        batches = []
        for index, post in enumerate(posts):
            try:
                value = await self._linked(target, peer, post, limits)
                batches.append(value.model_copy(update={"post_window": window, "attempted": True}))
            except (TelegramTransportError, ValueError, PermissionError) as error:
                if isinstance(error, TelegramCollectionStopped):
                    batches.extend(value.model_copy(update={"post_window": window}) for value in error.observations)
                    error = error.failure
                else:
                    batches.append(TelegramConversation(target=target, origin=post, status="unavailable",
                        reason=_stop_reason(error), attempted=True, post_window=window,
                        observed_at=datetime.now(timezone.utc).isoformat()))
                for remaining in posts[index + 1:]:
                    batches.append(TelegramConversation(target=target, origin=remaining, status="unavailable",
                        reason="not_attempted_after_" + _stop_reason(error), attempted=False, post_window=window,
                        observed_at=datetime.now(timezone.utc).isoformat()))
                raise TelegramCollectionStopped(error, batches) from None
        return batches
