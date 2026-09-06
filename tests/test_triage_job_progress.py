"""Live progress describes in-flight items, never the last completed paper."""
import asyncio
from threading import Event
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest

from tests.test_triage_job_cancellation import _wait_for
from tests.test_triage_job_controls import _new_job, _state
from zotero_summarizer.api.routes.triage import router
from zotero_summarizer.mcp.tools import triage as mcp_triage
from zotero_summarizer.models import SummarizeResponse
from zotero_summarizer.services.triage import triage_jobs as jobs
from zotero_summarizer.storage import repositories as db


@pytest.mark.parametrize("fail_b", [False, True])
def test_http_and_mcp_report_active_parallel_items_through_read_summary_and_completion(monkeypatch, tmp_path, fail_b):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "triage.db")
    db.init_db()
    job = _new_job("progress", ["A", "B", "C"])
    _state().triage_jobs = {"progress": job}
    entered_read = {key: Event() for key in "ABC"}
    entered_summary = {key: Event() for key in "ABC"}
    release_read = Event()
    release_summary = {key: Event() for key in "ABC"}

    def read(key):
        entered_read[key].set()
        assert release_read.wait(5)
        return {"title": f"Paper {key}", "pdf_path": "/stub.pdf"}

    def pipeline(req, *args, **kwargs):
        key = req.title[-1]
        entered_summary[key].set()
        assert release_summary[key].wait(5)
        if fail_b and key == "B":
            raise ValueError("forced B failure")
        return SummarizeResponse(executive_summary="ok", relevance_score=3, triage_rationale="ok")

    monkeypatch.setattr(jobs, "get_zotero_reader_or_raise", lambda: SimpleNamespace(get_item_detail=read))
    monkeypatch.setattr(jobs, "TRIAGE_JOB_CONCURRENCY", 2)
    monkeypatch.setattr(jobs.summarization, "run_pipeline", pipeline)
    app = FastAPI()
    app.include_router(router)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://stub") as client:
            async def api_request(method, path):
                response = await client.request(method, path)
                response.raise_for_status()
                return {"ok": True, "data": response.json()}

            monkeypatch.setattr(mcp_triage, "_api_request", api_request)
            worker = asyncio.create_task(jobs.run_triage_job_worker("progress", ["A", "B", "C"], False))
            try:
                await _wait_for(lambda: all(entered_read[key].is_set() for key in "AB"))
                first = (await client.get("/api/triage/jobs/progress")).json()
                assert first["active_items"] == [{"item_key": key, "title": f"Item {key}"} for key in "AB"]
                assert first["completed"] == 0 and not entered_read["C"].is_set()
                assert "current_item_key" not in first and "current_title" not in first
                jobs.trim_job_cache(_state().triage_jobs, keep=0)
                assert "progress" in _state().triage_jobs
                release_read.set()
                await _wait_for(lambda: all(entered_summary[key].is_set() for key in "AB"))
                expected = [{"item_key": key, "title": f"Paper {key}"} for key in "AB"]
                listed = (await client.get("/api/triage/jobs")).json()["items"][0]
                assert listed["active_items"] == expected
                mcp_status = await mcp_triage.get_job_status("progress")
                assert mcp_status["active_items"] == expected
                assert "current_item_key" not in mcp_status and "current_title" not in mcp_status
                assert first["active_items"][0]["title"] == "Item A"  # Detached snapshot.
                release_summary["A"].set()
                await _wait_for(lambda: job["completed"] == 1)
                partial = (await client.get("/api/triage/jobs/progress")).json()
                assert partial["active_items"] == [{"item_key": "B", "title": "Paper B"}]
                assert not entered_read["C"].is_set()
                release_summary["B"].set()
                await _wait_for(entered_summary["C"].is_set)
                assert (await jobs.get_triage_job("progress"))["active_items"] == [{"item_key": "C", "title": "Paper C"}]
            finally:
                release_read.set()
                for event in release_summary.values():
                    event.set()
                await worker
            final = (await client.get("/api/triage/jobs/progress")).json()
            assert final["active_items"] == [] and final["completed"] == 3
            assert final["status"] == ("failed" if fail_b else "completed")
            jobs.trim_job_cache(_state().triage_jobs, keep=0)
            assert _state().triage_jobs == {}
            assert (await jobs.get_triage_job("progress"))["active_items"] == []

    asyncio.run(run())


def test_legacy_persisted_progress_is_not_restored_as_live_work(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "triage.db")
    db.init_db()
    job = _new_job("legacy", ["A"], status="interrupted")
    job.update(current_item_key="A", current_title="Old completed title")
    db.upsert_triage_job(job)
    _state().triage_jobs = {}

    result = asyncio.run(jobs.get_triage_job("legacy"))

    assert result["active_items"] == []
    assert "current_item_key" not in result and "current_title" not in result
