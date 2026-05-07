from __future__ import annotations

import asyncio
import json
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.domain import (
    TRIAGE_APPROVED_TAG,
    TRIAGE_REJECTED_TAG,
    ReadingPriority,
    feedback_signal_from_verdict,
    feedback_verdict_from_signal,
    is_positive_priority,
    is_valid_reading_priority,
)
from zotero_summarizer.models import (
    CalibrationPeriodMetrics,
    TriageDimensionOverrideRequest,
    TriageDimensions,
    TriageFeedbackRequest,
    TriageFeedbackResponse,
    TriageResult,
)
from zotero_summarizer.services import scoring
from zotero_summarizer.services._common import LOGGER, clamp, now_iso, safe_parse_response_json
from zotero_summarizer.storage import repositories as triage_db


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(float(numerator) / float(denominator), 3)


def compute_calibration_period(rows: list[dict[str, Any]]) -> CalibrationPeriodMetrics:
    approved_count = 0
    rejected_count = 0
    with_prediction_count = 0
    agreement_count = 0
    false_positive_count = 0
    false_negative_count = 0
    predicted_positive_count = 0
    actual_positive_count = 0
    true_positive_count = 0

    for row in rows:
        signal = str(row.get("signal") or "").strip()
        verdict = feedback_verdict_from_signal(signal)
        if verdict is None:
            continue

        is_approved = verdict == "approve"
        approved_count += 1 if is_approved else 0
        rejected_count += 0 if is_approved else 1

        reading_priority = str(row.get("reading_priority") or "").strip()
        if not is_valid_reading_priority(reading_priority):
            continue

        with_prediction_count += 1
        predicted_positive = is_positive_priority(reading_priority)
        if predicted_positive:
            predicted_positive_count += 1
        if is_approved:
            actual_positive_count += 1
        if predicted_positive and is_approved:
            true_positive_count += 1
        if predicted_positive and not is_approved:
            false_positive_count += 1
        if not predicted_positive and is_approved:
            false_negative_count += 1
        if predicted_positive == is_approved:
            agreement_count += 1

    return CalibrationPeriodMetrics(
        total_feedback=approved_count + rejected_count,
        approved_count=approved_count,
        rejected_count=rejected_count,
        with_prediction_count=with_prediction_count,
        agreement_count=agreement_count,
        false_positive_count=false_positive_count,
        false_negative_count=false_negative_count,
        predicted_positive_count=predicted_positive_count,
        actual_positive_count=actual_positive_count,
        true_positive_count=true_positive_count,
        agreement_rate=_safe_ratio(agreement_count, with_prediction_count),
        precision=_safe_ratio(true_positive_count, predicted_positive_count),
        recall=_safe_ratio(true_positive_count, actual_positive_count),
    )


async def dashboard_results(
    scope: str = "latest",
    batch_id: str | None = None,
    batch_ids: str | None = None,
    sort: str = "composite_score",
    order: str = "desc",
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    limit = max(0, min(limit, 500))
    offset = max(0, offset)
    safe_scope = str(scope or "latest").strip().lower()
    if safe_scope not in {"latest", "all", "batch", "compare"}:
        safe_scope = "latest"

    selected_batch_ids: list[str] = []
    if batch_id:
        selected_batch_ids = [batch_id]
    elif batch_ids:
        selected_batch_ids = [value.strip() for value in batch_ids.split(",") if value.strip()]

    if safe_scope == "batch" and selected_batch_ids:
        selected_batch_ids = selected_batch_ids[:1]
        rows = await asyncio.to_thread(triage_db.get_results_by_batch_ids, selected_batch_ids, sort, order, limit, offset)
    elif safe_scope == "compare" and selected_batch_ids:
        rows = await asyncio.to_thread(triage_db.get_results_by_batch_ids, selected_batch_ids, sort, order, limit, offset)
    elif safe_scope in {"batch", "compare"}:
        rows = []
    elif safe_scope == "all":
        rows = await asyncio.to_thread(triage_db.get_all_results, sort, order, limit, offset)
    else:
        rows = await asyncio.to_thread(triage_db.get_latest_results, sort, order, limit, offset)

    total = await asyncio.to_thread(triage_db.get_result_count, safe_scope, selected_batch_ids)
    for row in rows:
        row["response_json"] = safe_parse_response_json(row.get("response_json"), f"/api/results item_id={row.get('item_id')}")
    return {"scope": safe_scope, "total": total, "items": rows, "selected_batch_ids": selected_batch_ids}


async def dashboard_batches(limit: int = 20) -> dict[str, Any]:
    safe_limit = max(1, min(limit, 100))
    items = await asyncio.to_thread(triage_db.get_batch_runs, safe_limit)
    return {"items": items}


async def dashboard_result_detail(item_id: str, batch_id: str | None = None) -> dict[str, Any]:
    row = await asyncio.to_thread(triage_db.get_result_by_item_id, item_id, batch_id)
    if not row:
        raise APIError(error="not_found", message="Item not found", status_code=404)
    row["response_json"] = safe_parse_response_json(row.get("response_json"), f"/api/results/{{item_id}} item_id={item_id}")
    return row


async def submit_triage_feedback(item_key: str, req: TriageFeedbackRequest) -> TriageFeedbackResponse:
    safe_item_key = str(item_key or "").strip()
    if not safe_item_key:
        raise APIError(error="validation_error", message="item_key is required", status_code=422)

    result_row = await asyncio.to_thread(triage_db.get_result_by_item_id, safe_item_key, None)
    if not result_row:
        raise APIError(error="not_found", message="Item has no triage result yet", status_code=404)

    signal = feedback_signal_from_verdict(req.verdict)
    opposite_signal = "explicit_reject" if signal == "explicit_approve" else "explicit_approve"
    inferred_relevance = 5.0 if req.verdict == "approve" else 1.0
    original_priority = str(result_row.get("forced_priority") or result_row.get("reading_priority") or "").strip()

    await asyncio.to_thread(triage_db.delete_feedback_signals, safe_item_key, [opposite_signal])
    await asyncio.to_thread(
        triage_db.insert_feedback_events,
        [
            {
                "item_id": safe_item_key,
                "feedback_type": "explicit",
                "signal": signal,
                "original_priority": original_priority,
                "inferred_relevance": inferred_relevance,
            }
        ],
    )

    item_title = str(result_row.get("title") or safe_item_key)
    add_tag = TRIAGE_APPROVED_TAG if req.verdict == "approve" else TRIAGE_REJECTED_TAG
    remove_tag = TRIAGE_REJECTED_TAG if req.verdict == "approve" else TRIAGE_APPROVED_TAG
    queued = await asyncio.to_thread(
        triage_db.insert_pending_changes,
        safe_item_key,
        item_title,
        [{"change_type": "tag_changes", "payload": {"add_tags": [add_tag], "remove_tags": [remove_tag]}}],
    )
    LOGGER.info("Feedback saved item_key=%s verdict=%s queued=%s", safe_item_key, req.verdict, queued)
    return TriageFeedbackResponse(item_id=safe_item_key, verdict=req.verdict, signal=signal, queued=queued)


async def override_triage_dimensions(item_key: str, req: TriageDimensionOverrideRequest) -> dict[str, Any]:
    safe_item_key = str(item_key or "").strip()
    if not safe_item_key:
        raise APIError(error="validation_error", message="item_key is required", status_code=422)

    result_row = await asyncio.to_thread(triage_db.get_result_by_item_id, safe_item_key, None)
    if not result_row:
        raise APIError(error="not_found", message="Item has no triage result yet", status_code=404)

    response_json = result_row.get("response_json") or {}
    if isinstance(response_json, str):
        try:
            response_json = json.loads(response_json)
        except json.JSONDecodeError as exc:
            raise APIError(error="invalid_result_payload", message="Stored result JSON is invalid", status_code=500) from exc
    if not isinstance(response_json, dict):
        raise APIError(error="invalid_result_payload", message="Stored result payload is not an object", status_code=500)

    try:
        original_dimensions_obj = TriageDimensions.model_validate(response_json.get("triage_dimensions") or {})
        original_dimensions = original_dimensions_obj.model_dump(mode="python")
    except Exception:
        original_dimensions_obj = TriageDimensions()
        original_dimensions = original_dimensions_obj.model_dump(mode="python")

    override_dimensions = req.to_partial_dimensions()
    merged_dimensions_payload = {**original_dimensions, **override_dimensions}
    merged_dimensions_obj = TriageDimensions.model_validate(merged_dimensions_payload)

    try:
        triage_score = int(response_json.get("relevance_score", result_row.get("relevance_score")))
    except (TypeError, ValueError):
        triage_score = 3
    triage_score = max(1, min(5, triage_score))

    try:
        triage_confidence = float(response_json.get("triage_confidence", result_row.get("confidence")))
    except (TypeError, ValueError):
        triage_confidence = 0.5
    triage_confidence = clamp(triage_confidence, 0.0, 1.0)

    try:
        corpus_affinity = float(response_json.get("corpus_affinity_score"))
    except (TypeError, ValueError):
        corpus_affinity = 0.0
    corpus_affinity = clamp(corpus_affinity, -1.0, 1.0)

    triage_for_scoring = TriageResult(
        score=triage_score,
        reading_priority=str(result_row.get("reading_priority") or ReadingPriority.COULD_READ.value),
        tags=list(response_json.get("tags") or []),
        rationale=str(response_json.get("triage_rationale") or "manual override"),
        dimensions=merged_dimensions_obj,
        confidence=triage_confidence,
    )
    new_composite_score = scoring.compute_composite_score(triage_for_scoring, corpus_affinity)
    new_priority = scoring.map_priority_from_score(new_composite_score)

    original_priority = str(result_row.get("forced_priority") or result_row.get("reading_priority") or "").strip()
    try:
        original_composite_score = float(result_row.get("composite_score"))
    except (TypeError, ValueError):
        original_composite_score = None

    merged_dimensions = merged_dimensions_obj.model_dump(mode="python")
    response_json["triage_dimensions"] = merged_dimensions
    response_json["composite_relevance_score"] = new_composite_score
    response_json["reading_priority"] = new_priority
    response_json["triage_manual_override"] = {
        "applied_at": now_iso(),
        "override_dimensions": override_dimensions,
        "source": "ui",
    }

    result_row_id = int(result_row.get("id") or 0)
    updated = await asyncio.to_thread(
        triage_db.update_result_row_after_override,
        result_row_id,
        response_json,
        new_composite_score,
        new_priority,
        new_priority,
    )
    if not updated:
        raise APIError(error="conflict", message="Failed to update triage result", status_code=409)

    override_id = await asyncio.to_thread(
        triage_db.insert_triage_dimension_override,
        safe_item_key,
        result_row_id,
        original_dimensions,
        override_dimensions,
        merged_dimensions,
        corpus_affinity,
        original_composite_score,
        new_composite_score,
        original_priority,
        new_priority,
    )
    await asyncio.to_thread(
        triage_db.insert_feedback_events,
        [
            {
                "item_id": safe_item_key,
                "feedback_type": "manual_dimension_override",
                "signal": "manual_dimension_override",
                "original_priority": original_priority,
                "inferred_relevance": float(new_composite_score),
            }
        ],
    )

    LOGGER.info(
        "Dimension override applied item=%s override_id=%s old_score=%s new_score=%.2f old_priority=%s new_priority=%s",
        safe_item_key,
        override_id,
        original_composite_score,
        new_composite_score,
        original_priority,
        new_priority,
    )
    return {
        "item_id": safe_item_key,
        "override_id": override_id,
        "old_composite_score": original_composite_score,
        "new_composite_score": new_composite_score,
        "old_priority": original_priority,
        "new_priority": new_priority,
        "original_dimensions": original_dimensions,
        "override_dimensions": override_dimensions,
        "merged_dimensions": merged_dimensions,
    }
