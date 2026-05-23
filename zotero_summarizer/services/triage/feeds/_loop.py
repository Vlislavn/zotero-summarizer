"""feeds: the long-running asyncio daemon loop driving `run_daemon_tick`."""
from __future__ import annotations

import asyncio
import signal
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
    config = _load_config()
    feeds_cfg = config["feeds"]
    tick_seconds = int(feeds_cfg.get("daemon_tick_seconds") or 300)
    daemon_batch = int(feeds_cfg.get("daemon_batch_size") or 5)
    LOGGER.info("daemon starting tick_interval=%ds batch=%d", tick_seconds, daemon_batch)

    stop_event = asyncio.Event()

    def _on_signal(*_args: Any) -> None:
        LOGGER.info("daemon received shutdown signal — finishing current tick")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                loop.add_signal_handler(sig, _on_signal)
            except (NotImplementedError, RuntimeError):
                # Windows doesn't support add_signal_handler for SIGTERM.
                pass

    tick_count = 0
    while not stop_event.is_set():
        try:
            report = await asyncio.to_thread(
                run_daemon_tick,
                reader=reader,
                writer=writer,
                feed_library_ids=feed_library_ids,
                batch_size=daemon_batch,
            )
            LOGGER.info("tick %d: %s", tick_count + 1, report.as_dict())
        except Exception:
            LOGGER.exception("daemon tick raised; sleeping then retrying")
        tick_count += 1
        if max_ticks is not None and tick_count >= max_ticks:
            break
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=tick_seconds)
            break  # stop_event set during the wait
        except asyncio.TimeoutError:
            continue

    LOGGER.info("daemon exiting after %d ticks", tick_count)
