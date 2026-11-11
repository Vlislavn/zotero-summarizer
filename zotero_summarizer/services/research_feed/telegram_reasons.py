"""Owner feedback on Telegram posts: 'Не подошло' chips (Todo #112) and positive 'liked' chips + note.

Stored as ``research_feed_telegram_posts.reasons_json`` (migration 16, nullable; NULL = no feedback).
Payload v2: ``{"v": 2, "rejected": bool, "chips": [negative...], "liked": [positive...], "note": str}``;
a v1 payload ``{"chips": [...], "v": 1}`` means rejected. The negative chip ids are the Today
negative set (``api/routes/_daily_verdict``; services may not import the api layer, unifying them is Todo #108).
"""
from __future__ import annotations

from contextlib import closing
import json

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services._common import now_iso_z
from zotero_summarizer.storage import repositories
from zotero_summarizer.storage import research_feed_telegram as store

NEGATIVE_CHIPS = ("not_relevant", "low_quality", "no_code", "unclear_value",
                  "too_hard", "boring", "non_credible")
POSITIVE_CHIPS = ("useful", "new_to_me", "want_to_try", "good_discussion")
NOTE_MAX = 1000
_VERSION = 2


def parse(source_id: str) -> tuple[int, int]:
    try:
        prefix, channel, message = source_id.split(":")
        if prefix != "telegram":
            raise ValueError
        return int(channel), int(message)
    except ValueError:
        raise APIError("telegram_post_unknown", "Unknown Telegram post", status_code=404) from None


def _known(values: list[str], allowed: tuple[str, ...], code: str) -> list[str]:
    unknown = [value for value in values if value not in allowed]
    if unknown:
        raise APIError(code, f"Unknown chip {unknown[0]!r}", status_code=422)
    return list(dict.fromkeys(values))


def decode(value: str | None) -> dict:
    """Stored text -> ``{rejected, chips, liked, note}`` (corrupt or empty -> no feedback)."""
    empty = {"rejected": False, "chips": [], "liked": [], "note": "", "at": ""}
    try:
        data = json.loads(value or "")
        rejected = data.get("rejected", True)
        return {"rejected": bool(rejected), "chips": [c for c in data.get("chips", []) if c in NEGATIVE_CHIPS],
                "liked": [c for c in data.get("liked", []) if c in POSITIVE_CHIPS],
                "note": str(data.get("note", ""))[:NOTE_MAX], "at": str(data.get("at", ""))}
    except (ValueError, AttributeError, TypeError):
        return empty


def _encode(state: dict) -> str | None:
    if not (state["rejected"] or state["liked"] or state["note"]):
        return None
    return json.dumps({**state, "at": now_iso_z(), "v": _VERSION}, separators=(",", ":"), ensure_ascii=False)


def _update(settings, source_id: str, change) -> dict:
    channel, message = parse(source_id)
    with closing(repositories._connect_to(settings.triage_db_path)) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")  # /feedback and /reasons arrive back-to-back: no lost update
            state = decode(store.get_reasons(conn, channel, message))
            change(state)
            if not store.set_reasons(conn, channel, message, _encode(state)):
                raise APIError("telegram_post_unknown", "Unknown Telegram post", status_code=404)
    return view(state)


def view(state: dict) -> dict:
    """What the page shows: negative chips (None = not rejected) next to liked chips and the note."""
    return {"reasons": state["chips"] if state["rejected"] else None, "liked": state["liked"], "note": state["note"]}


def save(settings, source_id: str, chips: list[str] | None) -> dict:
    """None clears the rejection; a list (possibly empty) records it, de-duplicated in order."""
    def change(state):
        state["rejected"], state["chips"] = chips is not None, _known(chips or [], NEGATIVE_CHIPS, "telegram_reason_unknown")
    return {"source_id": source_id, **_update(settings, source_id, change)}


def save_feedback(settings, source_id: str, liked: list[str], note: str) -> dict:
    """Positive chips + optional free-text note (the owner's 'what I liked')."""
    kept = _known(liked, POSITIVE_CHIPS, "telegram_reason_unknown")

    def change(state):
        state["liked"], state["note"] = kept, note.strip()[:NOTE_MAX]
    return {"source_id": source_id, **_update(settings, source_id, change)}


def read_all(settings) -> dict[str, dict]:
    with closing(repositories._connect_to(settings.triage_db_path)) as conn:
        return {source_id: view(decode(value)) for source_id, value in store.read_reasons(conn).items()}


def export(settings) -> list[dict]:
    """Every post with feedback: source, permalink, post text, rejected chips, liked chips, note, time of the last change (``at``)."""
    with closing(repositories._connect_to(settings.triage_db_path)) as conn:
        rows = store.feedback_rows(conn)
    result = []
    for source_id, post_json, value in rows:
        state, post = decode(value), json.loads(post_json)
        result.append({"source_id": source_id, "channel": post.get("username"), "permalink": post.get("permalink"),
                       "published_at": post.get("published_at"), "text": post.get("text", ""),
                       "rejected": state["rejected"], "reasons": state["chips"], "liked": state["liked"], "note": state["note"],
                       "at": state["at"]})
    return result
