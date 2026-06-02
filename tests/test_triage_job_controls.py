from __future__ import annotations

import asyncio
import threading
import time

import pytest

from zotero_summarizer.runtime import get_context
from zotero_summarizer.services.triage import triage_jobs as app_module
from zotero_summarizer.models import SummarizeResponse, TriageRunRequest


def _state():
    return get_context().state


def _new_job(job_id: str, item_keys: list[str], status: str = "running") -> dict:
    return {
        "job_id": job_id,
        "status": status,
        "started_at": "2026-04-02T18:00:00+00:00",
        "updated_at": "2026-04-02T18:00:00+00:00",
        "total": len(item_keys),
        "completed": 0,
        "current_item_key": "",
        "current_title": "",
        "queue_changes": False,
        "item_keys": list(item_keys),
        "results": [],
        "errors": [],
    }


def test_run_triage_job_rejects_parallel_jobs(monkeypatch):
    _state().zotero_reader = object()
    _state().zotero_error = ""
    _state().triage_jobs = {
        "job_running": _new_job("job_running", ["A1"], status="running"),
    }

    async def _run() -> None:
        with pytest.raises(app_module.APIError) as exc_info:
            await app_module.run_triage_job(TriageRunRequest(item_keys=["B1"], queue_changes=True))
        assert exc_info.value.status_code == 409
        assert exc_info.value.error == "job_already_running"

    asyncio.run(_run())


def test_run_triage_job_single_flight_under_concurrent_requests(monkeypatch):
    _state().zotero_reader = object()
    _state().zotero_error = ""
    _state().triage_jobs = {}

    async def fake_background_job(_job_id: str, _item_keys: list[str], _queue_changes: bool) -> None:
        return None

    monkeypatch.setattr(app_module.triage_db, "list_triage_jobs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(app_module.triage_db, "upsert_triage_job", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app_module, "run_triage_job_worker", fake_background_job)

    async def _invoke_once() -> tuple[str, str]:
        try:
            result = await app_module.run_triage_job(TriageRunRequest(item_keys=["A1"], queue_changes=False))
            return ("ok", result.job_id)
        except app_module.APIError as exc:
            return ("error", exc.error)

    async def _run() -> None:
        outcomes = await asyncio.gather(_invoke_once(), _invoke_once())
        ok = [outcome for outcome in outcomes if outcome[0] == "ok"]
        errors = [outcome for outcome in outcomes if outcome[0] == "error"]

        assert len(ok) == 1
        assert len(errors) == 1
        assert errors[0][1] == "job_already_running"

    asyncio.run(_run())


def test_cancel_triage_job_sets_cancelled_status(monkeypatch):
    _state().triage_jobs = {
        "job_cancel": _new_job("job_cancel", ["A1", "A2"], status="running"),
    }
    monkeypatch.setattr(app_module.triage_db, "upsert_triage_job", lambda job: None)

    async def _run() -> None:
        response = await app_module.cancel_triage_job("job_cancel")
        assert response["cancelled"] is True
        assert response["status"] == "cancelled"
        assert _state().triage_jobs["job_cancel"]["status"] == "cancelled"

        second = await app_module.cancel_triage_job("job_cancel")
        assert second["already_done"] is True
        assert second["cancelled"] is False

    asyncio.run(_run())


def test_triage_job_runs_items_in_parallel(monkeypatch):
    job_id = "job_parallel"
    item_keys = ["K1", "K2", "K3", "K4"]
    _state().triage_jobs = {job_id: _new_job(job_id, item_keys, status="running")}

    class FakeReader:
        @staticmethod
        def get_item_detail(item_key: str) -> dict:
            return {
                "title": f"Paper {item_key}",
                "pdf_path": "/tmp/fake.pdf",
                "doi": "",
                "abstract": "Abstract",
            }

    tracker_lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0

    def fake_pipeline(_req, _prefix):
        nonlocal in_flight, max_in_flight
        with tracker_lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        time.sleep(0.12)
        with tracker_lock:
            in_flight -= 1
        return SummarizeResponse(
            executive_summary="ok",
            relevance_score=3,
            composite_relevance_score=3.0,
            reading_priority="could_read",
            triage_rationale="ok",
        )

    monkeypatch.setattr(app_module, "TRIAGE_JOB_CONCURRENCY", 3)
    monkeypatch.setattr(app_module, "get_zotero_reader_or_raise", lambda: FakeReader())
    monkeypatch.setattr(app_module.summarization, "run_pipeline", fake_pipeline)
    monkeypatch.setattr(app_module.triage_db, "upsert_triage_job", lambda job: None)
    monkeypatch.setattr(app_module.triage_db, "insert_result", lambda *args, **kwargs: None)

    async def _run() -> None:
        await app_module.run_triage_job_worker(job_id, item_keys, queue_changes=False)

    asyncio.run(_run())

    job = _state().triage_jobs[job_id]
    assert max_in_flight >= 2
    assert job["status"] == "completed"
    assert job["completed"] == 4
    assert len(job["results"]) == 4
    assert len(job["errors"]) == 0


def test_triage_job_resume_skips_processed_keys(monkeypatch):
    job_id = "job_resume"
    item_keys = ["K1", "K2", "K3", "K4"]
    job = _new_job(job_id, item_keys, status="interrupted")
    job["results"] = [
        {
            "item_key": "K3",
            "title": "Already done",
            "reading_priority": "could_read",
            "relevance_score": 3,
            "composite_relevance_score": 3.0,
            "queued_change_count": 0,
        }
    ]
    job["completed"] = 1
    _state().triage_jobs = {job_id: job}

    class FakeReader:
        @staticmethod
        def get_item_detail(item_key: str) -> dict:
            return {
                "title": f"Paper {item_key}",
                "pdf_path": "/tmp/fake.pdf",
                "doi": "",
                "abstract": "Abstract",
            }

    def fake_pipeline(_req, _prefix):
        return SummarizeResponse(
            executive_summary="ok",
            relevance_score=3,
            composite_relevance_score=3.0,
            reading_priority="could_read",
            triage_rationale="ok",
        )

    monkeypatch.setattr(app_module, "TRIAGE_JOB_CONCURRENCY", 4)
    monkeypatch.setattr(app_module, "get_zotero_reader_or_raise", lambda: FakeReader())
    monkeypatch.setattr(app_module.summarization, "run_pipeline", fake_pipeline)
    monkeypatch.setattr(app_module.triage_db, "upsert_triage_job", lambda job: None)
    monkeypatch.setattr(app_module.triage_db, "insert_result", lambda *args, **kwargs: None)

    async def _run() -> None:
        await app_module.run_triage_job_worker(job_id, item_keys, queue_changes=False)

    asyncio.run(_run())

    final_job = _state().triage_jobs[job_id]
    result_keys = [str(row.get("item_key") or "") for row in final_job["results"]]
    assert result_keys.count("K3") == 1
    assert set(result_keys) == {"K1", "K2", "K3", "K4"}
    assert final_job["completed"] == 4


def test_triage_job_marks_failed_when_any_item_errors(monkeypatch):
    job_id = "job_partial_error"
    item_keys = ["K1", "K2"]
    _state().triage_jobs = {job_id: _new_job(job_id, item_keys, status="running")}

    class FakeReader:
        @staticmethod
        def get_item_detail(item_key: str) -> dict:
            if item_key == "K2":
                return {
                    "title": "Paper K2",
                    "pdf_path": "",
                    "doi": "",
                    "abstract": "Abstract",
                }
            return {
                "title": f"Paper {item_key}",
                "pdf_path": "/tmp/fake.pdf",
                "doi": "",
                "abstract": "Abstract",
            }

    def fake_pipeline(_req, _prefix):
        return SummarizeResponse(
            executive_summary="ok",
            relevance_score=3,
            composite_relevance_score=3.0,
            reading_priority="could_read",
            triage_rationale="ok",
        )

    monkeypatch.setattr(app_module, "TRIAGE_JOB_CONCURRENCY", 2)
    monkeypatch.setattr(app_module, "get_zotero_reader_or_raise", lambda: FakeReader())
    monkeypatch.setattr(app_module.summarization, "run_pipeline", fake_pipeline)
    monkeypatch.setattr(app_module.triage_db, "upsert_triage_job", lambda job: None)
    monkeypatch.setattr(app_module.triage_db, "insert_result", lambda *args, **kwargs: None)

    async def _run() -> None:
        await app_module.run_triage_job_worker(job_id, item_keys, queue_changes=False)

    asyncio.run(_run())

    job = _state().triage_jobs[job_id]
    assert job["completed"] == 2
    assert len(job["results"]) == 1
    assert len(job["errors"]) == 1
    assert job["status"] == "failed"


def test_triage_job_marks_failed_when_reader_unavailable(monkeypatch):
    job_id = "job_reader_unavailable"
    item_keys = ["K1"]
    # Pin concurrency (like the sibling tests) so _effective_concurrency doesn't
    # resolve the feed-stage provider via app_state.config — this test stubs the
    # reader, not the full RuntimeState, so app_state is None here.
    monkeypatch.setattr(app_module, "TRIAGE_JOB_CONCURRENCY", 1)
    _state().triage_jobs = {job_id: _new_job(job_id, item_keys, status="running")}

    def fail_reader():
        raise app_module.APIError(
            error="zotero_unavailable",
            message="reader unavailable",
            status_code=503,
        )

    monkeypatch.setattr(app_module, "get_zotero_reader_or_raise", fail_reader)
    monkeypatch.setattr(app_module.triage_db, "upsert_triage_job", lambda *_args, **_kwargs: None)

    async def _run() -> None:
        await app_module.run_triage_job_worker(job_id, item_keys, queue_changes=False)

    asyncio.run(_run())

    job = _state().triage_jobs[job_id]
    assert job["status"] == "failed"
    assert job["current_item_key"] == ""
    assert job["current_title"] == ""
    assert len(job["errors"]) == 1
    assert job["errors"][0]["item_key"] == "job"
