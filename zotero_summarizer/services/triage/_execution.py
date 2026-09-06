"""Cooperative stops and ownership of blocking job work."""
from __future__ import annotations

import asyncio
from contextvars import copy_context
from threading import Event

from zotero_summarizer.services._common import LOGGER


class JobStopped(Exception):
    """A cooperative stop, distinct from cancellation of the owning asyncio task."""


def check_cancelled(stop: Event | None) -> None:
    if stop is not None and stop.is_set():
        raise JobStopped("Triage work stopped")


async def run_blocking(func, *args, stop: Event | None = None, timeout: float | None = None, **kwargs):
    """Keep ownership until the thread exits, even on deadline/task cancellation.

    A stop prevents later pipeline stages, not an already-entered native/HTTP call.
    """
    context = copy_context()

    def call():
        check_cancelled(stop)
        return func(*args, **kwargs)

    future = asyncio.get_running_loop().run_in_executor(None, context.run, call)
    try:
        return await asyncio.wait_for(asyncio.shield(future), timeout)
    except (asyncio.CancelledError, TimeoutError):
        if stop is not None:
            stop.set()
        # ponytail: drain the thread; use a subprocess for forcibly bounded shutdown.
        while not future.done():
            try:
                await asyncio.wait({future})
            except asyncio.CancelledError:
                continue  # repeated cancellation is deferred to the re-raise below
        error = future.exception()
        if error is not None and not isinstance(error, JobStopped):
            LOGGER.error("Stopped triage work failed while draining", exc_info=(type(error), error, error.__traceback__))
        raise
