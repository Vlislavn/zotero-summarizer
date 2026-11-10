"""Live production seam for the UI 'Collect' path: run the one pipeline in the child.

``telegram_workflow.begin`` runs :func:`collect` when no fake collector is
injected. It spawns the dedicated, session-leased Telegram child (``run_child``), which
runs ``collect_pages`` + digest + ``telegram_today`` persistence. The offline harness
injects a fake collector instead, so this module's Telethon construction is the ONLY
code not exercised by tests; it is verified with a fake client until the owner
authorizes Telegram (Todo #99). No session, keyring or model call happens in tests.
"""
from __future__ import annotations

import socket
from urllib.parse import urlparse


class ModelUnavailable(RuntimeError):
    code = "model_unavailable"


def _require_model(settings, profile, config) -> None:
    """Fail fast (not after 4 minutes of reading) when the Telegram model endpoint does not answer."""
    from zotero_summarizer.services.research_feed import telegram_processing

    url = urlparse(telegram_processing.resolve(settings, config, profile).provider.base_url or "")
    try:
        socket.create_connection((url.hostname, url.port or (443 if url.scheme == "https" else 80)), timeout=2).close()
    except (OSError, TypeError, ValueError):
        raise ModelUnavailable("Telegram model endpoint is not reachable") from None



def collect(settings, profile, config) -> None:
    """Run the owned child that collects, digests and persists the today digest.

    Exceptions (including ``TelegramChildFailed`` with a session ``code``) propagate
    to the caller, which maps session codes to ``needs_auth``.
    """
    from zotero_summarizer.integrations.telegram_child import run_child

    _require_model(settings, profile, config)
    run_child(settings, "digest")


def digest_seam(settings, sdk, profile, config, *, clock):
    """Build the live collect_pages digest seam from an authorized SDK client.

    Live-only; constructed inside the child loop after admission. The reply client
    reuses the bounded read-only ``TelethonConversationClient`` discussion fetch.
    """
    from zotero_summarizer.integrations.telegram_reply_adapter import ThreadReplyClient
    from zotero_summarizer.integrations.telegram_threads import TelethonConversationClient
    from zotero_summarizer.services.research_feed import telegram_processing, telegram_relevance

    # The app's research goals (Article selection) rank posts; the legacy profile is only a fallback.
    goals = list(config.research_goals) or [*profile.themes, *profile.projects]
    conversation = TelethonConversationClient(sdk)

    async def resolve_peer(channel_id):
        # The broadcast was just resolved by collect_pages, so it is in the entity cache.
        return await sdk.get_input_entity(channel_id), None

    # Local by default; a remote provider only with the owner's stored consent (no model -> no summary).
    model = telegram_processing.model(settings, config, profile)
    return {
        "reply_client": ThreadReplyClient(conversation, resolve_peer=resolve_peer),
        "scorer": telegram_relevance.specter2_scorer, "goals": goals, "model": model,
        "trends_model": model,
    }


__all__ = ["collect", "digest_seam"]
