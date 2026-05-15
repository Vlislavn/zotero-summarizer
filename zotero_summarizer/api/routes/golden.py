"""Phase 1.18 Step 1 — Label-Provenance audit + verdict endpoints.

Surfaces the chain-of-reasoning behind every paper's ``gold_priority_final``
so the user can audit ground-truth labels before trusting any model
metric computed against them, and records per-paper user verdicts that
override the derived label.

Endpoints:
- GET    /api/golden/provenance?item_key=...   single-paper breakdown
- GET    /api/golden/provenance/list           all rows + flag summary
- GET    /api/golden/review-detail?item_key=…  full paper detail + provenance + verdict
- POST   /api/golden/verdict                   record a user verdict (UPSERT per paper)
- GET    /api/golden/verdicts                  list all verdicts (optional filter)
- DELETE /api/golden/verdict?item_key=...      remove a verdict
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services import (
    hybrid_gt,
    label_provenance,
    review_detail as review_detail_svc,
)
from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.services.zotero import get_zotero_reader_or_raise
from zotero_summarizer.storage import repositories


router = APIRouter()


_VALID_USER_PRIORITIES = ("must_read", "should_read", "could_read", "dont_read")


class VerdictRequest(BaseModel):
    item_key: str = Field(..., min_length=1, description="Zotero item key.")
    user_priority: str = Field(
        ..., min_length=1,
        description="One of: must_read | should_read | could_read | dont_read.",
    )
    comment: str = Field(
        default="",
        description="Optional free-text rationale; empty string is allowed.",
    )


def _golden_csv_path():
    return get_settings().project_root / "zotero-summarizer-golden.csv"


def _db_path():
    return get_settings().triage_db_path


def _load_all():
    """Load every row's provenance. Fail-fast if the CSV is missing."""
    return label_provenance.load_golden_provenance(_golden_csv_path())


async def get_one(item_key: str) -> dict[str, Any]:
    """Return the full provenance breakdown for one paper."""
    provs = _load_all()
    for p in provs:
        if p.item_key == item_key:
            return label_provenance.provenance_to_dict(p)
    raise APIError(
        error="not_found",
        message=f"item_key {item_key!r} not in golden CSV",
        status_code=404,
    )


async def list_all(
    priority: str | None = None,
    flag: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """List provenance summaries with optional priority/flag filters.

    Use ``priority=must_read&flag=weak_must_read`` to find borderline labels
    the user should review. ``limit`` caps response size to keep the UI fast.
    """
    if not (1 <= int(limit) <= 2000):
        raise APIError(
            error="validation_error",
            message=f"limit must be between 1 and 2000; got {limit}",
            status_code=422,
        )
    provs = _load_all()

    filtered = provs
    if priority:
        filtered = [p for p in filtered if p.persisted_priority == priority]
    if flag:
        filtered = [p for p in filtered if flag in p.flags]

    summary = label_provenance.flag_summary(provs)
    items = [
        {
            "item_key": p.item_key,
            "title": p.title,
            "persisted_priority": p.persisted_priority,
            "derived_priority": p.derived_priority,
            "derived_score": p.derived_score,
            "is_direct_user_verdict": p.is_direct_user_verdict,
            "is_manual_override": p.is_manual_override,
            "flags": list(p.flags),
        }
        for p in filtered[:limit]
    ]
    return {
        "items": items,
        "total_matched": len(filtered),
        "total_rows": len(provs),
        "flag_counts": {k: len(v) for k, v in summary.items()},
    }


def _build_source_payload(item_key: str) -> dict[str, Any] | None:
    """Dispatch by ``item_key`` prefix and return the source-specific
    payload, or ``None`` when the underlying data row is gone.

    Each branch is a thin wrapper that resolves dependencies (DB path,
    Zotero reader) before delegating to ``services.review_detail``.
    """
    settings_ = get_settings()
    source = review_detail_svc.classify_item_key(item_key)

    if source == review_detail_svc.SOURCE_FEED:
        feed_item_id = review_detail_svc.parse_feed_key(item_key)
        return review_detail_svc.build_feed_detail(
            triage_db_path=settings_.triage_db_path,
            zotero_data_dir=settings_.zotero_data_dir,
            feed_item_id=feed_item_id,
        )

    if source == review_detail_svc.SOURCE_NOTE:
        parent_key, note_id = review_detail_svc.parse_note_key(item_key)
        reader = get_zotero_reader_or_raise()
        return review_detail_svc.build_note_detail(reader, parent_key, note_id)

    reader = get_zotero_reader_or_raise()
    return review_detail_svc.build_library_detail(reader, item_key)


async def review_detail(item_key: str) -> dict[str, Any]:
    """Return the composed verdict-UI payload for one paper.

    Dispatches by key prefix:
      * ``feed:<id>`` -> processed_feed_items + Zotero feedItems lookup
      * ``note:<parent>:<id>`` -> parent Zotero item + the chosen note
      * else (8-char) -> Zotero library item

    The payload shape is uniform across the three sources (see
    ``services/review_detail.py``); the React UI branches on the
    ``source`` field, not on key syntax. Fails 404 only when the
    referenced row is gone from its underlying store.
    """
    safe_item_key = str(item_key or "").strip()
    if not safe_item_key:
        raise APIError(
            error="validation_error",
            message="item_key is required",
            status_code=422,
        )

    provs = _load_all()
    prov_match = next((p for p in provs if p.item_key == safe_item_key), None)
    if prov_match is None:
        raise APIError(
            error="not_found",
            message=f"item_key {safe_item_key!r} not in golden CSV",
            status_code=404,
        )

    source_payload = await asyncio.to_thread(_build_source_payload, safe_item_key)
    if source_payload is None:
        raise APIError(
            error="not_found",
            message=f"item_key {safe_item_key!r}: underlying row not found in its source store",
            status_code=404,
        )

    verdict_row = repositories.get_label_verdict(_db_path(), safe_item_key)

    return {
        "item_key": safe_item_key,
        **source_payload,
        "provenance": label_provenance.provenance_to_dict(prov_match),
        "verdict": verdict_row,
    }


async def submit_verdict(req: VerdictRequest) -> dict[str, Any]:
    """Record a user verdict for one paper. UPSERTs over any prior verdict."""
    if req.user_priority not in _VALID_USER_PRIORITIES:
        raise APIError(
            error="validation_error",
            message=(
                f"user_priority must be one of {_VALID_USER_PRIORITIES}; "
                f"got {req.user_priority!r}"
            ),
            status_code=422,
        )

    # Anchor original_derived_priority to the CURRENT provenance, never the client.
    provs = _load_all()
    prov_match = next((p for p in provs if p.item_key == req.item_key), None)
    if prov_match is None:
        raise APIError(
            error="not_found",
            message=f"item_key {req.item_key!r} not in golden CSV",
            status_code=404,
        )

    row_id = repositories.insert_or_update_label_verdict(
        _db_path(),
        item_key=req.item_key,
        original_derived_priority=prov_match.derived_priority,
        user_priority=req.user_priority,
        comment=req.comment,
    )
    stored = repositories.get_label_verdict(_db_path(), req.item_key)
    if stored is None:
        raise RuntimeError(
            f"verdict UPSERT returned id={row_id} but get_label_verdict found nothing"
        )
    return {"id": row_id, "created_at": stored["created_at"]}


async def list_verdicts(user_priority: str | None = None) -> dict[str, Any]:
    """List recorded verdicts, optionally filtered by user_priority."""
    if user_priority is not None and user_priority not in _VALID_USER_PRIORITIES:
        raise APIError(
            error="validation_error",
            message=(
                f"user_priority must be one of {_VALID_USER_PRIORITIES}; "
                f"got {user_priority!r}"
            ),
            status_code=422,
        )
    verdicts = repositories.list_label_verdicts(
        _db_path(), user_priority=user_priority
    )
    return {"verdicts": verdicts, "total": len(verdicts)}


async def remove_verdict(item_key: str) -> dict[str, Any]:
    """Delete one verdict; returns whether a row was removed."""
    safe_item_key = str(item_key or "").strip()
    if not safe_item_key:
        raise APIError(
            error="validation_error",
            message="item_key is required",
            status_code=422,
        )
    deleted = repositories.delete_label_verdict(_db_path(), safe_item_key)
    return {"deleted": deleted}


async def effective_labels_summary() -> dict[str, Any]:
    """Aggregate counts for the hybrid ground-truth pipeline.

    Phase 1.18 Step 2: surfaces how many CSV rows have a user verdict
    overlaid, and of those, how many changed the derived label vs.
    confirmed it. Powers the "Used as GT" badge and the Settings
    "Effective labels" widget.
    """
    return hybrid_gt.hybrid_summary(_golden_csv_path(), _db_path())


async def effective_labels_list() -> dict[str, Any]:
    """Return the full hybrid ground-truth map.

    Keys are item_key, values carry both the derived and user labels and
    the ``effective_priority`` that ML pipelines use. Useful for the UI
    to mark which rows are user-overridden.
    """
    merged = hybrid_gt.load_hybrid_labels(_golden_csv_path(), _db_path())
    return {
        "items": list(merged.values()),
        "total": len(merged),
    }


async def border_suggestions(top_k: int = 20) -> dict[str, Any]:
    """Active-learning endpoint: return library rows whose re-labelling
    would maximally help the model, ranked by distance to the nearest
    priority threshold (4.5 / 3.6 / 2.6). The user opens each one in the
    UI and updates the verdict.
    """
    from zotero_summarizer.services import active_learning
    from zotero_summarizer.services._common import read_config

    csv_path = _golden_csv_path()
    if not csv_path.exists():
        raise APIError(
            error="not_found",
            message=f"golden CSV missing at {csv_path}",
            status_code=404,
        )
    rows = active_learning.load_rows(csv_path)
    goals_config = read_config(get_settings().config_path)
    suggestions = await asyncio.to_thread(
        active_learning.suggest_border_labels,
        rows,
        corpus_db_path=get_settings().corpus_db_path,
        goals_config=goals_config,
        classifier_name="lightgbm",
        top_k=int(top_k),
    )
    return {
        "items": [
            {
                "item_key": s.item_key,
                "title": s.title,
                "authors": s.authors,
                "venue": s.venue,
                "abstract_preview": s.abstract_preview,
                "predicted_score": round(s.predicted_score, 4),
                "predicted_priority": s.predicted_priority,
                "current_priority": s.current_priority,
                "border_distance": round(s.border_distance, 4),
                "disagrees": s.disagrees,
            }
            for s in suggestions
        ],
        "total": len(suggestions),
    }


router.add_api_route("/api/golden/provenance", get_one, methods=["GET"])
router.add_api_route("/api/golden/provenance/list", list_all, methods=["GET"])
router.add_api_route("/api/golden/review-detail", review_detail, methods=["GET"])
router.add_api_route("/api/golden/verdict", submit_verdict, methods=["POST"])
router.add_api_route("/api/golden/verdicts", list_verdicts, methods=["GET"])
router.add_api_route("/api/golden/verdict", remove_verdict, methods=["DELETE"])
router.add_api_route(
    "/api/golden/effective-labels/summary",
    effective_labels_summary,
    methods=["GET"],
)
router.add_api_route(
    "/api/golden/border-suggestions",
    border_suggestions,
    methods=["GET"],
)
router.add_api_route(
    "/api/golden/effective-labels",
    effective_labels_list,
    methods=["GET"],
)
