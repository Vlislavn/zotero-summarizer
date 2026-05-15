"""Phase 1.17 Step 1+2+4 — "Today" tab endpoints.

Five endpoints power the daily-5 product and its 3-stage validation:

- ``GET  /api/daily``                             — assemble the daily slate
- ``POST /api/daily/{item_key}/role-verdict``     — Stage 1: per-paper role-value
- ``GET  /api/daily/role-stats``                  — per-role win-rate summary
- ``POST /api/daily/weekly-ab``                   — Stage 2: weekly A/B verdict
- ``GET  /api/daily/ab-status``                   — A/B running totals + lock state
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services import daily_select
from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.storage import repositories


router = APIRouter()


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class RoleVerdictRequest(BaseModel):
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


class WeeklyABRequest(BaseModel):
    week_start: str = Field(..., description="ISO date YYYY-MM-DD for the week start.")
    winner: str = Field(..., description="One of: roles | pure_score | tied.")
    slate_a_keys: list[str] = Field(
        ..., min_length=1,
        description="Item keys shown in slate A (role-allocated).",
    )
    slate_b_keys: list[str] = Field(
        ..., min_length=1,
        description="Item keys shown in slate B (pure top-K by composite).",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _db_path():
    return get_settings().triage_db_path


def _slate_to_payload(slate: daily_select.DailySlate) -> dict[str, Any]:
    return {
        "papers": [asdict(paper) for paper in slate.papers],
        "pool_size": slate.pool_size,
        "capped_at": slate.capped_at,
        "lookback_hours": slate.lookback_hours,
        "empty_role_events": list(slate.empty_role_events),
    }


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
    return _slate_to_payload(slate)


async def submit_role_verdict(item_key: str, body: RoleVerdictRequest) -> dict[str, Any]:
    """Record one user verdict on whether a slot was worth their attention."""
    safe_item_key = (item_key or "").strip()
    if not safe_item_key:
        raise APIError(
            error="validation_error",
            message="item_key path parameter is required",
            status_code=422,
        )
    try:
        new_id = await asyncio.to_thread(
            repositories.insert_role_value_verdict,
            _db_path(),
            item_key=safe_item_key,
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


async def get_role_stats() -> dict[str, Any]:
    """Return per-role win-rate counts + Wilson 95% CI."""
    summary = await asyncio.to_thread(
        repositories.list_role_verdicts_summary, _db_path(),
    )
    return {"roles": summary}


async def submit_weekly_ab(body: WeeklyABRequest) -> dict[str, Any]:
    """Record one weekly A/B verdict."""
    try:
        new_id = await asyncio.to_thread(
            repositories.insert_weekly_ab_verdict,
            _db_path(),
            week_start=body.week_start,
            winner=body.winner,
            slate_a_keys=body.slate_a_keys,
            slate_b_keys=body.slate_b_keys,
        )
    except ValueError as exc:
        raise APIError(
            error="validation_error", message=str(exc), status_code=422,
        ) from exc
    return {"id": new_id}


async def get_ab_status() -> dict[str, Any]:
    """Return the running A/B tally + decision-lock state."""
    return await asyncio.to_thread(repositories.list_ab_decision_status, _db_path())


router.add_api_route("/api/daily", get_daily, methods=["GET"])
router.add_api_route(
    "/api/daily/role-stats", get_role_stats, methods=["GET"],
)
router.add_api_route(
    "/api/daily/ab-status", get_ab_status, methods=["GET"],
)
router.add_api_route(
    "/api/daily/weekly-ab", submit_weekly_ab, methods=["POST"],
)
router.add_api_route(
    "/api/daily/{item_key}/role-verdict", submit_role_verdict, methods=["POST"],
)
