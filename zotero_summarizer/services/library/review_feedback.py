"""Owner feedback on one review (chips + note) — the user-study instrument.

Keyed by paper and review version: the server snapshots ``reviewed_at``, the generation hash and
the model from the stored review, so a client cannot attach feedback to a version that does not exist.
"""
from __future__ import annotations

from contextlib import closing
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services._common import settings
from zotero_summarizer.services.library import deep_review
from zotero_summarizer.storage import repositories, review_feedback as store

CHIPS = ("methods_not_useful", "wrong_or_unsupported_fact", "flags_noise", "too_long",
         "missing_code_or_data", "wrong_read_depth", "grade_unclear", "good_as_is")
NOTE_MAX = 1000


def _known(chips: list[str]) -> list[str]:
    unknown = [chip for chip in chips if chip not in CHIPS]
    if unknown:
        raise APIError("review_feedback_chip_unknown", f"Unknown chip {unknown[0]!r}", status_code=422)
    return list(dict.fromkeys(chips))


def save(item_key: str, chips: list[str], note: str) -> dict[str, Any]:
    """Upsert the feedback for the paper's current review; no chips and no note clears it."""
    kept, text = _known(chips), note.strip()[:NOTE_MAX]
    review = deep_review.get_usable_review(item_key)
    if review is None:
        raise APIError("review_not_found", "This paper has no review to give feedback on", status_code=404)
    reviewed_at = str(review.get("reviewed_at") or "")
    with closing(repositories._connect_to(settings().triage_db_path)) as conn, conn:
        if not (kept or text):
            store.delete(conn, item_key, reviewed_at)  # idempotent: nothing stored is fine
            return {"cleared": True}
        identity, provenance = review.get("review_identity") or {}, review.get("provenance") or {}
        version = (reviewed_at, str(identity.get("generation_sha256") or ""), str(provenance.get("model") or ""))
        row = store.upsert(conn, item_key, version, kept, text)
    return {"cleared": False, "feedback": row}


def list_feedback(item_key: str) -> list[dict[str, Any]]:
    with closing(repositories._connect_to(settings().triage_db_path)) as conn:
        return store.list_for_paper(conn, item_key)


def export() -> list[dict[str, Any]]:
    """Every feedback row, all papers and review versions."""
    with closing(repositories._connect_to(settings().triage_db_path)) as conn:
        return store.all_rows(conn)
