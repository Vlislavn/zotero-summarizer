from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Event
import uuid
from typing import Any

from zotero_summarizer.api.errors import APIError, ExtractionError
from zotero_summarizer.domain import EXPLICIT_FEEDBACK_SIGNALS, feedback_verdict_from_signal
from zotero_summarizer.models import SummarizeRequest, TriageRunRequest, TriageRunResponse
from zotero_summarizer.services.zotero import pending
from zotero_summarizer.services.triage import summarization
from zotero_summarizer.services.triage._execution import JobStopped, check_cancelled, run_blocking
from zotero_summarizer.services._common import (
    LOGGER, build_log_prefix, effective_llm_concurrency, now_iso, settings, state,
    unique_non_empty_strings,
)
from zotero_summarizer.services.zotero.zotero import get_zotero_reader_or_raise
from zotero_summarizer.storage import repositories as triage_db


TRIAGE_START_LOCK = asyncio.Lock()
TRIAGE_JOB_CONCURRENCY: int | None = None
_ACTIVE: dict[str, tuple[asyncio.Task, Event]] = {}


async def _persist_job(job: dict[str, Any]) -> None:
    async with TRIAGE_START_LOCK:
        await run_blocking(triage_db.upsert_triage_job, _job_snapshot(job))


def new_job(item_keys: list[str], queue_changes: bool = True) -> dict[str, Any]:
    """A fresh in-memory triage-job record (state-independent lifecycle data)."""
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
        "active_items": {},
        "queue_changes": bool(queue_changes),
        "item_keys": normalized,
        "results": [],
        "errors": [],
    }


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
    ``upsert_triage_job`` runs in a worker thread and
    serialises those lists. Handing it a snapshot guarantees the thread never
    iterates a list the event loop could mutate at an await boundary.
    """
    snap = dict(job)
    snap.pop("active_items", None)  # Live work is not resumable progress.
    snap["results"] = list(job.get("results") or [])
    snap["errors"] = list(job.get("errors") or [])
    snap["item_keys"] = list(job.get("item_keys") or [])
    return snap


def trim_job_cache(jobs: dict[str, dict[str, Any]], keep: int = 20) -> None:
    if len(jobs) <= keep:
        return
    ordered = sorted(jobs.values(), key=lambda row: str(row.get("started_at", "")), reverse=True)
    keep_ids = {str(job.get("job_id")) for job in ordered[:keep]} | _ACTIVE.keys()
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
        "active_items": [{"item_key": key, "title": title} for key, title in job.get("active_items", {}).items()],
        "results": list(job.get("results") or []),
        "errors": list(job.get("errors") or []),
    }


@dataclass
class _TriageJobCtx:
    reader: Any
    job_id: str
    item_positions: dict[str, int]
    total: int
    queue_changes: bool
    active_items: dict[str, str]
    stop: Event = field(default_factory=Event)


async def _summarize_job_item(item_key: str, ctx: _TriageJobCtx) -> dict[str, Any]:
    """Return an explicit success/error/stop outcome; task cancellation propagates."""
    if ctx.stop.is_set():
        return {"item_key": item_key, "cancelled": True}
    if ctx.reader is None:
        raise APIError(error="zotero_unavailable", message="Zotero reader is unavailable", status_code=503)

    title = f"Item {item_key}"
    ctx.active_items[item_key] = title
    try:
        detail = await run_blocking(ctx.reader.get_item_detail, item_key, stop=ctx.stop)
        check_cancelled(ctx.stop)
        if not detail:
            raise APIError(error="not_found", message=f"Item {item_key} not found", status_code=404)

        title = str(detail.get("title") or title)
        ctx.active_items[item_key] = title
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
            request, item_id=item_key, batch_id=ctx.job_id,
            index=ctx.item_positions.get(item_key), total=ctx.total,
        )
        summary = await run_blocking(
            summarization.run_pipeline, request, prefix, cancel_event=ctx.stop,
            stop=ctx.stop, timeout=settings().summary_timeout_seconds,
        )
        check_cancelled(ctx.stop)
        changes = []
        if ctx.queue_changes:
            changes = await run_blocking(pending.plan_changes_for_item, item_key, title, summary, stop=ctx.stop)
        queued_change_count = await run_blocking(
            triage_db.insert_result, item_key, title, summary.model_dump(),
            pdf_path=pdf_path, pending_changes=changes, stop=ctx.stop,
        )
        check_cancelled(ctx.stop)

        return {
            "item_key": item_key,
            "title": title,
            "ok": True,
            "reading_priority": summary.reading_priority,
            "relevance_score": summary.relevance_score,
            "composite_relevance_score": summary.composite_relevance_score,
            "queued_change_count": queued_change_count,
        }
    except JobStopped:
        return {"item_key": item_key, "cancelled": True}
    except Exception as exc:
        LOGGER.warning("Job %s failed item=%s", ctx.job_id, item_key, exc_info=True)
        return {"item_key": item_key, "title": title, "ok": False, "error": str(exc) or type(exc).__name__}
    finally:
        del ctx.active_items[item_key]


async def run_triage_job_worker(job_id: str, item_keys: list[str], queue_changes: bool) -> None:
    stop = Event()
    _ACTIVE[job_id] = (asyncio.current_task(), stop)
    try:
        await _run_job(job_id, item_keys, queue_changes, stop)
    finally:
        _ACTIVE.pop(job_id, None)


async def _run_job(job_id: str, item_keys: list[str], queue_changes: bool, stop: Event) -> None:
    app_state = state()
    jobs: dict[str, dict[str, Any]] = app_state.triage_jobs
    job = jobs.get(job_id)
    if job is None:
        return
    if job["status"] in {"cancelled", "cancelling"}:
        job["status"] = "cancelled"
        await _persist_job(job)
        return

    normalized_item_keys = unique_non_empty_strings(item_keys)
    item_positions = {item_key: idx + 1 for idx, item_key in enumerate(normalized_item_keys)}

    processed_keys = {row["item_key"] for row in job["results"]}

    remaining_keys = [item_key for item_key in normalized_item_keys if item_key not in processed_keys]
    effective_concurrency = _effective_concurrency(len(remaining_keys))

    job["item_keys"] = list(normalized_item_keys)
    job["queue_changes"] = bool(queue_changes)
    job["total"] = len(normalized_item_keys)
    job["completed"] = min(len(normalized_item_keys), len(processed_keys))
    job["status"] = "running"
    job["errors"] = []  # Errors describe this attempt; only successes survive resume.
    job["active_items"] = {}
    job["updated_at"] = now_iso()
    try:
        await _persist_job(job)
        reader = get_zotero_reader_or_raise()
        ctx = _TriageJobCtx(
            reader=reader, job_id=job_id, item_positions=item_positions,
            total=len(normalized_item_keys), queue_changes=queue_changes, stop=stop,
            active_items=job["active_items"],
        )
        cursor = 0
        while cursor < len(remaining_keys):
            if stop.is_set():
                break

            batch = remaining_keys[cursor : cursor + effective_concurrency]
            cursor += len(batch)
            async with asyncio.TaskGroup() as group:
                tasks = [group.create_task(_summarize_job_item(item_key, ctx)) for item_key in batch]
                for completed_task in asyncio.as_completed(tasks):
                    outcome = await completed_task
                    if job["status"] != "cancelling":
                        _record_outcome(job, outcome, processed_keys)
                    await _persist_job(job)

        if str(job.get("status") or "") == "running":
            job["status"] = "failed" if job.get("errors") else "completed"
        elif str(job.get("status") or "") == "cancelling":
            job["status"] = "cancelled"
            LOGGER.info(
                "Triage job %s cancelled by user after %s/%s items",
                job_id,
                int(job.get("completed") or 0),
                len(normalized_item_keys),
            )
    except asyncio.CancelledError:
        stop.set()
        job["status"] = "cancelled" if job["status"] == "cancelling" else "interrupted"
        raise
    except Exception as exc:  # background job failure is exposed through persisted status
        LOGGER.exception("Triage job %s crashed", job_id)
        job["status"] = "failed"
        job["errors"].append({"item_key": "job", "error": str(exc)})
    finally:
        job["updated_at"] = now_iso()
        await _persist_job(job)


def _record_outcome(job: dict[str, Any], outcome: dict[str, Any], processed_keys: set[str]) -> None:
    item_key = outcome["item_key"]
    if outcome.get("cancelled") or item_key in processed_keys:
        return
    processed_keys.add(item_key)
    if outcome["ok"]:
        job["results"].append({key: value for key, value in outcome.items() if key != "ok"})
    else:
        job["errors"].append({"item_key": item_key, "error": outcome["error"]})
    job["completed"] = min(job["total"], len(processed_keys))
    job["updated_at"] = now_iso()


async def run_triage_job(req: TriageRunRequest) -> TriageRunResponse:
    get_zotero_reader_or_raise()

    async with TRIAGE_START_LOCK:
        app_state = state()
        jobs: dict[str, dict[str, Any]] = getattr(app_state, "triage_jobs", {})
        running_in_memory = next((job for job in jobs.values() if job["status"] in {"running", "cancelling"}), None)
        if running_in_memory is not None:
            running_job_id = str(running_in_memory.get("job_id") or "")
            raise APIError(
                error="job_already_running",
                message=f"Triage job {running_job_id} is already running. Cancel it first before starting a new job.",
                status_code=409,
                details={"job_id": running_job_id},
            )

        running_persisted = await run_blocking(triage_db.list_triage_jobs, 1, ["running", "cancelling"])
        if running_persisted:
            running_job_id = str(running_persisted[0].get("job_id") or "")
            raise APIError(
                error="job_already_running",
                message=f"Triage job {running_job_id} is already running. Cancel it first before starting a new job.",
                status_code=409,
                details={"job_id": running_job_id},
            )

        job = new_job(req.item_keys, req.queue_changes)
        job_id = str(job["job_id"])
        app_state.triage_jobs[job_id] = job
        trim_job_cache(app_state.triage_jobs)
        await run_blocking(triage_db.upsert_triage_job, _job_snapshot(job))
        asyncio.create_task(run_triage_job_worker(job_id, req.item_keys, req.queue_changes))
        return TriageRunResponse(job_id=job_id, status="running", total=len(req.item_keys))


async def list_triage_jobs(limit: int = 20) -> dict[str, Any]:
    safe_limit = max(1, min(limit, 100))
    persisted = await asyncio.to_thread(triage_db.list_triage_jobs, safe_limit)
    live_jobs = state().triage_jobs
    return {"items": [public_triage_job(live_jobs.get(job["job_id"], job)) for job in persisted]}


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
    async with TRIAGE_START_LOCK:
        jobs = state().triage_jobs
        job = jobs.get(job_id)
        if job is None:
            job = await run_blocking(triage_db.get_triage_job, job_id)
            if job is None:
                raise APIError(error="not_found", message="Job not found", status_code=404)
            jobs[job_id] = job
            trim_job_cache(jobs)

        current_status = job["status"]
        if current_status not in {"running", "interrupted", "cancelling"}:
            return {"job_id": job_id, "status": current_status, "cancelled": False, "already_done": True}

        active = _ACTIVE.get(job_id)
        if active is not None and not active[0].done():
            active[1].set()
            job["status"] = "cancelling"
        else:
            job["status"] = "cancelled"
        job["updated_at"] = now_iso()
        await run_blocking(triage_db.upsert_triage_job, _job_snapshot(job))
        LOGGER.info("Cancel requested for triage job %s at %s/%s", job_id, job["completed"], job["total"])
        return {"job_id": job_id, "status": job["status"], "cancelled": job["status"] == "cancelled", "already_done": False}
