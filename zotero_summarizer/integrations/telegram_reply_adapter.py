"""Thin adapter: expose ``telegram_threads`` reply fetching as the digest seam.

The one-pipeline digest (``telegram_digest``) fetches comments through a minimal
``reply_client.get_replies(channel_id, post_id, limit, min_id) -> [TelegramMessage]``
seam so it stays offline-testable with a fake. This adapter satisfies that seam for
the LIVE path by reusing the existing bounded, read-only ``TelethonConversationClient``
discussion fetch (``_linked`` → ``_replies``); it adds no new Telegram capability and
is only constructed inside the owned Telegram child. It is verified with a fake client
until the owner authorizes Telegram (Todo #99); no live call happens in tests.
"""
from __future__ import annotations

from zotero_summarizer.models.research_feed import TelegramTransportError
from zotero_summarizer.models.telegram_discussion import TelegramLimits, TelegramTarget


class ThreadReplyClient:
    """Adapt the broadcast discussion leaf to the digest's ``get_replies`` seam."""

    def __init__(self, conversation_client, *, resolve_peer):
        # conversation_client: integrations.telegram_threads.TelethonConversationClient
        # resolve_peer(channel_id) -> (InputPeerChannel, username) for the broadcast.
        self._client = conversation_client
        self._resolve_peer = resolve_peer

    async def get_replies(self, *, channel_id, post_id, limit, min_id=None):
        # The bounded discussion resolver re-fetches the newest window; the per-post
        # cursor is advisory here (the thread leaf caps by `limit`, not by min_id).
        del min_id
        peer, username = await self._resolve_peer(channel_id)
        post = await self._client._one(peer, post_id)
        limits = TelegramLimits(replies_per_thread=min(max(int(limit), 1), 300))
        target = TelegramTarget(kind="broadcast", username=username or "unavailable")
        from zotero_summarizer.integrations.telegram_threads import _post_origin

        conversation = await self._client._linked(
            target, peer, _post_origin(post, peer, username), limits)
        if conversation.status == "unavailable":
            raise TelegramTransportError(conversation.reason or "channel_unavailable")
        return list(conversation.messages)


__all__ = ["ThreadReplyClient"]
