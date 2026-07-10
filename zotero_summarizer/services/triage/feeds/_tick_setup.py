"""feeds: resolve the per-tick reader/writer/zotero-reader adapters.

Split out as a sibling of ``_tick_dedup``/``_zotero_readsync`` — ``_tick`` and
``_tick_phases`` are both already at their LOC ceiling, so this one setup
phase gets its own module rather than growing either.
"""
from __future__ import annotations

from zotero_summarizer.integrations.app_rss import AppRssReader
from zotero_summarizer.integrations.zotero_read import ZoteroReader, ZoteroReadError
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.services.triage.feeds._common import LOGGER, get_settings


def resolve_tick_adapters(
    reader: ZoteroReader | None,
    writer: ZoteroWriter | None,
    *,
    tick_id: str,
) -> tuple[ZoteroReader, ZoteroWriter | None, ZoteroReader | None]:
    """Resolve the reader/writer/zotero_reader adapters for one tick.

    The Zotero READ side (library dedup, outcome membership, read-sync) is a
    separate optional adapter from the triage source ``reader`` — the app-RSS
    source reader cannot answer Zotero-library questions (the 2026-07 outage).
    """
    reader = reader or AppRssReader(get_settings().triage_db_path)
    if writer is None:
        try:
            writer = ZoteroWriter(get_settings().zotero_data_dir)
        except Exception as exc:  # noqa: BLE001 — Zotero is now an optional adapter
            writer = None
            LOGGER.info("[%s] Zotero writer unavailable; app RSS read-state only: %s", tick_id, exc)
    zotero_reader: ZoteroReader | None = None
    try:
        zotero_reader = ZoteroReader(get_settings().zotero_data_dir)
    except ZoteroReadError as exc:  # Zotero absent — optional adapter, degrade loudly
        LOGGER.info("[%s] Zotero reader unavailable; library dedup/outcomes/read-sync skipped: %s", tick_id, exc)
    return reader, writer, zotero_reader


__all__ = ["resolve_tick_adapters"]
