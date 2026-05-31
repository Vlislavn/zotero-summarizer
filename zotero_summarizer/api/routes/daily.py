""""Today" tab endpoints.

- ``GET  /api/daily``                — assemble the daily slate
- ``GET  /api/daily/pipeline``       — funnel overview (in/filtered/awaiting/added/trashed)
- ``POST /api/daily/triage-backlog`` — drain the un-triaged backlog (sota model)
- ``GET  /api/daily/triage-status``  — poll the backlog drain
- ``POST /api/daily/rescore-slate``  — re-score the current slate in place (gate upgrade)
- ``POST /api/daily/add-to-library`` — materialize cards into Zotero Inbox (positive label)
- ``POST /api/daily/trash``          — record dont_read (negative label) + mark read
- ``POST /api/daily/verdict``        — record a must/should/could/don't card label
- ``POST /api/daily/role-verdict``   — record a per-card worth/waste rating (rehydrated by GET /api/daily)
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.triage import daily_actions, daily_select
from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.storage import repositories


router = APIRouter()


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class RoleVerdictRequest(BaseModel):
    # Bug-fix Phase 1.18 Step 4: item_key for feed papers is the live
    # arxiv URL (``http://arxiv.org/abs/...``) which contains ``/``. Path
    # parameters can't carry slashes — encoded ``%2F`` ASGI servers
    # interpret as literal ``/`` and the FastAPI route fails to match
    # (HTTP 405). Carry the key in the body instead so any string works.
    item_key: str | None = Field(
        default=None,
        description=(
            "Item key the verdict applies to. Required when posting to "
            "/api/daily/role-verdict; ignored when the legacy path-based "
            "route is used (path wins)."
        ),
    )
    role: str = Field(..., description="Role slot the paper occupied in the slate.")
    verdict: str = Field(
        ...,
        description="One of: worth | waste | unknown.",
    )
    composite_score: float | None = Field(
        default=None, description="Composite score at the time of display."
    )
    surprise_score: float | None = Field(
        default=None, description="Surprise score at the time of display."
    )
    corpus_affinity: float | None = Field(
        default=None, description="Corpus affinity at the time of display."
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _db_path():
    return get_settings().triage_db_path


def _awaiting_review_total(lookback_hours: int = 168) -> int:
    """Honest count of feed papers genuinely awaiting an add/trash decision.

    Delegates to the slate's own handled-aware logic so the header counter and
    the slate share one definition of "awaiting" (the old raw
    ``triaged_pending`` count included already-handled papers and disagreed
    with what the slate actually showed)."""
    return daily_select.count_awaiting_unhandled(
        _db_path(), lookback_hours=lookback_hours
    )


def _slate_to_payload(slate: daily_select.DailySlate) -> dict[str, Any]:
    return {
        "papers": [asdict(paper) for paper in slate.papers],
        "pool_size": slate.pool_size,
        "capped_at": slate.capped_at,
        "lookback_hours": slate.lookback_hours,
        "empty_role_events": list(slate.empty_role_events),
        "fellback_to_recent": slate.fellback_to_recent,
    }


def _attach_saved_verdicts(papers: list[dict[str, Any]]) -> None:
    """Overlay each card's previously-saved verdict + label onto the payload.

    Without this the Today UI loses its selection state on every reload (the
    React state is session-only), so a saved must/should/could/don't and a
    worth/waste rating both vanish from the screen even though they persist
    in the DB. Adds ``role_value_verdict`` (worth|waste|unknown) and
    ``user_priority`` (must_read|…|dont_read) so the frontend can rehydrate.
    """
    if not papers:
        return
    db = _db_path()
    item_keys = [str(p.get("item_key") or "") for p in papers]
    pks = [int(p["item_id"]) for p in papers if isinstance(p.get("item_id"), int)]
    role_verdicts = repositories.get_role_verdicts_by_keys(db, item_keys)
    label_priorities = repositories.get_label_priorities_by_pks(db, pks)
    for paper in papers:
        paper["role_value_verdict"] = role_verdicts.get(str(paper.get("item_key") or ""))
        paper["user_priority"] = label_priorities.get(paper.get("item_id"))


async def get_daily(K: int = 5, lookback_hours: int = 168) -> dict[str, Any]:
    """Return today's slate of up to ``K`` papers across all RSS feeds."""
    if not (5 <= K <= 20):
        raise APIError(
            error="validation_error",
            message=f"K must be between 5 and 20 inclusive; got {K}",
            status_code=422,
        )
    if not (24 <= lookback_hours <= 720):
        raise APIError(
            error="validation_error",
            message=(
                f"lookback_hours must be between 24 and 720 inclusive; got {lookback_hours}"
            ),
            status_code=422,
        )
    slate = await asyncio.to_thread(
        daily_select.assemble_daily_slate,
        db_path=_db_path(),
        K=K,
        lookback_hours=lookback_hours,
    )
    payload = _slate_to_payload(slate)
    await asyncio.to_thread(_attach_saved_verdicts, payload["papers"])
    payload["awaiting_review_total"] = await asyncio.to_thread(
        _awaiting_review_total, lookback_hours
    )
    payload["showing"] = len(payload["papers"])
    return payload


# Funnel stages shown on Today: friendly label + the decision states that feed
# the count + a deep-link to the existing page that already browses that pool.
# Tesler's Law — the system carries the "what does this mean" explanation in
# the hint so the user never decodes raw decision strings.
_PIPELINE_STAGES: tuple[dict[str, Any], ...] = (
    {
        "key": "filtered",
        "label": "Filtered",
        "decisions": [
            feeds_storage.DECISION_GATE_REJECTED,
            feeds_storage.DECISION_REJECTED_LOW_SCORE,
            feeds_storage.DECISION_REJECTED_DEDUP_LIBRARY,
            feeds_storage.DECISION_REJECTED_DEDUP_PROCESSED,
            feeds_storage.DECISION_REJECTED_DAILY_CUTOFF,
            feeds_storage.DECISION_REJECTED_ELBOW,
        ],
        "hint": "Auto-filtered by the model. Marked read in Zotero, NOT added "
                "to your library. Spot-check them to catch a wrong reject.",
        "link": "/review?state=gate_rejected",
    },
    {
        "key": "awaiting",
        "label": "Awaiting you",
        "decisions": [],  # computed via the handled-aware count, not a raw sum
        "hint": "Scored and waiting for your Add/Trash decision — this is your "
                "cull queue.",
        "link": "/review?state=awaiting_review",
    },
    {
        "key": "added",
        "label": "Added",
        "decisions": [
            feeds_storage.DECISION_SELECTED,
            feeds_storage.DECISION_BLACK_SWAN,
            feeds_storage.DECISION_USER_APPROVED,
        ],
        "hint": "Kept into your Zotero 'Inbox' collection — they sit unread "
                "there by design until you read them.",
        "link": None,
    },
    {
        "key": "trashed",
        "label": "Trashed",
        "decisions": [feeds_storage.DECISION_USER_REJECTED],
        "hint": "You rejected these — recorded as a strong negative signal and "
                "marked read in Zotero.",
        "link": None,
    },
)


def _pipeline_payload(lookback_hours: int) -> dict[str, Any]:
    import sqlite3

    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    try:
        by_decision = feeds_storage.count_all_by_decision(conn)
    finally:
        conn.close()

    awaiting = daily_select.count_awaiting_unhandled(
        _db_path(), lookback_hours=lookback_hours
    )
    total_in = sum(by_decision.values())

    stages: list[dict[str, Any]] = [
        {"key": "in", "label": "Came in", "count": total_in, "link": None,
         "hint": "Feed papers the app has triaged so far. Items not yet "
                 "triaged still show as unread in your Zotero feeds."},
    ]
    for stage in _PIPELINE_STAGES:
        count = (
            awaiting
            if stage["key"] == "awaiting"
            else sum(by_decision.get(d, 0) for d in stage["decisions"])
        )
        stages.append({
            "key": stage["key"],
            "label": stage["label"],
            "count": count,
            "hint": stage["hint"],
            "link": stage["link"],
        })
    return {"stages": stages, "by_decision": by_decision}


async def get_pipeline(lookback_hours: int = 168) -> dict[str, Any]:
    """Funnel overview for Today: came in / filtered / awaiting / added / trashed."""
    if not (24 <= lookback_hours <= 720):
        raise APIError(
            error="validation_error",
            message=(
                f"lookback_hours must be between 24 and 720 inclusive; got {lookback_hours}"
            ),
            status_code=422,
        )
    return await asyncio.to_thread(_pipeline_payload, lookback_hours)


async def submit_role_verdict(item_key: str, body: RoleVerdictRequest) -> dict[str, Any]:
    """Record one user verdict on whether a slot was worth their attention.

    Legacy path-parameter form. The path key wins over any value in the
    body; new clients should POST to ``/api/daily/role-verdict`` and
    pass ``item_key`` in the body so URL-shaped keys work.
    """
    safe_item_key = (item_key or "").strip()
    if not safe_item_key:
        raise APIError(
            error="validation_error",
            message="item_key path parameter is required",
            status_code=422,
        )
    return await _record_role_verdict(safe_item_key, body)


async def submit_role_verdict_body(body: RoleVerdictRequest) -> dict[str, Any]:
    """Record a role verdict using item_key from the request body.

    Avoids path-parameter encoding issues for URL-shaped item keys
    (feed papers use ``http://arxiv.org/abs/…`` as item_key).
    """
    safe_item_key = (body.item_key or "").strip()
    if not safe_item_key:
        raise APIError(
            error="validation_error",
            message="item_key body field is required",
            status_code=422,
        )
    return await _record_role_verdict(safe_item_key, body)


async def _record_role_verdict(item_key: str, body: RoleVerdictRequest) -> dict[str, Any]:
    """Shared insert path for both verdict routes. The ``ValueError``
    re-raise narrows storage-layer validation errors (e.g. unknown role)
    into the 422 the API contract documents — every other exception
    propagates."""
    try:
        new_id = await asyncio.to_thread(
            repositories.insert_role_value_verdict,
            _db_path(),
            item_key=item_key,
            role=body.role,
            verdict=body.verdict,
            composite_score=body.composite_score,
            surprise_score=body.surprise_score,
            corpus_affinity=body.corpus_affinity,
        )
    except ValueError as exc:
        raise APIError(
            error="validation_error", message=str(exc), status_code=422,
        ) from exc
    return {"id": new_id}


# ---------------------------------------------------------------------------
# Backlog triage (drain the un-triaged feed backlog via the custom SOTA provider)
# ---------------------------------------------------------------------------


async def trigger_triage_backlog() -> dict[str, Any]:
    """Start a background drain of the un-triaged feed backlog.

    Scoring uses the configured **backlog** stage provider/model
    (``goals.yaml: llm_routing.backlog``); the gate fast-rejects the obvious
    non-matches for free first. Returns immediately; the client polls
    ``GET /api/daily/triage-status``. If a drain is already running, this
    is a no-op that reports the in-flight status.
    """
    from zotero_summarizer.services.triage import triage_backlog
    started = triage_backlog.start_drain()
    return {"started": started, "status": triage_backlog.status()}


async def get_triage_status() -> dict[str, Any]:
    """Poll the backlog-drain job status."""
    from zotero_summarizer.services.triage import triage_backlog
    return triage_backlog.status()


async def rescore_slate() -> dict[str, Any]:
    """Re-score the current Today slate IN PLACE with the loaded gate.

    Use after a gate upgrade (e.g. a new model artifact) so the items already
    on Today reflect the new scores. Updates only the gate-derived fields —
    never a card's decision or read status, so nothing already handled is
    re-surfaced. Reads the LIVE in-memory gate, so restart the server first if
    you trained a new artifact with an unchanged golden-CSV sha.
    """
    from zotero_summarizer.services.triage import rescore_slate as rescore
    return await asyncio.to_thread(rescore.rescore_slate)


# ---------------------------------------------------------------------------
# Today-card priority verdict (feeds golden training)
# ---------------------------------------------------------------------------


class DailyVerdictRequest(BaseModel):
    item_id: int = Field(..., ge=1, description="processed_feed_items PK (SlatePaper.item_id).")
    user_priority: str = Field(
        ..., min_length=1,
        description="One of: must_read | should_read | could_read | dont_read.",
    )
    comment: str = Field(default="", description="Optional free-text rationale.")


_VALID_DAILY_PRIORITIES = ("must_read", "should_read", "could_read", "dont_read")


async def submit_daily_verdict(body: DailyVerdictRequest) -> dict[str, Any]:
    """Record a must/should/could/don't verdict on a Today card.

    The verdict is the user's manual label and must (a) persist + win, (b)
    feed golden-set training. We therefore both UPSERT ``label_verdicts``
    (keyed ``feed:<feed_item_id>`` — consistent with review_detail + the
    golden CSV) AND append the feed item to the golden CSV via
    ``review.append_to_golden`` (idempotent) so the next retrain trains on
    the manual label through ``hybrid_gt``.
    """
    if body.user_priority not in _VALID_DAILY_PRIORITIES:
        raise APIError(
            error="validation_error",
            message=f"user_priority must be one of {_VALID_DAILY_PRIORITIES}; got {body.user_priority!r}",
            status_code=422,
        )

    row = await asyncio.to_thread(_load_processed_row, body.item_id)
    if row is None:
        raise APIError(
            error="not_found",
            message=f"no processed_feed_items row with id={body.item_id}",
            status_code=404,
        )

    feed_item_id = int(row.get("feed_item_id") or 0)
    golden_key = f"feed:{feed_item_id}" if feed_item_id else f"processed:{row.get('id')}"

    # Append to golden CSV (idempotent) so the row enters the training set.
    from zotero_summarizer.services.library import review
    await asyncio.to_thread(
        review.append_to_golden, row,
        label=body.user_priority, note=body.comment,
    )

    # UPSERT the manual verdict so it wins + stays visible/editable.
    derived = (row.get("reading_priority") or "").strip() or "unknown"
    row_id = await asyncio.to_thread(
        repositories.insert_or_update_label_verdict,
        _db_path(),
        item_key=golden_key,
        original_derived_priority=derived,
        user_priority=body.user_priority,
        comment=body.comment,
    )
    return {"id": row_id, "item_key": golden_key}


# ---------------------------------------------------------------------------
# Today batch keep/trash (Stage 1 of the two-stage reading flow)
# ---------------------------------------------------------------------------


class BatchItemsRequest(BaseModel):
    item_ids: list[int] = Field(
        ..., min_length=1,
        description="processed_feed_items PKs (SlatePaper.item_id) to act on.",
    )


async def add_to_library(body: BatchItemsRequest) -> dict[str, Any]:
    """Materialize the selected Today cards into the Zotero Inbox + record a
    positive training label. Acted cards drop out of the slate on next fetch."""
    return await asyncio.to_thread(daily_actions.add_to_library, body.item_ids)


async def trash_papers(body: BatchItemsRequest) -> dict[str, Any]:
    """Record dont_read (strong negative) for the selected cards + mark them
    read. Acted cards drop out of the slate on next fetch."""
    return await asyncio.to_thread(daily_actions.trash, body.item_ids)


def _load_processed_row(pk: int) -> dict[str, Any] | None:
    import sqlite3
    from zotero_summarizer.storage import feeds as feeds_storage
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    try:
        return feeds_storage.get_processed_feed_item_by_pk(conn, pk)
    finally:
        conn.close()


router.add_api_route("/api/daily", get_daily, methods=["GET"])
router.add_api_route("/api/daily/pipeline", get_pipeline, methods=["GET"])
router.add_api_route(
    "/api/daily/role-verdict", submit_role_verdict_body, methods=["POST"],
)
router.add_api_route(
    "/api/daily/triage-backlog", trigger_triage_backlog, methods=["POST"],
)
router.add_api_route(
    "/api/daily/triage-status", get_triage_status, methods=["GET"],
)
router.add_api_route(
    "/api/daily/rescore-slate", rescore_slate, methods=["POST"],
)
router.add_api_route(
    "/api/daily/verdict", submit_daily_verdict, methods=["POST"],
)
router.add_api_route(
    "/api/daily/add-to-library", add_to_library, methods=["POST"],
)
router.add_api_route(
    "/api/daily/trash", trash_papers, methods=["POST"],
)
router.add_api_route(
    "/api/daily/{item_key}/role-verdict", submit_role_verdict, methods=["POST"],
)
