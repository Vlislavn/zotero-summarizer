"""Executable MCP HTTP/handler contracts for audit boundary regressions."""
from __future__ import annotations

import asyncio
import importlib

import httpx
import pytest

from zotero_summarizer.mcp import api_client, config
from zotero_summarizer.mcp.tools import search, status


def test_find_similar_papers_uses_hybrid_search_and_excludes_seed(monkeypatch):
    calls = []

    async def api_request(method, path, *, params=None, payload=None):
        calls.append((method, path, params))
        if path == "/api/zotero/items/SEED0001":
            return {"ok": True, "data": {"title": "A very useful biomedical paper title"}}
        return {"ok": True, "data": {"items": [
            {"item_key": "SEED0001", "title": "Seed"},
            {"item_key": "SIMILAR1", "title": "Related paper"},
        ], "semantic": True, "semantic_unavailable": False, "total_unread": 2}}

    monkeypatch.setattr(search, "_api_request", api_request)
    result = asyncio.run(search.find_similar_papers(item_key="SEED0001", limit=1))
    assert result["ok"] is True
    assert [row["item_key"] for row in result["items"]] == ["SIMILAR1"]
    assert calls[1][1] == "/api/library/reading-queue"
    assert calls[1][2]["semantic"] is True
    assert calls[1][2]["include_read"] is False


def test_get_paper_scopes_pending_rows_by_item_key(monkeypatch):
    from zotero_summarizer.mcp.tools import search as tool

    calls = []

    async def api_request(method, path, *, params=None, payload=None):
        calls.append((path, params))
        if path.endswith("/items/TARGET01"):
            return {"ok": True, "data": {"title": "Target"}}
        if path == "/api/results/TARGET01":
            return {"ok": True, "data": {}}
        if path == "/api/pending":
            return {"ok": True, "data": {"items": [{"item_key": "TARGET01", "id": 7}]}}
        return {"ok": True, "data": {"items": []}}

    monkeypatch.setattr(tool, "_api_request", api_request)
    monkeypatch.setattr(api_client, "_api_request", api_request)
    result = asyncio.run(tool.get_paper("TARGET01"))
    assert result["ok"] is True
    pending = next(params for path, params in calls if path == "/api/pending")
    assert pending["item_key"] == "TARGET01" and pending["limit"] == 5000


def test_status_tool_reports_total_backend_failure(monkeypatch):
    async def failed_snapshot():
        return {"generated_at": "now", "api_base_url": "local", "warnings": [{"code": "backend_unreachable"}]}

    monkeypatch.setattr(status, "_collect_status_snapshot", failed_snapshot)
    result = asyncio.run(status.get_library_status())
    assert result["ok"] is False
    assert result["error"]["code"] == "backend_unavailable"


def test_status_snapshot_requests_active_jobs_without_recent_window(monkeypatch):
    calls = []

    async def api_request(method, path, *, params=None, payload=None):
        calls.append((path, params))
        if path == "/api/triage/jobs":
            return {"ok": True, "data": {"items": [{"job_id": "old", "status": "running"}]}}
        return {"ok": True, "data": {}}

    monkeypatch.setattr(api_client, "_api_request", api_request)
    snapshot = asyncio.run(api_client._collect_status_snapshot())
    assert snapshot["active_job"]["job_id"] == "old"
    assert dict(calls)["/api/triage/jobs"] == {"active_only": True}


def test_active_job_api_queries_status_before_limit(monkeypatch):
    from types import SimpleNamespace
    from zotero_summarizer.services.triage import triage_jobs

    query = []
    def db_list(limit, statuses):
        query.extend([limit, statuses])
        return [{"job_id": "older-active", "status": "running"}]

    monkeypatch.setattr(triage_jobs.triage_db, "list_triage_jobs", db_list)
    monkeypatch.setattr(triage_jobs, "state", lambda: SimpleNamespace(triage_jobs={}))
    result = asyncio.run(triage_jobs.list_triage_jobs(active_only=True))
    assert query == [20, ["running", "cancelling"]]
    assert result["items"][0]["job_id"] == "older-active"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ZOTERO_MCP_TIMEOUT_SECONDS", "nan"),
        ("ZOTERO_MCP_TIMEOUT_SECONDS", "-1"),
        ("ZOTERO_MCP_MAX_TRIAGE_ITEMS", "501"),
        ("ZOTERO_MCP_SECONDS_PER_ITEM", "0"),
    ],
)
def test_mcp_numeric_environment_out_of_domain_fails_import(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        importlib.reload(config)
    monkeypatch.delenv(name)
    importlib.reload(config)


def test_api_request_normalizes_backend_error_with_retry_metadata(monkeypatch):
    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, *, params=None, json=None):
            request_seen.append((method, url, params, json))
            return httpx.Response(
                503, json={"error": "zotero_db_locked", "message": "busy"},
                headers={"Retry-After": "12"},
            )

    request_seen = []
    monkeypatch.setattr(api_client.httpx, "AsyncClient", Client)
    result = asyncio.run(api_client._api_request("POST", "/api/write", payload={"value": 1}))
    assert result["ok"] is False and result["error"]["retryable"] is True
    assert result["error"]["retry_after_sec"] == 12
    assert request_seen[0][3] == {"value": 1}
