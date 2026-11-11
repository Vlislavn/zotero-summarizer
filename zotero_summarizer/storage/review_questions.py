"""Durable, immutable review-question submissions."""

from collections.abc import Mapping
import sqlite3
from uuid import UUID

from zotero_summarizer.models.paper_keys import validate_paper_item_key
from zotero_summarizer.storage.review_notes import resolve_item_key_aliases

_ROW_FIELDS = (
    "submission_id",
    "item_key",
    "question",
    "origin",
    "preset_id",
    "submitted_at",
)
_PAYLOAD_FIELDS = _ROW_FIELDS[:-1]


def _required_text(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value


def _payload_values(payload: Mapping[str, object]) -> tuple[str, str, str, str, str | None]:
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a mapping")
    raw_id = _required_text(payload, "submission_id")
    try:
        submission_id = str(UUID(raw_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("submission_id must be a UUID") from exc

    item_key = validate_paper_item_key(payload.get("item_key"))
    question = _required_text(payload, "question")
    if len(question) > 2000:
        raise ValueError("question must not exceed 2000 code points")
    origin = _required_text(payload, "origin")
    if origin not in {"typed", "preset"}:
        raise ValueError("origin must be 'typed' or 'preset'")
    raw_preset_id = payload.get("preset_id")
    if raw_preset_id is None:
        preset_id = None
    elif isinstance(raw_preset_id, str) and raw_preset_id.strip():
        preset_id = raw_preset_id
    else:
        raise ValueError("preset_id must be non-empty text or None")
    if origin == "preset" and preset_id is None:
        raise ValueError("preset_id is required for preset questions")
    return submission_id, item_key, question, origin, preset_id


def _row_dict(row: sqlite3.Row | tuple[object, ...]) -> dict[str, object]:
    return dict(zip(_ROW_FIELDS, row))


def _question_by_id(conn: sqlite3.Connection, submission_id: str) -> dict[str, object] | None:
    columns = ", ".join(_ROW_FIELDS)
    row = conn.execute(
        f"SELECT {columns} FROM review_questions WHERE submission_id = ?", (submission_id,)
    ).fetchone()
    return _row_dict(row) if row is not None else None


def save(
    conn: sqlite3.Connection, payload: Mapping[str, object]
) -> tuple[dict[str, object], bool]:
    """Insert one submission or return its exact prior row; caller owns commit."""
    values = _payload_values(payload)
    cursor = conn.execute(
        """INSERT INTO review_questions
           (submission_id, item_key, question, origin, preset_id)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(submission_id) DO NOTHING""",
        values,
    )
    created = cursor.rowcount == 1
    row = _question_by_id(conn, values[0])
    if row is None:
        raise RuntimeError("review question insert did not produce a row")
    if not created and tuple(row[field] for field in _PAYLOAD_FIELDS) != values:
        raise ValueError("submission_id already exists with a different payload")
    if created:
        from zotero_summarizer.storage import review_note_projection

        review_note_projection.stage(conn, row["item_key"])
    return row, created


def _list_for_keys(conn: sqlite3.Connection, keys: list[str]) -> list[dict[str, object]]:
    if not keys:
        return []
    placeholders = ", ".join("?" for _ in keys)
    columns = ", ".join(_ROW_FIELDS)
    rows = conn.execute(
        f"SELECT {columns} FROM review_questions WHERE item_key IN ({placeholders}) "
        "ORDER BY submitted_at, submission_id",
        keys,
    ).fetchall()
    return [_row_dict(row) for row in rows]


def list_for_paper(conn: sqlite3.Connection, item_key: str) -> list[dict[str, object]]:
    """List questions for the resolved paper identity without rewriting keys."""
    return _list_for_keys(conn, resolve_item_key_aliases(conn, item_key))
