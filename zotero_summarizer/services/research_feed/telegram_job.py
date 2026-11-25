"""Background «Проверить каналы» and «Load discussions»: one job thread per server process, one cross-process lock.

``start`` validates synchronously (consent, sources, provider), publishes the 'collecting'
state, then runs the pipeline on a thread and returns at once. A second call while the
thread lives returns the running status instead of a second run. Status/progress come from
``telegram_workflow.status``; a restart mid-run is detected there (dead pid / old heartbeat).
"""
from __future__ import annotations

from contextlib import ExitStack, closing
import threading

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.research_feed import (
    telegram_discussions, telegram_processing, telegram_today, telegram_workflow)
from zotero_summarizer.storage import repositories, research_feed_telegram as store

_guard = threading.Lock()
_thread: threading.Thread | None = None


def start(settings, *, run_collect=None, discussions=None) -> dict:
    """``discussions`` = (topic id, source ids) runs «Load discussions» instead of «Check channels»."""
    global _thread
    with _guard:
        if _thread is not None and _thread.is_alive():
            return telegram_workflow.status(settings)
        if not telegram_processing.collect_allowed(settings):
            raise APIError("telegram_consent_required", "Allow reading your saved Telegram sources first", status_code=409)
        with ExitStack() as stack:
            stack.enter_context(telegram_workflow._exclusive(settings))
            execute = (telegram_workflow.begin(settings, run_collect=run_collect) if discussions is None
                       else telegram_workflow.begin_discussions(settings, *discussions, run_collect=run_collect))
            held = stack.pop_all()  # the lock now belongs to the job thread

        def job():
            with held:
                execute()
        _thread = threading.Thread(target=job, name="telegram-collect", daemon=True)
        _thread.start()
    return telegram_workflow.status(settings)


def start_discussions(settings, topic_id: int, *, run_collect=None) -> dict:
    """«Load discussions» of one trend: its posts with stored replies (≤ ``MAX_POSTS``) through the same job and lock."""
    topics = telegram_today.read_today(settings).get("trends", {}).get("topics", [])
    topic = next((value for value in topics if value.get("id") == topic_id), None)
    if topic is None:
        raise APIError("telegram_topic_unknown", "This trend is not in the saved digest any more", status_code=404)
    with closing(repositories._connect_to(settings.triage_db_path)) as conn:
        posts = telegram_discussions.pick(store.list_posts(conn, limit=telegram_discussions.WINDOW_LIMIT),
                                          topic.get("source_ids") or [])
    if not posts:
        raise APIError("telegram_no_replies", "No post in this trend had replies when last checked", status_code=409)
    return start(settings, run_collect=run_collect, discussions=(topic_id, [post.source_id for post in posts]))
