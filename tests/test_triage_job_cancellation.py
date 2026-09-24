"""Terminal job states must mean their blocking work has actually stopped."""
import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_triage_job_controls import _new_job, _state
from zotero_summarizer.models import GoalsConfig, SummarizeRequest, SummarizeResponse, TriageResult, TriageRunRequest
from zotero_summarizer.services.triage import summarization
from zotero_summarizer.services.triage._execution import JobStopped, check_cancelled, run_blocking
from zotero_summarizer.services.triage import triage_jobs as jobs
from zotero_summarizer.storage import repositories


async def _wait_for(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.005)


@pytest.mark.parametrize("stage", ["read", "summary", "plan", "commit"])
@pytest.mark.parametrize("external", [False, True], ids=["request", "task"])
def test_cancel_waits_for_inflight_stage_and_stops_following_writes(monkeypatch, stage, external):
    entered, release = threading.Event(), threading.Event()
    calls = []
    snapshots = []

    def visit(name, result):
        calls.append(name)
        if name == stage:
            entered.set()
            assert release.wait(3)
        return result

    reader = SimpleNamespace(get_item_detail=lambda key: visit("read", {"title": key, "pdf_path": "/stub.pdf"}))
    summary = SummarizeResponse(executive_summary="ok", relevance_score=3, triage_rationale="ok")

    def pipeline(*args, cancel_event):
        visit("summary", None)
        check_cancelled(cancel_event)
        return summary

    monkeypatch.setattr(jobs, "get_zotero_reader_or_raise", lambda: reader)
    monkeypatch.setattr(jobs, "TRIAGE_JOB_CONCURRENCY", 1)
    monkeypatch.setattr(jobs.summarization, "run_pipeline", pipeline)
    monkeypatch.setattr(jobs.pending, "plan_changes_for_item", lambda *a: visit("plan", []))
    monkeypatch.setattr(jobs.triage_db, "insert_result", lambda *a, **k: visit("commit", 0))
    monkeypatch.setattr(jobs.triage_db, "upsert_triage_job", lambda job: snapshots.append(job))
    monkeypatch.setattr(jobs.triage_db, "list_triage_jobs", lambda *a: [])
    _state().triage_jobs = {"cancel": _new_job("cancel", ["A", "B"])}

    async def run():
        worker = asyncio.create_task(jobs.run_triage_job_worker("cancel", ["A", "B"], True))
        try:
            await _wait_for(entered.is_set)
            assert [item["item_key"] for item in (await jobs.get_triage_job("cancel"))["active_items"]] == ["A"]
            if external:
                worker.cancel()
                await asyncio.sleep(0.02)
            else:
                response = await jobs.cancel_triage_job("cancel")
                assert response["status"] == "cancelling" and not response["cancelled"]
            assert not worker.done()
            assert [item["item_key"] for item in (await jobs.get_triage_job("cancel"))["active_items"]] == ["A"]
            with pytest.raises(jobs.APIError) as caught:
                await jobs.run_triage_job(TriageRunRequest(item_keys=["C"]))
            assert caught.value.status_code == 409
        finally:
            release.set()
            if external:
                with pytest.raises(asyncio.CancelledError):
                    await worker
            else:
                await worker
        job = await jobs.get_triage_job("cancel")
        expected = "interrupted" if external else "cancelled"
        assert job["status"] == expected
        assert job["results"] == [] and job["errors"] == []
        assert job["active_items"] == []
        stages = ["read", "summary", "plan", "commit"]
        assert calls == stages[:stages.index(stage) + 1]
        assert snapshots[-1]["status"] == expected

    asyncio.run(run())


def test_timeout_retains_slot_until_thread_finishes_and_skips_later_items(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    active = threading.Event()
    calls = []

    def pipeline(req, *args, **kwargs):
        calls.append(req.title)
        active.set()
        entered.set()
        try:
            assert release.wait(3)
            check_cancelled(kwargs["cancel_event"])
            return SummarizeResponse(executive_summary="ok", relevance_score=3, triage_rationale="ok")
        finally:
            active.clear()

    reader = SimpleNamespace(get_item_detail=lambda key: {"title": key, "pdf_path": "/stub.pdf"})
    monkeypatch.setattr(jobs, "get_zotero_reader_or_raise", lambda: reader)
    monkeypatch.setattr(jobs, "TRIAGE_JOB_CONCURRENCY", 1)
    monkeypatch.setattr(jobs, "settings", lambda: SimpleNamespace(summary_timeout_seconds=0.01))
    monkeypatch.setattr(jobs.summarization, "run_pipeline", pipeline)
    insert = Mock()
    monkeypatch.setattr(jobs.triage_db, "insert_result", insert)
    monkeypatch.setattr(jobs.triage_db, "upsert_triage_job", lambda job: None)
    monkeypatch.setattr(jobs.triage_db, "list_triage_jobs", lambda *a: [])
    _state().triage_jobs = {"timeout": _new_job("timeout", ["A", "B"])}

    async def run():
        worker = asyncio.create_task(jobs.run_triage_job_worker("timeout", ["A", "B"], False))
        try:
            await _wait_for(entered.is_set)
            await asyncio.sleep(0.05)
            assert active.is_set() and not worker.done()
            assert (await jobs.get_triage_job("timeout"))["active_items"] == [{"item_key": "A", "title": "A"}]
            with pytest.raises(jobs.APIError):
                await jobs.run_triage_job(TriageRunRequest(item_keys=["C"]))
        finally:
            release.set()
            await worker
        assert not active.is_set() and calls == ["A"]
        job = await jobs.get_triage_job("timeout")
        assert job["status"] == "failed" and len(job["errors"]) == 1
        assert job["errors"][0]["error"]
        assert job["active_items"] == []
        insert.assert_not_called()

    asyncio.run(run())


def test_startup_finalizes_cancellation_instead_of_resuming_it(monkeypatch, tmp_path):
    monkeypatch.setattr(repositories, "DB_PATH", tmp_path / "triage.db")
    repositories.init_db()
    repositories.upsert_triage_job(_new_job("cancel", ["A"], status="cancelling"))
    assert repositories.mark_running_triage_jobs_interrupted() == 0
    assert repositories.get_triage_job("cancel")["status"] == "cancelled"


@pytest.mark.parametrize("text", ["paper", "paper " * 14000], ids=["direct", "chunked"])
@pytest.mark.parametrize("stop_requested", [False, True], ids=["complete", "stop"])
def test_pipeline_stop_after_first_llm_call_prevents_more_calls(monkeypatch, text, stop_requested):
    stop = threading.Event()

    def prompt(_prompt):
        if stop_requested:
            stop.set()
            return "malformed JSON must not trigger another LLM call"
        return '{"executive_summary": "grounded summary"}'

    llm = Mock(prompt=Mock(side_effect=prompt))
    llm.pydantic_prompt.return_value = TriageResult(score=3, rationale="evidence")
    config = GoalsConfig(relevance_scale={i: str(i) for i in range(1, 6)}, llm={
        "draft_model": "stub", "refine_model": "stub", "api_base": "http://stub", "api_key_env": "STUB_KEY",
    })
    app = SimpleNamespace(app_state=SimpleNamespace(config=config), resolve_stage_client=lambda stage: llm)
    monkeypatch.setattr(summarization, "state", lambda: app)
    monkeypatch.setattr(summarization, "_extract_pdf_text", lambda path: text)
    monkeypatch.setattr(summarization.corpus, "run_corpus_match", lambda *a: {})

    request = SummarizeRequest(title="paper", pdf_path="/stub.pdf")
    if stop_requested:
        with pytest.raises(JobStopped):
            summarization.run_pipeline(request, cancel_event=stop)
        llm.prompt.assert_called_once()
        llm.pydantic_prompt.assert_not_called()
    else:
        result = summarization.run_pipeline(request)
        assert result.executive_summary == "grounded summary" and result.relevance_score == 3
        assert llm.prompt.call_count == (3 if len(text) > 80000 else 1)
        llm.pydantic_prompt.assert_called_once()


def test_repeated_task_cancellation_cannot_orphan_a_thread():
    entered, release, stop = threading.Event(), threading.Event(), threading.Event()

    def blocking():
        entered.set()
        assert release.wait(3)

    async def run():
        task = asyncio.create_task(run_blocking(blocking, stop=stop))
        try:
            await _wait_for(entered.is_set)
            task.cancel()
            await _wait_for(stop.is_set)
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())
