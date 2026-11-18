"""Per-card Today decision + optional reason chips (Todo #37).

One tap records a must/should/could/don't label for a single card; an optional
follow-up tap attaches ≤ a few canonical reason chips. The chips live on the
verdict row (``label_verdicts.reasons_json``), never in the exposure log, and a
fresh verdict WITHOUT chips clears any prior reasons (write semantics: a new
decision owns its reasons). Undo retracts the row so the card returns to the
undecided state.

A positive pill also takes the paper-page Add path (``verdict_effects.add_feed_verdict_to_library``,
#207): one Add attempt per decision, parked when Zotero is open. Undo never deletes a Zotero item.

Split out of ``daily.py`` (at the module LOC cap) and mounted there with one
``include_router`` line. Positive priorities still pass the shipped
``require_review`` gate — the owner decided to keep it, so the UI offers
"Generate a review first" rather than a silent write to his Zotero.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services import interaction_log
from zotero_summarizer.services.golden import label_verdicts, proposal_snapshot, verdict_effects
from zotero_summarizer.services.triage import daily_actions
from zotero_summarizer.storage import feeds as feeds_storage, repositories
from zotero_summarizer.storage.feed_identity import row_feed_keys
from zotero_summarizer.storage.feeds_lookup import materialized_key_for

router = APIRouter()

_VALID_DAILY_PRIORITIES = ("must_read", "should_read", "could_read", "dont_read")

# Canonical reason-chip ids (display strings live in the frontend). Negative
# chips explain a cull; positive chips explain a keep. The owner-confirmed set
# from annotation/annotation-design.md; "conditions" is reserved for the later
# «зависит от» capture and is not populated yet.
_NEGATIVE_CHIPS = (
    "not_relevant", "low_quality", "no_code", "unclear_value",
    "too_hard", "boring", "non_credible",
)
_POSITIVE_CHIPS = ("has_takeaway", "good_figures")
_ALLOWED_CHIPS = frozenset(_NEGATIVE_CHIPS + _POSITIVE_CHIPS)
_REASONS_VERSION = 1


def _db_path():
    # Reuse the parent router's resolver so tests (and settings overrides) patch
    # one place; lazy import avoids the daily -> _daily_verdict import cycle.
    from zotero_summarizer.api.routes import daily
    return daily._db_path()


def _load_processed_row(pk: int) -> dict[str, Any] | None:
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    try:
        return feeds_storage.get_processed_feed_item_by_pk(conn, pk)
    finally:
        conn.close()


def _materialized_key(row: dict[str, Any]) -> str | None:
    """The Zotero key the paper already lives under (this row or a sibling), read from this route's DB."""
    with feeds_storage.open_triage_conn(_db_path()) as conn:
        return materialized_key_for(conn, row)


def _reasons_json(chips: list[str] | None, surface: str) -> str | None:
    """Build the forward-compatible chip payload, or ``None`` when no chip is set.

    ``None`` is meaningful: a decision with no reason. De-dupes while preserving
    tap order and rejects any id outside the canonical set (fail fast, no silent
    drop)."""
    if not chips:
        return None
    seen: list[str] = []
    for chip in chips:
        if chip not in _ALLOWED_CHIPS:
            raise APIError(
                "validation_error",
                f"unknown reason chip {chip!r}; allowed: {sorted(_ALLOWED_CHIPS)}",
                422,
            )
        if chip not in seen:
            seen.append(chip)
    return json.dumps(
        {"chips": seen, "surface": surface, "v": _REASONS_VERSION},
        ensure_ascii=False,
    )


class DailyVerdictRequest(BaseModel):
    item_id: int = Field(..., ge=1, description="processed_feed_items PK (SlatePaper.item_id).")
    user_priority: str = Field(
        ..., min_length=1,
        description="One of: must_read | should_read | could_read | dont_read.",
    )
    comment: str = Field(default="", description="Optional free-text rationale.")
    reasons: list[str] | None = Field(
        default=None,
        description=(
            "Optional canonical reason-chip ids. Omitted/empty → reasons_json NULL "
            "(a decision with no reason). Reasons are per item, never set in batch."
        ),
    )


class DailyVerdictUndoRequest(BaseModel):
    item_id: int = Field(..., ge=1, description="processed_feed_items PK whose verdict to retract.")


def _add_outcome(db_path, golden_key: str, priority: str, *, repeat: bool) -> dict[str, Any]:
    """Run the Add for a NEW positive decision; a chips-only re-submit or a negative one attempts none."""
    if priority == "dont_read":
        return {"add_attempted": False, "add_status": "not_applicable", "added_to_library": False,
                "add_error": None, "zotero_key": None}
    if repeat:
        return {"add_attempted": False, "add_status": "unchanged", "added_to_library": False,
                "add_error": None, "zotero_key": None}
    result = verdict_effects.add_feed_verdict_to_library(golden_key, priority, db_path)
    return {"add_attempted": True, "add_status": result["add_status"],
            "added_to_library": result["added_to_library"], "add_error": result["add_error"],
            "zotero_key": result.get("_zotero_key")}


async def submit_daily_verdict(body: DailyVerdictRequest) -> dict[str, Any]:
    """Write one reviewed-positive / immediate-negative Today card label (+ chips)."""
    if body.user_priority not in _VALID_DAILY_PRIORITIES:
        raise APIError(
            "validation_error",
            f"user_priority must be one of {_VALID_DAILY_PRIORITIES}; got {body.user_priority!r}",
            422,
        )
    reasons_json = _reasons_json(body.reasons, "today_card")

    row = await asyncio.to_thread(_load_processed_row, body.item_id)
    if row is None:
        raise APIError("not_found", f"no processed_feed_items row with id={body.item_id}", 404)

    if body.user_priority != "dont_read" and not await asyncio.to_thread(
        daily_actions._materialized_key_for, row
    ):
        from zotero_summarizer.services.library.review_eligibility import require_review

        await asyncio.to_thread(require_review, row)  # Owner-kept gate on Zotero writes.

    golden_key = row_feed_keys(row)[0]
    previous = await asyncio.to_thread(repositories.get_label_verdict, _db_path(), golden_key)
    repeat = previous is not None and previous["user_priority"] == body.user_priority
    from zotero_summarizer.services.library import review
    await asyncio.to_thread(
        review.append_to_golden, row, label=body.user_priority, note=body.comment,
    )
    derived = (row.get("reading_priority") or "").strip() or "unknown"
    row_id = await asyncio.to_thread(
        label_verdicts.set_label_verdict,
        _db_path(),
        item_key=golden_key,
        original_derived_priority=derived,
        user_priority=body.user_priority,
        surface="today_card",
        comment=body.comment,
        reasons_json=reasons_json,
        proposal_json=proposal_snapshot.build_proposal(surface="today_card", row=row),
    )
    if body.user_priority == "dont_read":
        await asyncio.to_thread(verdict_effects.cancel_pending_feed_add, _db_path(), golden_key)
    await asyncio.to_thread(
        daily_actions.record_row_outcome, row, feeds_storage.OUTCOME_USER_LABELED,
    )
    interaction_log.log_feed_decision(
        row=row, item_key=golden_key, surface="today_card",
        model_priority=derived, comment=body.comment,
        human={"kind": "priority", "value": body.user_priority},
    )
    # After the label is committed (a failed Add never undoes the decision), same Add as the paper page.
    add = await asyncio.to_thread(_add_outcome, _db_path(), golden_key, body.user_priority, repeat=repeat)
    return {"id": row_id, "item_key": golden_key, "reasons_saved": reasons_json is not None, **add}


async def undo_daily_verdict(body: DailyVerdictUndoRequest) -> dict[str, Any]:
    """Retract one card's verdict (+ its reasons), restoring the undecided card."""
    row = await asyncio.to_thread(_load_processed_row, body.item_id)
    if row is None:
        raise APIError("not_found", f"no processed_feed_items row with id={body.item_id}", 404)
    golden_key = row_feed_keys(row)[0]
    zotero_key = await asyncio.to_thread(_materialized_key, row)
    deleted = await asyncio.to_thread(
        label_verdicts.retract_label_verdict,
        _db_path(), item_key=golden_key, surface="today_card",
    )
    # The parked Zotero Add belongs to the withdrawn decision: leaving it would keep
    # the card dead and let a later Apply write the item without a label.
    released = await asyncio.to_thread(verdict_effects.release_pending_feed_add, _db_path(), golden_key)
    # An Add that already reached Zotero is never reverted here: only the label is withdrawn.
    return {"deleted": bool(deleted), "item_key": golden_key, "released_pending_add": released,
            "item_stays_in_zotero": bool(zotero_key), "zotero_key": zotero_key}


router.add_api_route("/api/daily/verdict", submit_daily_verdict, methods=["POST"])
router.add_api_route("/api/daily/verdict/undo", undo_daily_verdict, methods=["POST"])
