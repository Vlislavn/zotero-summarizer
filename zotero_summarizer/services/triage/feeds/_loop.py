"""feeds: the long-running asyncio daemon loop driving `run_daemon_tick`."""
from __future__ import annotations

import asyncio
import signal
import sys
from typing import Any

from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.services.triage.feeds._common import LOGGER, _load_config
from zotero_summarizer.services.triage.feeds._tick import run_daemon_tick


async def run_daemon_loop(
    *,
    reader: ZoteroReader | None = None,
    writer: ZoteroWriter | None = None,
    feed_library_ids: list[int] | None = None,
    max_ticks: int | None = None,
) -> None:
    """Long-running daemon: tick every N seconds until shutdown.

    SIGINT / SIGTERM finish the current tick (in flight) and then exit
    cleanly — no half-applied state because each tick's DB writes are
    committed before sleeping.

    `max_ticks=None` runs forever; set a finite value for testing.
    """
    if max_ticks is not None and max_ticks < 0:
        raise ValueError("max_ticks must be nonnegative")
    if max_ticks == 0:
        return
    config = _load_config()
    feeds_cfg = config["feeds"]
    tick_seconds = int(feeds_cfg.get("daemon_tick_seconds") or 300)
    daemon_batch = int(feeds_cfg.get("daemon_batch_size") or 5)
    LOGGER.info("daemon starting tick_interval=%ds batch=%d", tick_seconds, daemon_batch)

    stop_event = asyncio.Event()

    def _on_signal(*_args: Any) -> None:
        LOGGER.info("daemon received shutdown signal — finishing current tick")
        stop_event.set()

    loop = asyncio.get_running_loop()
    registered = []
    tick_count = 0
    try:
        if sys.platform != "win32":
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, _on_signal)
                registered.append(sig)
        while not stop_event.is_set():
            report = await asyncio.to_thread(
                run_daemon_tick, reader=reader, writer=writer,
                feed_library_ids=feed_library_ids, batch_size=daemon_batch,
            )
            tick_count += 1
            LOGGER.info("tick %d: %s", tick_count, report.as_dict())
            if report.errors or report.fatal_llm_error:
                raise RuntimeError(f"Feed tick {report.tick_id} failed: errors={report.errors}, fatal_llm_error={report.fatal_llm_error}")
            if max_ticks is not None and tick_count >= max_ticks:
                break
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=tick_seconds)
            except asyncio.TimeoutError:
                continue
    finally:
        for sig in registered:
            loop.remove_signal_handler(sig)
    LOGGER.info("daemon exiting after %d ticks", tick_count)
