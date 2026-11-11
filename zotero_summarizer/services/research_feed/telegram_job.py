"""Background «Проверить каналы»: one job thread per server process, one cross-process lock.

``start`` validates synchronously (consent, sources, provider), publishes the 'collecting'
state, then runs the pipeline on a thread and returns at once. A second call while the
thread lives returns the running status instead of a second run. Status/progress come from
``telegram_workflow.status``; a restart mid-run is detected there (dead pid / old heartbeat).
"""
from __future__ import annotations

from contextlib import ExitStack
import threading

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.research_feed import telegram_processing, telegram_workflow

_guard = threading.Lock()
_thread: threading.Thread | None = None


def start(settings, *, run_collect=None) -> dict:
    global _thread
    with _guard:
        if _thread is not None and _thread.is_alive():
            return telegram_workflow.status(settings)
        if not telegram_processing.collect_allowed(settings):
            raise APIError("telegram_consent_required", "Allow reading your saved Telegram sources first", status_code=409)
        with ExitStack() as stack:
            stack.enter_context(telegram_workflow._exclusive(settings))
            execute = telegram_workflow.begin(settings, run_collect=run_collect)
            held = stack.pop_all()  # the lock now belongs to the job thread

        def job():
            with held:
                execute()
        _thread = threading.Thread(target=job, name="telegram-collect", daemon=True)
        _thread.start()
    return telegram_workflow.status(settings)
