from __future__ import annotations

import asyncio
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.domain import (
    TRIAGE_APPROVED_TAG,
    TRIAGE_REJECTED_TAG,
    feedback_signal_from_verdict,
    feedback_verdict_from_signal,
    is_positive_priority,
    is_valid_reading_priority,
)
from zotero_summarizer.models import (
    CalibrationPeriodMetrics,
    TriageFeedbackRequest,
    TriageFeedbackResponse,
)
from zotero_summarizer.services._common import LOGGER, safe_parse_response_json
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
