from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import uuid
from typing import Any

from zotero_summarizer.api.errors import APIError, ExtractionError
from zotero_summarizer.contracts import TriageJob
from zotero_summarizer.domain import EXPLICIT_FEEDBACK_SIGNALS, feedback_verdict_from_signal
from zotero_summarizer.models import SummarizeRequest, TriageRunRequest, TriageRunResponse
from zotero_summarizer.services.zotero import pending
from zotero_summarizer.services.triage import summarization
from zotero_summarizer.services._common import (
    LOGGER, build_log_prefix, effective_llm_concurrency, now_iso, settings, state,
    unique_non_empty_strings,
)
from zotero_summarizer.services.zotero.zotero import get_zotero_reader_or_raise
from zotero_summarizer.storage import repositories as triage_db


TRIAGE_START_LOCK = asyncio.Lock()
TRIAGE_JOB_CONCURRENCY: int | None = None


class TriageJobService:
    """State-independent helpers for triage job lifecycle data."""

    @staticmethod
    def new_job(item_keys: list[str], queue_changes: bool = True) -> dict[str, Any]:
        normalized = unique_non_empty_strings(item_keys)
        job_id = f"job_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        current_time = now_iso()
        return {
            "job_id": job_id,
            "status": "running",
            "started_at": current_time,
            "updated_at": current_time,
            "total": len(normalized),
            "completed": 0,
            "current_item_key": "",
            "current_title": "",
            "queue_changes": bool(queue_changes),
            "item_keys": normalized,
            "results": [],
            "errors": [],
        }

    @staticmethod
    def public_job(job: dict[str, Any]) -> TriageJob:
        return TriageJob(
            job_id=str(job.get("job_id") or ""),
            status=str(job.get("status") or ""),
            total=int(job.get("total") or 0),
            completed=int(job.get("completed") or 0),
            item_keys=[str(item) for item in job.get("item_keys") or []],
            results=list(job.get("results") or []),
            errors=list(job.get("errors") or []),
        )


def _effective_concurrency(total_remaining: int) -> int:
    # The module-level override (tests / explicit ops pin) is a hard value that
    # wins regardless of provider locality.
    if TRIAGE_JOB_CONCURRENCY is not None:
        return max(1, min(int(TRIAGE_JOB_CONCURRENCY), total_remaining if total_remaining else 1))
    # Otherwise size by the feed-stage provider: serial for a local model,
    # the configured cap for a remote one. This job runs the feed pipeline.
    provider = state().resolve_stage_provider("feed")
    return effective_llm_concurrency(provider, total_remaining)


def _job_snapshot(job: dict[str, Any]) -> dict[str, Any]:
    """Shallow copy with the mutable lists copied.

    The job dict is mutated on the event loop (worker is a coroutine), but
    ``upsert_triage_job`` runs in a worker thread via ``to_thread`` and
    serialises those lists. Handing it a snapshot guarantees the thread never
    iterates a list the event loop could mutate at an await boundary.
    """
    snap = dict(job)
    snap["results"] = list(job.get("results") or [])
    snap["errors"] = list(job.get("errors") or [])
    snap["item_keys"] = list(job.get("item_keys") or [])
    return snap


def trim_job_cache(jobs: dict[str, dict[str, Any]], keep: int = 20) -> None:
    if len(jobs) <= keep:
        return
    ordered = sorted(jobs.values(), key=lambda row: str(row.get("started_at", "")), reverse=True)
    keep_ids = {str(job.get("job_id")) for job in ordered[:keep]}
    for job_id in list(jobs.keys()):
        if job_id not in keep_ids:
            jobs.pop(job_id, None)


def public_triage_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": str(job.get("job_id") or ""),
        "status": str(job.get("status") or "running"),
        "started_at": str(job.get("started_at") or ""),
        "updated_at": str(job.get("updated_at") or ""),
        "total": int(job.get("total") or 0),
        "completed": int(job.get("completed") or 0),
        "current_item_key": str(job.get("current_item_key") or ""),
        "current_title": str(job.get("current_title") or ""),
        "results": list(job.get("results") or []),
        "errors": list(job.get("errors") or []),
    }


async def run_triage_job_worker(job_id: str, item_keys: list[str], queue_changes: bool) -> None:
    app_state = state()
    jobs: dict[str, dict[str, Any]] = app_state.triage_jobs
    job = jobs.get(job_id)
    if job is None:
        return

    normalized_item_keys = unique_non_empty_strings(item_keys)
    item_positions = {item_key: idx + 1 for idx, item_key in enumerate(normalized_item_keys)}

    existing_results = list(job.get("results") or [])
    existing_errors = list(job.get("errors") or [])
    processed_keys: set[str] = set()
    for row in existing_results:
        key = str((row or {}).get("item_key") or "").strip()
        if key:
            processed_keys.add(key)
    for row in existing_errors:
        key = str((row or {}).get("item_key") or "").strip()
        if key and key != "job":
            processed_keys.add(key)

    remaining_keys = [item_key for item_key in normalized_item_keys if item_key not in processed_keys]
    effective_concurrency = _effective_concurrency(len(remaining_keys))

    job["item_keys"] = list(normalized_item_keys)
    job["queue_changes"] = bool(queue_changes)
    job["total"] = len(normalized_item_keys)
    job["completed"] = min(len(normalized_item_keys), len(processed_keys))
    if str(job.get("status") or "") != "cancelled":
        job["status"] = "running"
    job["updated_at"] = now_iso()
    await asyncio.to_thread(triage_db.upsert_triage_job, _job_snapshot(job))

    reader = None

    async def process_item(item_key: str) -> dict[str, Any]:
        if str(job.get("status") or "") == "cancelled":
            return {"item_key": item_key, "cancelled": True}
        if reader is None:
            raise APIError(error="zotero_unavailable", message="Zotero reader is unavailable", status_code=503)

        title = f"Item {item_key}"
        try:
            detail = await asyncio.to_thread(reader.get_item_detail, item_key)
            if not detail:
                raise APIError(error="not_found", message=f"Item {item_key} not found", status_code=404)

            title = str(detail.get("title") or title)
            pdf_path = str(detail.get("pdf_path") or "")
            if not pdf_path:
                raise ExtractionError("No local PDF attachment available for this item")

            request = SummarizeRequest(
                title=title,
                doi=str(detail.get("doi") or "") or None,
                pdf_path=pdf_path,
                abstract=str(detail.get("abstract") or "") or None,
            )
            prefix = build_log_prefix(
                request,
                item_id=item_key,
                batch_id=job_id,
                index=item_positions.get(item_key),
                total=len(normalized_item_keys),
            )
            summary = await asyncio.wait_for(
                asyncio.to_thread(summarization.run_pipeline, request, prefix),
                timeout=settings().summary_timeout_seconds,
            )
            await asyncio.to_thread(
                triage_db.insert_result,
                item_key,
                title,
                summary.model_dump(),
                None,
                None,
                None,
                None,
                None,
                pdf_path,
            )

            queued_change_count = 0
            if queue_changes:
                queued_change_count = await asyncio.to_thread(pending.queue_changes_for_item, item_key, title, summary)

            return {
                "item_key": item_key,
                "title": title,
                "ok": True,
                "reading_priority": summary.reading_priority,
                "relevance_score": summary.relevance_score,
                "composite_relevance_score": summary.composite_relevance_score,
                "queued_change_count": queued_change_count,
            }
        except Exception as exc:
            LOGGER.warning("Job %s failed item=%s", job_id, item_key, exc_info=True)
            return {"item_key": item_key, "title": title, "ok": False, "error": str(exc)}

    try:
        reader = get_zotero_reader_or_raise()
        cursor = 0
        while cursor < len(remaining_keys):
            if str(job.get("status") or "") == "cancelled":
                break

            batch = remaining_keys[cursor : cursor + effective_concurrency]
            cursor += len(batch)
            tasks = [asyncio.create_task(process_item(item_key)) for item_key in batch]
            for completed_task in asyncio.as_completed(tasks):
                outcome = await completed_task
                item_key = str(outcome.get("item_key") or "").strip()
                if not item_key or item_key in processed_keys:
                    continue
                if bool(outcome.get("cancelled")):
                    continue

                processed_keys.add(item_key)
                if bool(outcome.get("ok")):
                    job["results"].append(
                        {
                            "item_key": item_key,
                            "title": str(outcome.get("title") or f"Item {item_key}"),
                            "reading_priority": str(outcome.get("reading_priority") or ""),
                            "relevance_score": float(outcome.get("relevance_score") or 0),
                            "composite_relevance_score": float(outcome.get("composite_relevance_score") or 0),
                            "queued_change_count": int(outcome.get("queued_change_count") or 0),
                        }
                    )
                else:
                    job["errors"].append(
                        {
                            "item_key": item_key,
                            "error": str(outcome.get("error") or "Unknown error"),
                        }
                    )

                job["completed"] = min(len(normalized_item_keys), len(processed_keys))
                job["current_item_key"] = item_key
                job["current_title"] = str(outcome.get("title") or "")
                job["updated_at"] = now_iso()
                await asyncio.to_thread(triage_db.upsert_triage_job, _job_snapshot(job))

        if str(job.get("status") or "") == "running":
            job["status"] = "failed" if job.get("errors") else "completed"
        elif str(job.get("status") or "") == "cancelled":
            LOGGER.info(
                "Triage job %s cancelled by user after %s/%s items",
                job_id,
                int(job.get("completed") or 0),
                len(normalized_item_keys),
            )
    except Exception as exc:  # pragma: no cover - defensive guard for async background execution
        LOGGER.exception("Triage job %s crashed", job_id)
        job["status"] = "failed"
        job["errors"].append({"item_key": "job", "error": str(exc)})
    finally:
        job["current_item_key"] = ""
        job["current_title"] = ""
        job["updated_at"] = now_iso()
        await asyncio.to_thread(triage_db.upsert_triage_job, _job_snapshot(job))


async def run_triage_job(req: TriageRunRequest) -> TriageRunResponse:
    get_zotero_reader_or_raise()

    async with TRIAGE_START_LOCK:
        app_state = state()
        jobs: dict[str, dict[str, Any]] = getattr(app_state, "triage_jobs", {})
        running_in_memory = next((job for job in jobs.values() if str(job.get("status") or "") == "running"), None)
        if running_in_memory is not None:
            running_job_id = str(running_in_memory.get("job_id") or "")
            raise APIError(
                error="job_already_running",
                message=f"Triage job {running_job_id} is already running. Cancel it first before starting a new job.",
                status_code=409,
                details={"job_id": running_job_id},
            )

        running_persisted = await asyncio.to_thread(triage_db.list_triage_jobs, 1, ["running"])
        if running_persisted:
            running_job_id = str(running_persisted[0].get("job_id") or "")
            raise APIError(
                error="job_already_running",
                message=f"Triage job {running_job_id} is already running. Cancel it first before starting a new job.",
                status_code=409,
                details={"job_id": running_job_id},
            )

        job = TriageJobService.new_job(req.item_keys, req.queue_changes)
        job_id = str(job["job_id"])
        app_state.triage_jobs[job_id] = job
        trim_job_cache(app_state.triage_jobs)
        await asyncio.to_thread(triage_db.upsert_triage_job, _job_snapshot(job))
        asyncio.create_task(run_triage_job_worker(job_id, req.item_keys, req.queue_changes))
        return TriageRunResponse(job_id=job_id, status="running", total=len(req.item_keys))


start_triage_job = run_triage_job


async def list_triage_jobs(limit: int = 20) -> dict[str, Any]:
    safe_limit = max(1, min(limit, 100))
    persisted = await asyncio.to_thread(triage_db.list_triage_jobs, safe_limit)
    return {"items": [public_triage_job(job) for job in persisted]}


async def get_triage_job(job_id: str) -> dict[str, Any]:
    app_state = state()
    jobs: dict[str, dict[str, Any]] = getattr(app_state, "triage_jobs", {})
    job = jobs.get(job_id)
    if job:
        return public_triage_job(job)

    persisted = await asyncio.to_thread(triage_db.get_triage_job, job_id)
    if not persisted:
        raise APIError(error="not_found", message="Job not found", status_code=404)

    jobs[job_id] = persisted
    trim_job_cache(jobs)
    return public_triage_job(persisted)


async def get_latest_triage_feedback(item_keys: str = "") -> dict[str, Any]:
    normalized_keys = unique_non_empty_strings(item_keys.split(","))
    if not normalized_keys:
        return {"items": []}

    rows_by_item = await asyncio.to_thread(
        triage_db.get_latest_feedback_for_items,
        normalized_keys,
        list(EXPLICIT_FEEDBACK_SIGNALS),
    )

    items: list[dict[str, Any]] = []
    for item_key in normalized_keys:
        row = rows_by_item.get(item_key)
        if not row:
            continue
        signal = str(row.get("signal") or "")
        verdict = feedback_verdict_from_signal(signal)
        if verdict is None:
            continue
        items.append({"item_id": item_key, "verdict": verdict, "signal": signal, "created_at": row.get("created_at")})
    return {"items": items}


async def cancel_triage_job(job_id: str) -> dict[str, Any]:
    app_state = state()
    jobs: dict[str, dict[str, Any]] = getattr(app_state, "triage_jobs", {})
    job = jobs.get(job_id)
    if job is None:
        persisted = await asyncio.to_thread(triage_db.get_triage_job, job_id)
        if not persisted:
            raise APIError(error="not_found", message="Job not found", status_code=404)
        job = persisted
        jobs[job_id] = job
        trim_job_cache(jobs)

    current_status = str(job.get("status") or "")
    if current_status not in {"running", "interrupted"}:
        return {"job_id": job_id, "status": current_status, "cancelled": False, "already_done": True}

    job["status"] = "cancelled"
    job["updated_at"] = now_iso()
    await asyncio.to_thread(triage_db.upsert_triage_job, _job_snapshot(job))
    LOGGER.info(
        "Cancel requested for triage job %s at %s/%s",
        job_id,
        int(job.get("completed") or 0),
        int(job.get("total") or 0),
    )
    return {"job_id": job_id, "status": "cancelled", "cancelled": True, "already_done": False}
