"""Durable question submissions, committed before optional paper Q&A."""
from collections.abc import Mapping
from contextlib import closing
import sqlite3
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services._common import settings
from zotero_summarizer.storage import review_questions as question_storage

_SUBMISSION_CONFLICT = "submission_id already exists with a different payload"


def save_question(payload: Mapping[str, object]) -> dict[str, Any]:
    """Persist or replay one immutable question using a short writer transaction."""
    try:
        with closing(sqlite3.connect(settings().triage_db_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row, _created = question_storage.save(conn, payload)
            conn.commit()
    except ValueError as exc:
        is_conflict = str(exc) == _SUBMISSION_CONFLICT
        raise APIError(
            error="submission_conflict" if is_conflict else "validation_error",
            message="submission_id already exists with a different question payload"
            if is_conflict else str(exc),
            status_code=409 if is_conflict else 422,
        ) from exc
    return row


def mirror_question(item_key: str) -> dict[str, Any]:
    """Retry the current composite note after a question transaction has closed."""
    from zotero_summarizer.services.golden import verdict_effects

    return verdict_effects.mirror_review_note(settings().triage_db_path, item_key)


def list_questions(item_key: str) -> list[dict[str, Any]]:
    """Read immutable questions for the paper and any unambiguous aliases."""
    with closing(sqlite3.connect(settings().triage_db_path)) as conn:
        return question_storage.list_for_paper(conn, item_key)
