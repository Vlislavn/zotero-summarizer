"""Owned-child auth/collect composition; caller must exit its dedicated process."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone

from zotero_summarizer.integrations import telegram_session
from zotero_summarizer.models.research_feed import TelegramTransportError
from zotero_summarizer.services.research_feed.profile import load_profile
from zotero_summarizer.services.research_feed import telegram_collect
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import migrations, repositories, research_feed_telegram as store


def _page_client(sdk):
    from zotero_summarizer.integrations.telegram import TelethonPageClient

    return TelethonPageClient(sdk)


def collect_once_cli(settings: Settings, *, session_factory=None, client_factory=None,
                     now=None, purpose="collect") -> dict:
    """Explicit factories run only in the dedicated public CLI child or owned tests.

    Factories run inside the integration's owned loop, after metadata/retry admission.
    The native fuse and lease remain live AFTER return. Exit this process; never run
    later ML/LLM work or embed this function in a server.
    """
    if purpose not in {"collect", "auth"}:
        raise ValueError("invalid Telegram process purpose")
    profile = None if purpose == "auth" else load_profile(settings.data_dir, persist_default=False)
    usernames = [] if profile is None else profile.telegram_channels
    channels = telegram_collect._channel_receipts(usernames)
    account = {"status": "not_checked", "retry_at": None}
    if not channels and purpose == "collect":
        return {"status": "no_channels", "cleanup": "not_started", "collection": None,
                "account": account}
    clock = now if now is not None else lambda: datetime.now(timezone.utc)
    if session_factory is not None and client_factory is not None:
        telegram_collect._utc_now(clock)

    def admit():
        migrations.run_migrations(settings.triage_db_path, "triage", migrations.TRIAGE_MIGRATIONS)
        with closing(repositories._connect_to(settings.triage_db_path)) as conn:
            return telegram_collect._admit_request(conn, clock, account)

    async def collect(sdk):
        if purpose == "auth":
            from zotero_summarizer.integrations.telegram_auth import authenticate

            return await authenticate(sdk)
        collected = await telegram_collect.collect_pages(settings, usernames, _page_client(sdk), now=clock)
        with closing(repositories._connect_to(settings.triage_db_path)) as conn:
            for channel in collected["channels"]:
                channel["cursor"] = store.read_channel_state(conn, username=channel["username"])
        return collected

    def on_error(error: TelegramTransportError):
        failure_at = telegram_collect._utc_now(clock)
        with closing(repositories._connect_to(settings.triage_db_path)) as conn:
            telegram_collect._record_error(conn, error, {}, account, lambda: failure_at)

    result = telegram_session.collect_once_cli(
        settings, collect, session_factory=session_factory, client_factory=client_factory,
        admit=admit, on_error=on_error, purpose=purpose,
    )
    result["account"] = (result["collection"]["account"]
                         if result["collection"] is not None and purpose == "collect" else account)
    return result


__all__ = ["collect_once_cli"]
